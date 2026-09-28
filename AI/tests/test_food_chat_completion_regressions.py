"""Acceptance regressions for the audited food-chat conversation boundaries."""

import json

import pytest

from app.core.models import ChatRequest, Product, Profile
from app.main import chat
from app.infrastructure.llm import set_llm_client
from app.services.recipe import build_recipe_response


def test_known_family_conflict_survives_another_missing_profile():
    response = chat(ChatRequest(
        message="이 제품 먹어도 돼?", barcode="8801043035989",
        profiles=[Profile(profile_name="아이", allergies=["새우"]), Profile(profile_name="나")],
    ))
    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
    assert [item.allergy_status for item in response.per_profile_results] == ["conflict", "unknown"]


@pytest.mark.parametrize("message", ["이 제품 먹어도 돼?", "이 제품 레시피 추천해줘"])
def test_unsupported_allergy_cannot_become_a_negative_match(message):
    response = chat(ChatRequest(
        message=message, barcode="8801234567890",
        profile=Profile(allergies=["미등록 항목"]),
    ))
    assert response.assessment_status == "unsupported"
    assert response.allergy_status == "unknown"
    assert response.overall_decision == "check_required"


def test_turn_diet_is_used_in_top_level_and_family_results():
    response = chat(ChatRequest(
        message="이 제품 글루텐프리로 먹어도 돼?", barcode="8801043035989",
        profiles=[Profile(profile_name="나", allergies=[]), Profile(profile_name="아이", allergies=[])],
    ))
    assert response.diet_match == "not_match"
    assert response.overall_decision == "caution"
    assert all(item.diet_match == "not_match" for item in response.per_profile_results)


def test_missing_source_label_blocks_recipe_without_claiming_a_completed_check():
    response = build_recipe_response(
        Profile(allergies=["새우"]),
        Product(product_name="표시 없는 스낵", prdlst_dcnm="과자", facts_verified=True),
        "이 제품 레시피 추천해줘",
    )
    assert response.recipe is None
    assert response.allergy_status == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.missing_information


def _draft(**changes):
    return {
        "title": "병아리콩 오이 볼", "servings": "1인분", "product_name": "기본 식재료",
        "ingredients": [{"name": "삶은 병아리콩", "amount": "1/2컵"}, {"name": "오이", "amount": "1/3개"}],
        "steps": ["병아리콩과 오이를 섞어요."], "substitutions": [], **changes,
    }


@pytest.mark.parametrize("draft", [
    _draft(title="땅콩 알레르기에도 100% 안전한 채소 볼"),
    _draft(ingredients=[{"name": "오이", "amount": "땅콩버터 1큰술"}]),
    _draft(ingredients=[{"name": "오이", "amount": "-10g"}]),
    _draft(steps=["모든 재료를 섞어요. 나트륨 0mg으로 저염 인증을 충족해요."]),
    _draft(steps=["병아리콩과 오이를 섞고 소금 5큰술을 넣어요."]),
    _draft(steps=["병아리콩과 오이를 섞고 비밀가루를 추가해요."]),
    _draft(steps=["병아리콩과 오이를 섞고 비밀가루 1큰술 추가해요."]),
    _draft(steps=["비밀가루와 오이를 섞어요."]),
    _draft(title="100% allergen-free safe bowl"),
])
def test_unsafe_or_unverifiable_recipe_drafts_fall_back(draft):
    set_llm_client(lambda _prompt: json.dumps(draft, ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="저염 레시피 추천해줘", profile=Profile(allergies=["땅콩"])))
    finally:
        set_llm_client(None)
    assert response.answer_source == "rules"
    assert response.validation_failure_types
    assert "100% 안전" not in response.answer


def test_recipe_fingerprint_does_not_trust_the_title():
    from app.core.models import RecipeResult
    from app.services.recipe import _recipe_fingerprint
    base = RecipeResult(**_draft(), allergen_check=["검사"])
    assert _recipe_fingerprint(base) == _recipe_fingerprint(base.model_copy(update={"title": "병아리콩 샐러드"}))


def _state(response):
    return response.conversation_state.model_dump(exclude_none=True)


def test_comparison_confirms_a_variant_then_keeps_both_products_for_the_axis():
    profile = Profile(allergies=["새우"])
    first = chat(ChatRequest(message="새우깡과 저염 두부스낵 비교해줘", profile=profile))
    assert len(first.product_candidates) == 2
    assert first.overall_decision == "check_required"
    selected = chat(ChatRequest(message="첫 번째", profile=profile, conversation_state=_state(first)))
    assert selected.overall_decision == "avoid"
    assert len(selected.comparison_products) == 2
    followup = chat(ChatRequest(message="나트륨으로 비교해줘", profile=profile, conversation_state=_state(selected)))
    assert followup.overall_decision == "avoid"
    assert [p.barcode for p in followup.comparison_products] == [p.barcode for p in selected.comparison_products]
    assert followup.missing_information
    assert set(followup.missing_information) == set(followup.assessment_summary.missing_information)
    assert followup.assessment_status == "incomplete"
    assert "기준을 아직 정하지" not in followup.answer


def test_recipe_goal_survives_history_truncation_and_unrelated_turns():
    profile = Profile(allergies=[])
    first = chat(ChatRequest(message="저염 레시피 추천해줘", profile=profile))
    middle = chat(ChatRequest(message="알레르기 표시 보는 법 알려줘", profile=profile, conversation_state=_state(first)))
    next_recipe = chat(ChatRequest(message="다른 레시피 더 있어?", profile=profile, conversation_state=_state(middle)))
    assert next_recipe.recipe.requested_diets == ["저염식"]
    assert next_recipe.recipe.title != first.recipe.title


def test_vegetarian_confirmation_is_applied_and_retained_but_not_persisted_to_profile():
    profile = Profile(profile_id=12, allergies=[], special_diets=["채식"])
    first = chat(ChatRequest(message="이 우유로 채식 레시피 추천해줘", barcode="P00156", profile=profile))
    assert first.recipe is None
    clarified = chat(ChatRequest(
        message="유제품과 달걀은 먹는 채식이야. 이 제품으로 레시피 만들어줘",
        profile=profile, conversation_state=_state(first),
    ))
    assert clarified.recipe is not None
    assert clarified.conversation_state.vegetarian_allowances
    again = chat(ChatRequest(message="다른 레시피 더 있어?", profile=profile, conversation_state=_state(clarified)))
    assert again.recipe is not None
    assert profile.vegetarian_dairy_allowed is None


def test_vegetarian_allowance_cannot_override_milk_allergy():
    response = chat(ChatRequest(
        message="유제품과 달걀은 먹는 채식이야. 이 제품으로 레시피 만들어줘", barcode="P00156",
        profile=Profile(allergies=["우유"], special_diets=["채식"]),
    ))
    assert response.overall_decision == "avoid"
    assert response.recipe is None or response.recipe.source_product_blocked


def test_vegetarian_allowance_needs_an_unambiguous_profile():
    response = chat(ChatRequest(
        message="유제품과 달걀은 먹는 채식이야. 레시피 만들어줘",
        profiles=[Profile(profile_name="아이", allergies=[]), Profile(profile_name="나", allergies=[])],
    ))
    assert response.recipe is None
    assert "누구" in response.answer


def test_product_taxonomy_wins_over_misleading_name_words():
    from app.services.recipe import _product_recipe_variants
    assert _product_recipe_variants(Product(product_name="바닐라 우유 아이스크림", prdlst_dcnm="빙과류")) == []
    variants = _product_recipe_variants(Product(product_name="라면 소스", prdlst_dcnm="소스류"))
    assert variants[0]["ingredients"][0]["amount"] == "1큰술"


def test_every_high_protein_template_has_a_named_protein_ingredient():
    from app.services.recipe import _general_recipe_variants
    for variant in _general_recipe_variants(["고단백"]):
        assert any(item["name"] in {"삶은 병아리콩", "삶은 렌틸콩", "두부"} for item in variant["ingredients"])


def test_comparison_missing_value_is_not_misreported_as_missing_axis(monkeypatch):
    from app.services import comparison_context
    from app.services.general_handlers import handle_food_compare
    products = [
        Product(product_name="현미 과자", rawmtrl_nm="현미", nutrition={"sodium": 10}, facts_verified=True),
        Product(product_name="쌀 과자", rawmtrl_nm="쌀", facts_verified=True),
    ]
    monkeypatch.setattr(comparison_context, "resolve_comparison", lambda message, *_: (products, None, {}, message))
    response = handle_food_compare(Profile(allergies=[]), None, "두 제품 나트륨 비교해줘")
    assert "기준을 아직 정하지" not in response.answer
    assert "쌀 과자" in " ".join(response.missing_information)
    assert response.assessment_status == "incomplete"


def test_new_recipe_request_can_clear_previous_message_goals():
    profile = Profile(allergies=[])
    first = chat(ChatRequest(message="저염 레시피 추천해줘", profile=profile))
    fresh = chat(ChatRequest(message="레시피 추천해줘", profile=profile, conversation_state=_state(first)))
    again = chat(ChatRequest(message="다른 레시피 더 있어?", profile=profile, conversation_state=_state(fresh)))
    assert fresh.recipe.requested_diets == []
    assert again.recipe.requested_diets == []


def test_packaged_compound_not_in_allowed_ingredients_is_rejected():
    draft = _draft(product_name="저염 두부스낵", ingredients=[
        {"name": "저염 두부스낵", "amount": "30g"}, {"name": "카레가루", "amount": "1큰술"},
    ], steps=["재료를 섞어요."])
    set_llm_client(lambda _: json.dumps(draft, ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="이 제품 레시피 추천해줘", barcode="8801234567890", profile=Profile(allergies=["우유"])))
    finally:
        set_llm_client(None)
    assert response.answer_source == "rules"
    assert all(item.name != "카레가루" for item in response.recipe.ingredients)


def test_saved_room_retains_recipe_goal_beyond_fifty_history_messages(monkeypatch):
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
    try:
        room = repository.create(101)

        def send(message):
            response = client.post("/chat", json={
                "conversation_id": room.conversation_id, "turn_id": str(uuid4()),
                "message": message, "profile": {"allergies": []},
            }, headers=_headers("POST", "/chat"))
            assert response.status_code == 200, response.text
            return response.json()

        send("저염 레시피 추천해줘")
        for _ in range(25):
            send("알레르기 표시 보는 법 알려줘")
        last = send("다른 레시피 더 있어?")
        assert last["recipe"]["requested_diets"] == ["저염식"]
        assert repository.get(101, room.conversation_id).state["recipe_requested_diets"] == ["저염식"]
    finally:
        client.close()
        set_conversation_repository(old)
