"""Question wording, required evidence and refusal boundaries of the corpus."""

import json
from pathlib import Path

import pytest

from app.core.models import ChatResponse, Product
from app.infrastructure import knowledge_review as review, rag
from app.intent.rules import rule_based_classify_intent
from app.services import hybrid
from app.services.rag_grounding import answer_matches_reviewed_sources, select_reviewed_entries
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources


COVERAGE = json.loads((Path(__file__).resolve().parents[1] / "evaluation/rag_question_coverage.json").read_text())
POSITIVE = [(group, question) for group in COVERAGE["groups"] for question in group["questions"]]


def base(intent="general_nutrition_guide"):
    return ChatResponse(intent=intent, answer="자료를 확인해야 합니다.", risk_level="unknown")


@pytest.mark.parametrize("group,question", POSITIVE, ids=[question for _, question in POSITIVE])
def test_question_wording_gets_relevant_complete_evidence(official_entries, group, question):
    intent = rule_based_classify_intent(question, None, None, [])
    assert intent in {"general_nutrition_guide", "general_diet_guide", "general_allergy_guide"}
    result = hybrid.try_general_llm_fallback(base(intent), question, None, None)
    assert result.answer_source == "rag"
    documents = {entry["source"].split(":")[-1] for entry in select_reviewed_entries(question, official_entries)}
    expected = group.get("expected_documents_by_question", {}).get(question, [group["document_id"]])
    assert documents == set(expected)
    assert all(term in result.answer for term in group["must_contain_all"])
    assert answer_matches_reviewed_sources(result.answer, result.sources)
    validated = hybrid.validate_final_response_grounding(result, base(intent), None, result.grounding_context)
    assert validated.answer_source == "rag" and validated.answer == result.answer


@pytest.mark.parametrize("case", COVERAGE["multi_topic"], ids=[case["question"] for case in COVERAGE["multi_topic"]])
def test_multiple_topics_need_every_source_and_never_return_partial_answer(official_entries, monkeypatch, case):
    question = case["question"]
    intent = rule_based_classify_intent(question, None, None, [])
    assert intent in {"general_nutrition_guide", "general_diet_guide", "general_allergy_guide"}
    expected = set(case["expected_documents"])
    selected = select_reviewed_entries(question, official_entries)
    assert {entry["source"].split(":")[-1] for entry in selected} == expected
    result = hybrid.try_general_llm_fallback(base(intent), question, None, None)
    assert result.answer_source == "rag" and len(result.sources) == len(expected)
    assert answer_matches_reviewed_sources(result.answer, result.sources)
    for missing in expected:
        partial = [entry for entry in official_entries if entry["source"].split(":")[-1] != missing]
        monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: partial)
        result = hybrid.try_general_llm_fallback(base(intent), question, None, None)
        assert result.answer_source == "rules" and result.sources == []


@pytest.mark.parametrize("question", COVERAGE["unsupported_questions"])
def test_unreviewed_details_need_no_embedding_or_new_factual_answer(official_entries, monkeypatch, question):
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: pytest.fail("Unsupported details must not be embedded"))
    result = hybrid.try_general_llm_fallback(base(), question, None, None)
    assert result.answer_source == "rules" and result.sources == []


def test_each_released_topic_has_question_cases_and_unmodified_provenance(packaged_sources):
    entries = {
        review.parse_released_excerpt(path.read_text()).document_id
        for path in packaged_sources.glob("*.source.json")
    }
    assert entries == {group["document_id"] for group in COVERAGE["groups"]}


@pytest.mark.parametrize("question", [
    "이거 락토프리인데 먹어도 돼?", "이 제품 비건 맞아?", "이 과자 글루텐이 들어 있어?",
    "이 제품 단백질이 몇 g이야?", "이거 포화지방이 높아?",
])
def test_specific_product_requests_are_not_replaced_with_general_explanations(question, official_entries):
    product = Product(product_name="가상 식품", rawmtrl_nm="원재료 미확인")
    result = hybrid.try_general_llm_fallback(base(), question, None, product)
    assert result.answer_source == "rules" and result.sources == []
    assert rule_based_classify_intent(question, product, None, []) not in {
        "general_nutrition_guide", "general_allergy_guide", "general_diet_guide",
    }
