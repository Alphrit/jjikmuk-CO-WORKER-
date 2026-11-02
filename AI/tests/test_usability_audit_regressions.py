"""Behavioral regressions from the independent 2026-11-01 usability audit."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.api.adapters import normalize_product_payload
from app.core.models import ChatHistoryItem, Product, Profile
from app.intent.explanation_topics import requested_topics, resolve_explanation_question

AI_DIR = Path(__file__).resolve().parents[1]
CASE_IDS = [case["id"] for case in json.loads(
    (AI_DIR / "evaluation/conversation_usability_cases.json").read_text()
)["cases"]]


@pytest.fixture(scope="module")
def acceptance_results(tmp_path_factory):
    destination = tmp_path_factory.mktemp("conversation-usability") / "results.json"
    result = subprocess.run(
        [sys.executable, "evaluation/run_conversation_usability_eval.py", "--output", str(destination)],
        cwd=AI_DIR, env=os.environ.copy(), capture_output=True, text=True, timeout=120,
    )
    assert destination.exists(), result.stderr[-2000:]
    report = json.loads(destination.read_text())
    return {case["id"]: case for case in report["cases"]}


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_audited_conversation_expectations(case_id, acceptance_results):
    case = acceptance_results[case_id]
    assert case["passed"], case["failures"]


@pytest.mark.parametrize("factory", [Product.model_validate, normalize_product_payload])
@pytest.mark.parametrize("camel", [False, True])
def test_mass_fields_do_not_become_percentages(factory, camel):
    fields = ({"sodiumG": .125, "cholesterolG": .0015, "sodiumMg": 125, "cholesterolMg": 1.5}
              if camel else {"sodium_g": .125, "cholesterol_g": .0015, "sodium_mg": 125, "cholesterol_mg": 1.5})
    product = factory(fields)
    assert product.nutrition["sodium"] == 125
    assert product.nutrition["cholesterol"] == 1.5
    assert product.nutrition_percent == {}


@pytest.mark.parametrize("factory", [Product.model_validate, normalize_product_payload])
def test_explicit_percentages_remain_percentages(factory):
    product = factory({"sodium_percent": "6%", "cholesterolPercent": 2, "sodium_g": .125})
    assert product.nutrition_percent == {"sodium": 6, "cholesterol": 2}


def test_legacy_nested_mass_aliases_are_removed():
    product = Product(nutrition_percent={"sodium_g": .125, "cholesterolG": .0015, "protein": 5})
    assert product.nutrition_percent == {"protein": 5}


@pytest.mark.parametrize("questions,expected", [
    (["슈가프리랑 설탕을 넣지 않았다는 건 어떻게 달라?", "아 그러면 칼로리도 없는 거네?", "열량도 0이라는 말이 맞아?", "방금 설명 한 문장으로 정리해줘"], {"sugar_energy"}),
    (["다른 제품이랑 제조 공장이 같대. 무슨 의미야?", "그럼 원재료에 들어갔다는 건 아니지?"], {"allergy_facility_warning"}),
    (["다른 제품이랑 제조 공장이 같대. 무슨 의미야?", "그럼 원재료에 들어갔다는 건 아니지?", "어려워. 초보자도 알게 한 문장만", "얼마나 섞였는지 %로 알려줘"], set()),
    (["무가당 표시는 무슨 의미야?", "이제 나트륨하고 소금 차이만 설명해줘", "그거 짧게 다시 말해줄래?"], {"sodium_salt"}),
    (["나트륨이랑 소금은 같은 건가? 한 줄로", "소금 얘기는 됐고, g하고 mg는 뭐가 달라?"], {"nutrition_units"}),
    (["비건이면 우유 표시를 안 봐도 된다는 뜻이야?"], {"vegan_allergy"}),
])
def test_live_audit_question_topics(questions, expected):
    history = [ChatHistoryItem(role="user", content=text) for text in questions[:-1]]
    query = resolve_explanation_question(questions[-1], history)
    assert requested_topics(query) == expected


def test_recipe_replay_rejects_tampering_and_changed_conditions(monkeypatch):
    from app.services.recipe import build_recipe_response
    from app.services.recipe_followup import recipe_receipt, recipe_followup_response
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    mom = Profile(profile_name="엄마", allergies=["우유"])
    first = build_recipe_response(mom, None, "저염 레시피 추천해줘", profiles=[mom])
    assert first.recipe is not None
    receipt = recipe_receipt(first.recipe, [mom], ["저염식"])
    state = {"recipe_receipt": receipt, "recipe_requested_diets": ["저염식"]}
    question = "방금 레시피에서 조리 순서만 보여줘"
    replay = recipe_followup_response(question, state, [mom])
    assert replay.recipe == first.recipe
    assert replay.overall_decision == "check_required"
    for profiles, altered in [
        ([Profile(profile_name="아빠", allergies=["땅콩"])], state),
        ([mom], {**state, "recipe_receipt": "A" + receipt[1:]}),
        ([mom], {**state, "recipe_requested_diets": ["채식"]}),
    ]:
        rejected = recipe_followup_response(question, altered, profiles)
        assert rejected.recipe is None
        assert rejected.overall_decision == "check_required"


def test_recipe_receipt_survives_worker_restart_with_deployment_secret(monkeypatch):
    import app.services.recipe_followup as followup
    from app.services.recipe import build_recipe_response
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", "synthetic-recipe-replay-test-secret")
    first = build_recipe_response(None, None, "저염 레시피 추천해줘", profiles=[])
    receipt = followup.recipe_receipt(first.recipe, [], ["저염식"])
    monkeypatch.setattr(followup, "_LOCAL_KEY", b"simulated-new-worker")
    state = {"recipe_receipt": receipt, "recipe_requested_diets": ["저염식"]}
    assert followup.recipe_followup_response("조리 순서만 보여줘", state, []).recipe == first.recipe
    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", "rotated-secret")
    assert followup.recipe_followup_response("조리 순서만 보여줘", state, []).recipe is None


def test_unresolved_new_recipe_does_not_replay_previous_dish(monkeypatch):
    from app.api.main import chat
    from app.core.models import ChatRequest
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    first = chat(ChatRequest(message="저염 레시피 추천해줘"))
    assert first.recipe is not None
    second = chat(ChatRequest(message="이 제품으로 레시피 추천해줘", barcode="MISSING-UX-RECIPE-991",
                              conversation_state=first.conversation_state.model_dump()))
    assert second.recipe is None
    third = chat(ChatRequest(message="방금 레시피에서 조리 순서만 보여줘",
                             conversation_state=second.conversation_state.model_dump()))
    assert third.recipe is None


def test_optional_recipe_receipt_cannot_exhaust_saved_state_budget():
    from app.core.conversation_context_fields import carry_task_context
    state = {"recipe_receipt": "A" * 3072, "recipe_fingerprints": [str(i).zfill(64) for i in range(20)]}
    merged = carry_task_context(state, {"pending_user_message": "확인" * 500})
    assert "recipe_receipt" not in merged
    assert merged["pending_user_message"] == "확인" * 500


def test_suggested_intake_followup_does_not_only_repeat_nutrition(monkeypatch):
    from app.services.chat import generate_chat_response
    from test_chatbot import _verified_product
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    response = generate_chat_response(Profile(allergies=["우유"]),
        _verified_product(ingredients="원유", allergy=["우유"], nutrition={"sodium": 40}),
        "이 수치까지 포함해서 먹어도 괜찮은지 알려줘",
        chat_history=[ChatHistoryItem(role="user", content="나트륨 수치 알려줘")])
    assert response.intent == "can_i_eat"
    assert response.overall_decision == "avoid"
    assert "40mg" in response.answer


def test_unspecified_other_profile_requires_selection():
    from app.services.condition_subject import unresolved_profile_target_response
    response = unresolved_profile_target_response("다른 프로필 기준으로 바꿔서 다시 알려줘", [Profile(profile_name="엄마")])
    assert "ON" in response.answer
    assert response.per_profile_results == []
    assert response.overall_decision == "check_required"


@pytest.mark.parametrize("message", ["원재료와 영양성분 둘 다 보여줘", "영양정보를 정리해줘", "나트륨 수치 보여줘"])
@pytest.mark.parametrize("has_nutrition", [True, False])
def test_label_display_wording_and_missing_data_are_both_supported(message, has_nutrition, monkeypatch):
    from app.services.chat import generate_chat_response
    from test_chatbot import _verified_product
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    product = _verified_product(ingredients="원유", nutrition={"sodium": 40})
    if not has_nutrition:
        product = product.model_copy(update={"nutrition": {}})
    response = generate_chat_response(None, product, message)
    assert response.intent == "nutrition_explain"
    assert "40mg" in response.answer if has_nutrition else "영양성분 정보를 확인하지 못했어요" in response.answer
    if "원재료" in message:
        assert "원유" in response.answer


def test_compound_query_discloses_each_missing_field(monkeypatch):
    from app.services.chat import generate_chat_response
    from test_chatbot import _verified_product
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    product = _verified_product().model_copy(update={"ingredients": [], "rawmtrl_nm": None, "nutrition": {}})
    response = generate_chat_response(None, product, "원재료와 영양성분 둘 다 보여줘")
    assert "영양성분 정보를 확인하지 못했어요" in response.answer
    assert "원재료: 정보를 확인하지 못했어요" in response.answer
