"""Frozen transition audit plus new compositions and negative controls.

The original failing expectations remain unchanged. Product, profile and source
identity matter independently of a successful HTTP response.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.models import ChatHistoryItem, ChatRequest
from app.main import chat
from app.services.emergency_triage import build_emergency_triage_response
from app.services.rag_grounding import verified_mass_conversion
from test_rag_grounded_answers import official_entries, base_response
from test_released_knowledge import packaged_sources
from test_semantic_request_regressions import state

AI = Path(__file__).resolve().parents[1]
DATASETS = ["conversation_transition_audit_cases.json", "conversation_recommendation_audit_cases.json"]
CASES = [(filename, case["id"]) for filename in DATASETS
         for case in json.loads((AI / "evaluation" / filename).read_text())["cases"]]
LIVE = json.loads((AI / "evaluation/live_transition_audit_journeys.json").read_text())
DOCS = json.loads((AI / "evaluation/live_document_coverage_journeys.json").read_text())
RAG_CASES = [(f"{name}:{i}", turns[:i + 1]) for name, turns in LIVE + DOCS
             for i, turn in enumerate(turns) if turn[1]]


@pytest.fixture(scope="module")
def audit_results(tmp_path_factory):
    results = {}
    directory = tmp_path_factory.mktemp("transition-audit")
    for filename in DATASETS:
        output = directory / filename
        completed = subprocess.run([
            sys.executable, "evaluation/run_conversation_usability_eval.py", "--cases",
            "evaluation/" + filename, "--output", str(output),
        ], cwd=AI, env=os.environ.copy(), capture_output=True, text=True, timeout=180)
        assert output.exists(), completed.stderr[-2000:]
        results[filename] = {c["id"]: c for c in json.loads(output.read_text())["cases"]}
    return results


@pytest.mark.parametrize("filename,case_id", CASES, ids=[case[1] for case in CASES])
def test_original_transition_and_recommendation_expectations(filename, case_id, audit_results):
    result = audit_results[filename][case_id]
    assert result["passed"], result["failures"]


@pytest.mark.parametrize("case_id,turns", RAG_CASES, ids=[case[0] for case in RAG_CASES])
def test_new_rag_wording_keeps_requested_sources(case_id, turns, official_entries, monkeypatch):
    from app.infrastructure.rag import official_citation_from_entry
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services import product
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository(seed_data={}))
    history = [item for turn in turns[:-1] for item in (
        ChatHistoryItem(role="user", content=turn[0]),
        ChatHistoryItem(role="assistant", content="검토된 설명"),
    )]
    response = chat(ChatRequest(message=turns[-1][0], chat_history=history))
    expected = {official_citation_from_entry(e) for e in official_entries
                if e["source"].split(":")[-1] in turns[-1][1]}
    assert response.answer_source == "rag", response.fallback_reason
    assert set(response.sources) == expected
    assert response.current_product is None
    assert not response.conversation_state.pending_allergy


def test_vegan_vegetarian_comparison_needs_both_definitions(official_entries):
    from app.infrastructure.rag import official_citation_from_entry
    response = chat(ChatRequest(message="비건하고 채식주의는 같은 뜻이야?"))
    expected = {official_citation_from_entry(e) for e in official_entries if
                e["source"].endswith((":vegan-definition", ":vegetarian-definition"))}
    assert response.answer_source == "rag"
    assert set(response.sources) == expected
    assert "달걀" in response.answer and "유제품" in response.answer


@pytest.mark.parametrize("message,answer", [
    ("1500mg이면 1.5g 맞지?", "맞아요. 1500mg은 1.5g"),
    ("1500mg이면 15g 맞지?", "아니에요. 1500mg은 1.5g"),
    ("0.125g은 125mg 맞나요?", "맞아요. 0.125g은 125mg"),
    ("그럼 0.001mg이면 0.000001g 맞아?", "맞아요. 0.001mg은 0.000001g"),
])
def test_reviewed_unit_conversion_is_exact_and_corrects_false_premises(official_entries, monkeypatch, message, answer):
    from app.services import hybrid
    from app.infrastructure.rag import official_citation_from_entry
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    result = hybrid.try_general_llm_fallback(base_response(), message, None, None)
    assert result.answer.startswith(answer)
    assert result.answer_source == "rag"
    assert result.fallback_reason == "rag_verified_unit_calculation"
    unit = next(e for e in official_entries if e["source"].endswith(":nutrition-mass-units"))
    assert result.sources == [official_citation_from_entry(unit)]


@pytest.mark.parametrize("message", [
    "1500mg이면 1.5g 맞지? 약을 그만큼 먹어도 돼?",
    "1500ml이면 1.5g 맞지?", "-1500mg이면 -1.5g 맞지?",
    "100000000000000mg이면 1g 맞지?", "1500mg이면 1500mg 맞지?",
])
def test_unit_conversion_does_not_infer_doses_densities_or_unbounded_values(official_entries, message):
    assert verified_mass_conversion(message, official_entries) is None


def test_unit_conversion_cannot_create_a_source_when_retrieval_is_missing_or_tampered(official_entries):
    question = "1500mg이면 1.5g 맞지?"
    assert verified_mass_conversion(question, []) is None
    entry = next(e for e in official_entries if e["source"].endswith(":nutrition-mass-units"))
    assert verified_mass_conversion(question, [{**entry, "content": entry["content"] + " 변조"}]) is None


@pytest.mark.parametrize("food", ["땅콩과자", "콩음료", "생선"])
@pytest.mark.parametrize("symptom", ["숨 쉬는 게 힘들어요", "숨이 찬 상태예요", "목이 꽉 조여요"])
def test_current_severe_symptoms_with_unseen_food_combinations(food, symptom):
    response = build_emergency_triage_response(f"{food} 먹은 뒤 지금 {symptom}")
    assert response is not None
    assert response.intent == "emergency_safety"
    assert response.safety_guidance.emergency_contact == "119"


@pytest.mark.parametrize("message", [
    "우유 먹고 숨이 차면 어떻게 해야 해?",
    "땅콩과자 먹고 목이 꽉 조인다면 알레르기야?",
    "콩음료 먹고 숨 쉬는 게 힘들다는 표현은 무슨 뜻이야?",
    "생선 먹었는데 숨이 찬 건 아니에요",
    "우유 마셨는데 호흡곤란은 아닌데요",
])
def test_conditional_metalinguistic_and_negated_symptoms_are_not_current_emergencies(message):
    assert build_emergency_triage_response(message) is None


@pytest.mark.parametrize("ending", ["아니에요", "아닌데요"])
def test_double_negation_of_absence_retains_current_symptom(ending):
    response = build_emergency_triage_response("우유 마셨는데 호흡곤란이 없는 건 " + ending)
    assert response is not None
    assert response.safety_guidance.emergency_contact == "119"


@pytest.fixture
def package_catalog(monkeypatch):
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services import product
    from evaluation.product_label_fixtures import materialize_evaluation_repository_seed, synthetic_evaluation_label_source_registry
    data = json.loads((AI / "evaluation" / DATASETS[0]).read_text())
    with synthetic_evaluation_label_source_registry():
        monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository(
            seed_data=materialize_evaluation_repository_seed(data["products"])))
        yield


@pytest.mark.parametrize("amount,expected", [("반 팩", "3.75g"), ("두 팩", "15g"),
                                                ("0.25팩", "1.875g"), ("2.5팩", "18.75g")])
def test_requested_amount_uses_package_size_and_keeps_fractional_precision(package_catalog, amount, expected):
    first = chat(ChatRequest(message="이 제품 정보 알려줘", barcode="880000009010"))
    response = chat(ChatRequest(message=amount + " 단백질 알려줘", conversation_state=state(first)))
    assert response.current_product.barcode == "880000009010"
    assert response.intent == "nutrition_explain"
    assert expected in response.answer
    assert "계산 근거" in response.answer


@pytest.mark.parametrize("amount", ["반 팩", "두 팩", "2.5팩"])
def test_quantity_cannot_supply_a_missing_label_package_size(package_catalog, amount):
    first = chat(ChatRequest(message="이 제품 정보 알려줘", barcode="880000009001"))
    response = chat(ChatRequest(message=amount + " 단백질 알려줘", conversation_state=state(first)))
    assert response.current_product.barcode == "880000009001"
    assert "계산하지 못했어요" in response.answer
    assert "포장 총용량" in response.answer
