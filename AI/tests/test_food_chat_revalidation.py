"""Regression scenarios from the food-chat revalidation, including paraphrases."""

import json

import pytest

from app.core.models import ChatRequest, ChatHistoryItem, Profile, RecipeResult
from app.main import chat
from app.infrastructure.llm import set_llm_client
from app.services.diet_conversation_context import stated_vegetarian_allowance, apply_vegetarian_context
from app.services.recipe import _recipe_fingerprint


def state(response):
    return response.conversation_state.model_dump(exclude_none=True)


def draft(**changes):
    return {
        "title": "오이 채소 볼", "servings": "1인분", "product_name": "기본 식재료",
        "ingredients": [{"name": "오이", "amount": "1/3개"}],
        "steps": ["오이를 썰어요."], "substitutions": [], **changes,
    }


@pytest.mark.parametrize("changes", [
    {"steps": ["오이를 썰어요. 알레르기 걱정 없이 안심하고 드세요."]},
    {"steps": ["오이를 썰어요. 한 그릇은 50kcal이고 단백질은 30그램입니다."]},
    {"steps": ["오이를 썰어요. 마지막에 카레분을 첨가합니다."]},
    {"steps": ["오이를 썰어요. 비밀분말을 가미합니다."]},
    {"substitutions": ["카레분으로 바꿔도 됩니다."]},
    {"title": "알레르기 걱정 없는 오이 볼"},
    {"ingredients": [{"name": "오이", "amount": "999999999999999999g"}]},
    {"ingredients": [{"name": "오이", "amount": "1500g"}]},
    {"servings": "10000인분"},
    {"servings": "2인분"},
])
def test_unverifiable_recipe_prose_and_quantities_fall_back(changes):
    set_llm_client(lambda _: json.dumps(draft(**changes), ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=["땅콩"])))
    finally:
        set_llm_client(None)
    assert response.answer_source == "rules"
    assert response.validation_failure_types


def test_closed_vocabulary_still_accepts_a_grounded_draft():
    set_llm_client(lambda _: json.dumps(draft(), ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=[])))
    finally:
        set_llm_client(None)
    assert response.answer_source == "hybrid"
    assert response.recipe.servings == "1인분"


@pytest.mark.parametrize("text", ["유제품은 못 먹어", "유제품은 안 먹어", "유제품은 먹지 못해", "우유는 먹을 수 없어"])
def test_negative_dairy_statement_never_enables_dairy(text):
    assert stated_vegetarian_allowance(text) == {"dairy": False}


def test_statement_before_question_is_not_discarded():
    assert stated_vegetarian_allowance("유제품과 달걀은 먹는 채식이야. 레시피 만들어줄래?") == {"dairy": True, "egg": True}
    assert stated_vegetarian_allowance("유제품은 먹어도 돼?") == {}
    assert stated_vegetarian_allowance("유제품은 안 먹고 달걀은 먹어.") == {"dairy": False, "egg": True}


def test_banana_does_not_name_the_self_profile():
    profiles = [Profile(profile_name="나", allergies=[]), Profile(profile_name="아이", allergies=[])]
    _, allowances, ambiguous = apply_vegetarian_context(profiles, "유제품과 달걀은 먹는 채식이야. 바나나 레시피 추천해줘", {})
    assert ambiguous and not allowances
    effective, allowances, ambiguous = apply_vegetarian_context(profiles, "아이는 유제품을 먹는 채식이야. 바나나 레시피 추천해줘", {})
    assert not ambiguous
    assert effective[0].vegetarian_dairy_allowed is None
    assert effective[1].vegetarian_dairy_allowed is True


def test_negative_dairy_request_does_not_use_original_milk():
    response = chat(ChatRequest(
        message="유제품은 못 먹어. 이 제품으로 채식 레시피 만들어줘", barcode="P00156",
        profile=Profile(allergies=[], special_diets=["채식"]),
    ))
    assert response.recipe is None or response.recipe.source_product_blocked
    assert response.conversation_state.vegetarian_allowances[0].dairy is False


def test_equivalent_cooking_verbs_have_one_fingerprint():
    recipe = RecipeResult(**draft(), allergen_check=["시험용"])
    a = recipe.model_copy(update={"steps": ["오이를 섞어요."]})
    b = recipe.model_copy(update={"title": "오이 샐러드", "steps": ["오이를 버무려요."]})
    assert _recipe_fingerprint(a) == _recipe_fingerprint(b)


@pytest.mark.parametrize("followup", ["나트륨", "나트륨으로", "그럼 단백질은?", "나트륨으로 비교해줘"])
def test_short_comparison_axis_keeps_both_products_and_allergy(followup):
    profile = Profile(allergies=["새우"])
    first = chat(ChatRequest(message="새우깡과 저염 두부스낵 비교해줘", profile=profile))
    selected = chat(ChatRequest(message="첫 번째", profile=profile, conversation_state=state(first)))
    response = chat(ChatRequest(message=followup, profile=profile, conversation_state=state(selected)))
    assert response.intent == "food_compare"
    assert len(response.comparison_products) == 2
    assert response.overall_decision == "avoid"
    assert response.conversation_state.comparison_products


@pytest.mark.parametrize("selection", ["첫 번째, 나트륨으로 비교해줘", "첫 번째 나트륨으로 비교해줘", "농심 새우깡 90g으로 할게"])
def test_combined_or_named_comparison_selection(selection):
    profile = Profile(allergies=["새우"])
    first = chat(ChatRequest(message="새우깡과 저염 두부스낵 비교해줘", profile=profile))
    response = chat(ChatRequest(message=selection, profile=profile, conversation_state=state(first)))
    assert response.intent == "food_compare"
    assert len(response.comparison_products) == 2
    assert response.overall_decision == "avoid"


def test_cleared_recipe_goals_do_not_resurrect_from_history():
    profile = Profile(allergies=[])
    history = []
    previous = {}
    goals = []
    for message in ["저염 레시피 추천해줘", "레시피 추천해줘", "다른 레시피 더 있어?"]:
        response = chat(ChatRequest(message=message, profile=profile, conversation_state=previous, chat_history=history))
        goals.append(response.recipe.requested_diets)
        previous = state(response)
        history.extend([ChatHistoryItem(role="user", content=message), ChatHistoryItem(role="assistant", content=response.answer)])
    assert goals == [["저염식"], [], []]
    assert previous["recipe_goals_initialized"] is True


@pytest.mark.parametrize("detour", [["새우깡 먹어도 돼?", "첫 번째"], ["새우깡과 저염 두부스낵 비교해줘", "첫 번째"]])
def test_product_selection_preserves_recipe_goals_and_fingerprints(detour):
    profile = Profile(allergies=[])
    first = chat(ChatRequest(message="저염 레시피 추천해줘", profile=profile))
    previous = state(first)
    for message in detour:
        response = chat(ChatRequest(message=message, profile=profile, conversation_state=previous))
        previous = state(response)
        assert previous["recipe_requested_diets"] == ["저염식"]
        assert previous["recipe_fingerprints"] == state(first)["recipe_fingerprints"]
    response = chat(ChatRequest(message="다른 레시피 더 있어?", profile=profile, conversation_state=previous))
    assert response.recipe.requested_diets == ["저염식"]
    assert response.recipe.title != first.recipe.title


def test_recipe_summary_uses_its_actual_scope_and_missing_information():
    response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=[])))
    assert response.assessment_summary.scope == "recipe"
    assert "질문 의도 또는 평가 기준" not in response.missing_information
    assert response.missing_information == response.assessment_summary.missing_information
    assert any("영양" in item for item in response.missing_information)


def test_missing_allergy_profile_never_claims_a_completed_check():
    response = chat(ChatRequest(message="레시피 추천해줘", profile=Profile()))
    assert response.allergy_status == "unknown"
    assert response.assessment_summary.decision == "insufficient_information"
    assert all("검사 완료" not in reason for item in response.per_profile_results for reason in item.reasons)
    assert all("직접 충돌 없음" not in item for item in response.recipe.allergen_check)
    assert any("미입력" in item for item in response.recipe.allergen_check)


@pytest.mark.parametrize("message", ["이 제품 레시피 추천해줘", "이 제품 먹어도 돼?"])
def test_confirmed_conflict_is_not_weakened_by_summary(message):
    response = chat(ChatRequest(message=message, barcode="8801043035989", profile=Profile(allergies=["새우"])))
    assert response.overall_decision == "avoid"
    assert response.assessment_summary.decision == "avoid"
    assert not any("임상" in item for item in response.missing_information)


@pytest.fixture
def saved_chat(monkeypatch):
    from uuid import uuid4
    from test_conversation_core import SigningClient, _headers, API_TOKEN, IDENTITY_SECRET
    from app.api.main import app
    from app.infrastructure.conversations import InMemoryConversationRepository
    from app.infrastructure.request_identity import reset_conversation_identity_nonce_registry
    from app.services.conversations import get_conversation_repository, set_conversation_repository
    old = get_conversation_repository()
    repository = InMemoryConversationRepository()
    set_conversation_repository(repository)
    reset_conversation_identity_nonce_registry()
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_CONVERSATIONS", "true")
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", IDENTITY_SECRET)
    client = SigningClient(app)
    room = repository.create(101)

    def send(message, profile=None):
        response = client.post("/chat", json={
            "conversation_id": room.conversation_id, "turn_id": str(uuid4()),
            "message": message, "profile": profile or {"allergies": []},
        }, headers=_headers("POST", "/chat"))
        assert response.status_code == 200, response.text
        return response.json()
    def detail():
        path = f"/conversations/{room.conversation_id}"
        response = client.get(path, headers=_headers("GET", path))
        assert response.status_code == 200, response.text
        return response.json()
    send.detail = detail
    try:
        yield send
    finally:
        client.close()
        set_conversation_repository(old)


def test_saved_room_preserves_goals_after_selection_and_history_truncation(saved_chat):
    first = saved_chat("저염 레시피 추천해줘")
    saved_chat("새우깡 먹어도 돼?")
    saved_chat("첫 번째")
    for _ in range(25):
        saved_chat("알레르기 표시 보는 법 알려줘")
    last = saved_chat("다른 레시피 더 있어?")
    assert last["recipe"]["requested_diets"] == ["저염식"]
    assert last["recipe"]["title"] != first["recipe"]["title"]


def test_saved_room_cleared_goals_remain_cleared(saved_chat):
    results = [saved_chat(message) for message in ["저염 레시피 추천해줘", "레시피 추천해줘", "다른 레시피 더 있어?"]]
    assert [item["recipe"]["requested_diets"] for item in results] == [["저염식"], [], []]


def test_saved_comparison_combined_pick_and_short_axis(saved_chat):
    profile = {"allergies": ["새우"]}
    saved_chat("새우깡과 저염 두부스낵 비교해줘", profile)
    selected = saved_chat("첫 번째, 나트륨으로 비교해줘", profile)
    followup = saved_chat("그럼 단백질은?", profile)
    for response in (selected, followup):
        assert len(response["comparison_products"]) == 2
        assert response["overall_decision"] == "avoid"
        assert response["assessment_summary"]["decision"] == "avoid"


def test_diagnostics_are_private_in_chat_and_reopened_room(saved_chat):
    response = saved_chat("저염 레시피 추천해줘")
    private = {"pipeline_stage", "fallback_reason", "validation_failure_types", "critical_safety_violation",
               "blocked_llm_output_stored", "policy_version", "applied_rule_ids", "evidence", "source_details", "provenance_status"}
    assert private.isdisjoint(response)
    restored = [message["response"] for message in saved_chat.detail()["messages"] if message["role"] == "assistant"]
    assert restored
    assert all(private.isdisjoint(item) for item in restored)
    assert restored[0]["recipe"] == response["recipe"]
