"""Generated RAG must reject invented, partial or contradicted claim evidence."""

import json

import pytest

from app.core.models import ChatResponse
from app.infrastructure import knowledge_review as review, rag
from app.services import hybrid
from app.services.rag_grounding import reviewed_generated_entries
from app.services.rag_grounding import answer_sentences
from rag_test_helpers import grounding_review
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources


QUESTION = "무설탕과 무가당의 차이가 뭐야?"
PARAPHRASE = "무당과 무가당은 서로 다른 표시예요."


def entry_for(entries, document_id="sugar-zero-vs-added"):
    return next(e for e in entries if e["source"].endswith(":" + document_id))


def generated(monkeypatch, entry, answer, report):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: [entry])
    outputs = iter([answer, report])
    calls = []

    def provider(prompt):
        calls.append(prompt)
        return next(outputs)

    monkeypatch.setattr(hybrid, "generate_text", provider)
    base = ChatResponse(intent="general_nutrition_guide", answer="기본 규칙 안내", risk_level="unknown")
    response = hybrid.try_general_llm_fallback(base, QUESTION, None, None)
    return response, calls


def test_grounded_paraphrase_is_generated_reviewed_and_cited(official_entries, monkeypatch):
    entry = entry_for(official_entries)
    response, calls = generated(monkeypatch, entry, PARAPHRASE,
                                grounding_review(PARAPHRASE, [entry["content"]]))
    assert len(calls) == 2
    assert response.answer == PARAPHRASE
    assert response.answer_source == "rag"
    assert response.fallback_reason == "rag_generated_answer_verified"
    assert response.sources == [rag.official_citation_from_entry(entry)]
    assert PARAPHRASE in calls[1] and entry["content"] in calls[1]


def test_complete_review_accepts_an_outer_json_code_fence(official_entries):
    entry = entry_for(official_entries)
    report = "```json\n" + grounding_review(PARAPHRASE, [entry["content"]]) + "\n```"
    assert reviewed_generated_entries(report, PARAPHRASE, QUESTION, [entry]) == [entry]


def test_review_can_cite_contiguous_complete_sentences(official_entries):
    entry = entry_for(official_entries)
    quote = " ".join(answer_sentences(entry["content"])[:2])
    report = grounding_review(PARAPHRASE, [quote])
    assert reviewed_generated_entries(report, PARAPHRASE, QUESTION, [entry]) == [entry]


@pytest.mark.parametrize("ids,accepted", [([1], True), ([1, 2], True), ([], False), ([True], False),
                                        (["1"], False), ([0], False), ([999], False), ([1, 1], False)])
def test_model_can_reference_server_numbered_complete_sentences(official_entries, ids, accepted):
    entry = entry_for(official_entries)
    report = json.loads(grounding_review(PARAPHRASE, [entry["content"]]))
    report["claims"][0]["evidence"] = [{"source_id": "E1", "sentence_ids": ids}]
    assert bool(reviewed_generated_entries(json.dumps(report), PARAPHRASE, QUESTION, [entry])) is accepted


def test_known_opposite_statement_is_blocked_even_if_reviewer_would_agree(official_entries, monkeypatch):
    entry = entry_for(official_entries)
    opposite = "무당과 무가당은 같은 뜻입니다."
    response, calls = generated(monkeypatch, entry, opposite,
                                grounding_review(opposite, [entry["content"]]))
    assert len(calls) == 1
    assert response.answer == entry["content"]
    assert response.answer_source == "rag" and response.sources
    assert response.fallback_reason == "rag_generated_answer_rejected"
    assert "rag_evidence_contradiction" in response.validation_failure_types


@pytest.mark.parametrize("report", [None, "", "not JSON", "{}", '{"claims":[]}', '{"claims":true}'])
def test_unusable_review_returns_reviewed_text_with_sources(official_entries, monkeypatch, report):
    entry = entry_for(official_entries)
    response, _ = generated(monkeypatch, entry, PARAPHRASE, report)
    assert response.answer == entry["content"]
    assert response.sources == [rag.official_citation_from_entry(entry)]
    assert response.validation_failure_types == ["rag_claim_review_failed"]
    assert response.blocked_llm_output_stored is False


@pytest.mark.parametrize("verdict", ["contradicted", "unsupported", "SUPPORTED", True])
def test_non_supported_review_never_publishes_draft(official_entries, monkeypatch, verdict):
    entry = entry_for(official_entries)
    response, _ = generated(monkeypatch, entry, PARAPHRASE,
                             grounding_review(PARAPHRASE, [entry["content"]], verdict=verdict))
    assert response.answer == entry["content"]
    assert response.fallback_reason == "rag_generated_answer_rejected"


@pytest.mark.parametrize("mutation", ["missing_sentence", "changed_sentence", "unknown_source", "partial_quote", "fake_quote", "extra_field"])
def test_review_must_cover_every_sentence_and_quote_current_complete_evidence(official_entries, mutation):
    entry = entry_for(official_entries)
    answer = PARAPHRASE + " 무가당이어도 원료 자체의 당류가 남을 수 있어요."
    report = json.loads(grounding_review(answer, [entry["content"]]))
    if mutation == "missing_sentence":
        report["claims"].pop()
    elif mutation == "changed_sentence":
        report["claims"][0]["text"] = "다른 답변 문장"
    elif mutation == "unknown_source":
        report["claims"][0]["evidence"][0]["source_id"] = "E9"
    elif mutation == "partial_quote":
        report["claims"][0]["evidence"][0]["quote"] = "같은 뜻"
    elif mutation == "fake_quote":
        report["claims"][0]["evidence"][0]["quote"] = "무당과 무가당은 같은 뜻입니다."
    elif mutation == "extra_field":
        report["claims"][0]["approved"] = True
    assert not reviewed_generated_entries(json.dumps(report), answer, QUESTION, [entry])


def test_generation_failure_and_invented_number_use_reviewed_fallback(official_entries, monkeypatch):
    entry = entry_for(official_entries)
    for draft, failure in [(None, "llm_empty_response"), ("무가당은 당류가 987g이에요.", "unsupported_number")]:
        response, calls = generated(monkeypatch, entry, draft, None)
        assert len(calls) == 1
        assert response.answer == entry["content"] and response.sources
        assert failure in response.validation_failure_types


def test_multi_topic_review_cannot_drop_a_required_source(official_entries):
    basis = entry_for(official_entries, "nutrition-label-basis")
    percentage = entry_for(official_entries, "nutrition-label-percentage")
    answer = basis["content"]
    report = grounding_review(answer, [basis["content"]])
    assert not reviewed_generated_entries(report, answer, "100g 기준과 %를 설명해줘", [basis, percentage])


def test_revision_changed_during_review_never_returns_stale_citation(official_entries, monkeypatch):
    entry = entry_for(official_entries)
    report = grounding_review(PARAPHRASE, [entry["content"]])
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: [entry])
    calls = []

    def provider(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return PARAPHRASE
        monkeypatch.setattr(review, "RELEASED_EXCERPT_HASHES", {})
        return report

    monkeypatch.setattr(hybrid, "generate_text", provider)
    base = ChatResponse(intent="general_nutrition_guide", answer="근거를 다시 확인해야 해요.", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, QUESTION, None, None)
    assert result.answer == base.answer and result.answer_source == "rules"
    assert result.sources == []
    assert "rag_source_approval_changed" in result.validation_failure_types


def test_generated_mode_cannot_bypass_scope_or_product_guards(official_entries, monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: pytest.fail("Unsupported question must not be embedded"))
    base = ChatResponse(intent="general_allergy_guide", answer="근거가 부족해요.", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "같은 제조시설이면 알레르기 혼입 확률이 몇 %야?", None, None)
    assert result.answer_source == "rules" and not result.sources
