"""Audit regressions: condition ownership, partial updates and task switching."""

import pytest

from app.core.models import ChatRequest, Product, Profile
from app.main import chat
from app.services.diet_conversation_context import apply_vegetarian_context
from app.services.message_constraint_guard import apply_turn_local_allergies
from app.services.recipe_composition import composition_issues
from test_food_chat_condition_lifecycle import saved_chat, state


def vegetarian(**updates):
    return {
        "profile_id": 1, "profile_name": "나", "allergies": [], "special_diets": ["채식"],
        "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True, **updates,
    }


@pytest.mark.parametrize("word", ["아이스크림", "아이스티", "바나나"])
def test_food_words_do_not_identify_an_allergy_owner(saved_chat, word):
    me = {"profile_id": 1, "profile_name": "나", "allergies": []}
    child = {"profile_id": 2, "profile_name": "아이", "allergies": []}
    result = saved_chat(
        f"새우 알레르기가 있는데 {word} 말고 이 제품 먹어도 돼?", me,
        profiles=[me, child], barcode="8801043035989",
    )
    assert result["overall_decision"] == "check_required"
    assert "프로필" in result["answer"]
    assert not result.get("ui_guidance")


@pytest.mark.parametrize("condition", ["유제품을 먹는 채식이야", "새우 알레르기가 있어"])
def test_off_owner_is_not_reassigned_to_single_active_profile(saved_chat, condition):
    me = vegetarian(vegetarian_dairy_allowed=None)
    child = {"profile_id": 2, "profile_name": "아이", "is_active": False, "allergies": []}
    result = saved_chat(
        f"아이는 {condition}. 이 제품으로 레시피 만들어줘", me,
        profiles=[me, child], barcode="P00156",
    )
    assert result["recipe"] is None
    assert result["overall_decision"] == "check_required"
    assert not result.get("ui_guidance")
    saved_chat.detail()
    followup = saved_chat("이 제품으로 레시피 만들어줘", me, profiles=[me, child], barcode="P00156")
    assert followup["recipe"] is None  # No dairy permission was attached to me.


@pytest.mark.parametrize("sentence", ["아이는 유제품을 먹어.", "민수는 유제품을 먹어."])
def test_unavailable_named_owner_is_not_the_single_active_profile(sentence):
    profile = Profile(**vegetarian(vegetarian_dairy_allowed=None))
    effective, allowances, ambiguous = apply_vegetarian_context([profile], sentence, {})
    assert ambiguous
    assert effective[0].vegetarian_dairy_allowed is None
    assert not allowances


@pytest.mark.parametrize("message", [
    "새우 알레르기는 없어. 이 제품 먹어도 돼?",
    "새우 알레르기가 없어요. 이 제품 먹어도 돼?",
    "새우 알레르기는 아니야. 이 제품 먹어도 돼?",
])
def test_denied_allergy_is_not_added(saved_chat, message):
    result = saved_chat(message, {"allergies": []}, barcode="8801043035989")
    assert result["allergy_status"] == "no_known_match"
    assert result["overall_decision"] != "avoid"
    assert not result.get("ui_guidance")


def test_negation_does_not_erase_saved_allergy(saved_chat):
    result = saved_chat("새우 알레르기는 없어. 이 제품 먹어도 돼?", {"allergies": ["새우"]}, barcode="8801043035989")
    assert result["allergy_status"] == "conflict"
    assert result["overall_decision"] == "avoid"


def test_mixed_allergy_statements_are_interpreted_separately():
    original = Profile(allergies=[])
    context = apply_turn_local_allergies("새우 알레르기는 없고 우유 알레르기가 있어. 이거 먹어도 돼?", [original])
    assert context.profiles[0].resolved_allergies() == ["우유"]
    assert original.resolved_allergies() == []


@pytest.mark.parametrize("message", ["유제품을 먹는지는 잘 모르겠어. 이 제품 먹어도 돼?", "유제품을 먹는지는 잘 모르겠어. 이 제품으로 레시피 추천해줘"])
def test_known_allergy_conflict_survives_diet_clarification(saved_chat, message):
    profile = vegetarian(allergies=["새우"])
    result = saved_chat(message, profile, barcode="8801043035989")
    assert result["overall_decision"] == "avoid"
    assert result["allergy_status"] == "conflict"
    assert result["assessment_summary"]["decision"] == "avoid"
    assert "새우" in result["answer"] and "유제품" in result["answer"]
    assert result["recipe"] is None
    if "먹어도" in message:
        assert result["intent"] == "can_i_eat"
    detail = saved_chat.detail()
    assert detail["messages"][-1]["response"]["overall_decision"] == "avoid"


@pytest.mark.parametrize("punctuation", [". ", ", "])
def test_clear_exclusion_survives_clarification_and_reopen(saved_chat, punctuation):
    profile = vegetarian()
    saved_chat("이 제품으로 채식 레시피 만들어줘", profile, barcode="P00156")
    question = saved_chat(f"유제품은 빼줘{punctuation}달걀을 먹는지는 잘 모르겠어. 다른 레시피 추천해줘", profile)
    assert question["recipe"] is None
    assert "나 프로필의 달걀 허용 여부" in question["answer"]
    assert "유제품 허용 여부" not in question["answer"]
    saved_chat.detail()
    result = saved_chat("달걀은 먹어. 다른 레시피 추천해줘", profile)
    assert result["recipe"] is not None
    assert result["recipe"]["source_product_blocked"]
    assert all("매일우유" not in item["name"] for item in result["recipe"]["ingredients"])


def test_partial_exclusion_survives_stateless_roundtrip():
    p = Profile(**vegetarian())
    first = chat(ChatRequest(message="유제품은 빼줘. 달걀은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", profile=p, barcode="P00156"))
    assert state(first)["vegetarian_allowances"][0]["dairy"] is False
    assert state(first)["vegetarian_allowances"][0]["egg_pending"] is True
    second = chat(ChatRequest(message="달걀은 먹어. 다른 레시피 추천해줘", profile=p, conversation_state=state(first)))
    assert second.recipe is not None and second.recipe.source_product_blocked


@pytest.mark.parametrize("change", [{"vegetarian_egg_allowed": False}, {"special_diets": ["채식", "저칼로리"]}])
def test_unrelated_profile_change_preserves_exclusion(saved_chat, change):
    profile = vegetarian()
    first = saved_chat("유제품은 빼줘. 이 제품으로 레시피 만들어줘", profile, barcode="P00156")
    assert first["recipe"]["source_product_blocked"]
    saved_chat.detail()
    result = saved_chat("다른 레시피 더 있어?", {**profile, **change})
    from app.services.conversations import get_conversation_repository
    room = get_conversation_repository().get(101, result["conversation_id"])
    assert room.state["vegetarian_allowances"][0]["dairy"] is False
    # The catalog may have no alternative satisfying the newly added diet.
    # Do not demand a recipe at the expense of silently relaxing a condition.
    assert result["recipe"] is None or result["recipe"]["source_product_blocked"]
    if "vegetarian_egg_allowed" in change:
        assert result["recipe"] is not None


def test_unrelated_field_change_preserves_permission_too():
    profile = Profile(**vegetarian(vegetarian_dairy_allowed=None))
    _, allowances, _ = apply_vegetarian_context([profile], "유제품은 먹어.", {})
    changed = profile.model_copy(update={"vegetarian_egg_allowed": False})
    effective, _, ambiguous = apply_vegetarian_context([changed], "다른 레시피", {"vegetarian_allowances": allowances})
    assert not ambiguous and effective[0].vegetarian_dairy_allowed is True


def test_legacy_signature_change_never_lifts_exclusion():
    profile = Profile(**vegetarian())
    previous = {"vegetarian_allowances": [{"profile_key": "id:1", "dairy": False, "profile_signature": "0" * 64}]}
    effective, _, _ = apply_vegetarian_context([profile], "다른 레시피", previous)
    assert effective[0].vegetarian_dairy_allowed is False


def test_current_field_setting_can_resolve_pending_clarification(saved_chat):
    profile = vegetarian()
    saved_chat("유제품은 빼줘. 달걀은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    saved_chat.detail()
    result = saved_chat("다른 레시피 더 있어?", {**profile, "vegetarian_egg_allowed": False})
    assert result["recipe"] is not None and result["recipe"]["source_product_blocked"]


def test_family_conflict_remains_visible_during_clarification(saved_chat):
    me = vegetarian()
    child = {"profile_id": 2, "profile_name": "아이", "allergies": ["새우"]}
    result = saved_chat("나는 유제품을 먹는지 모르겠어. 이 제품 먹어도 돼?", me, profiles=[me, child], barcode="8801043035989")
    assert result["overall_decision"] == "avoid"
    assert result["assessment_summary"]["decision"] == "avoid"
    by_name = {item["profile_name"]: item for item in result["per_profile_results"]}
    assert by_name["아이"]["allergy_status"] == "conflict"
    assert by_name["나"]["allergy_status"] == "no_known_match"


def test_pending_clarification_survives_unrelated_question(saved_chat):
    profile = vegetarian()
    saved_chat("유제품은 빼줘. 달걀은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    saved_chat.detail()
    pending = saved_chat("다른 레시피 더 있어?", profile)
    assert pending["recipe"] is None and "달걀 허용 여부" in pending["answer"]
    resolved = saved_chat("달걀은 먹어. 다른 레시피 추천해줘", profile)
    assert resolved["recipe"] is not None and resolved["recipe"]["source_product_blocked"]


def start_comparison(send):
    profile = {"allergies": ["새우"]}
    send("새우깡과 저염 두부스낵 비교해줘", profile)
    assert len(send("첫 번째", profile)["comparison_products"]) == 2
    return profile


def test_current_product_followup_does_not_resume_old_comparison(saved_chat):
    profile = start_comparison(saved_chat)
    saved_chat("이 우유 영양정보 알려줘", profile, barcode="P00156")
    saved_chat.detail()
    result = saved_chat("그럼 단백질은?", profile)
    assert not result["comparison_products"]
    assert result["current_product"]["product_name"] == "매일우유 오리지널 200ml"
    assert result["overall_decision"] != "avoid"
    assert result["intent"] == "nutrition_explain"


def test_new_product_with_short_axis_does_not_compare_old_products(saved_chat):
    profile = start_comparison(saved_chat)
    result = saved_chat("그럼 단백질은?", profile, barcode="P00156")
    assert not result["comparison_products"]
    assert result["current_product"]["product_name"] == "매일우유 오리지널 200ml"


@pytest.mark.parametrize("pending", [True, False])
def test_comparison_cancellation_switches_to_recipe(saved_chat, pending):
    profile = {"allergies": []}
    saved_chat("새우깡과 저염 두부스낵 비교해줘", profile)
    if not pending:
        saved_chat("첫 번째", profile)
    result = saved_chat("비교는 그만하고 저염 레시피 추천해줘", profile)
    assert result["intent"] == "recipe_recommend"
    assert result["recipe"] is not None
    assert not result["comparison_products"]


def test_standalone_comparison_cancellation_persists(saved_chat):
    profile = start_comparison(saved_chat)
    result = saved_chat("비교는 그만해줘", profile)
    assert "종료" in result["answer"]
    assert not result["comparison_products"]
    detail = saved_chat.detail()
    assert detail["messages"][-2]["content"] == "비교는 그만해줘"
    followup = saved_chat("그럼 단백질은?", profile)
    assert not followup["comparison_products"]


@pytest.mark.parametrize("name,category", [("주스", "과채음료"), ("채소주스", "과채음료"), ("소스", "소스류"), ("과일소스", "소스류")])
def test_listed_product_names_are_not_generic_additions(name, category):
    from app.services.product import set_product_repository, get_product_repository
    from app.infrastructure.repository import InMemoryProductRepository
    previous = get_product_repository()
    try:
        set_product_repository(InMemoryProductRepository({"AUDIT-FOOD": {
            "식품명": name, "바코드": "AUDIT-FOOD", "RAWMTRL_NM": "토마토, 사과, 당근", "allergy": [], "PRDLST_DCNM": category,
        }}))
        result = chat(ChatRequest(message="이 제품으로 레시피 추천해줘", barcode="AUDIT-FOOD", profile=Profile(allergies=[])))
        assert result.recipe is not None
    finally:
        set_product_repository(previous)


def test_masking_product_name_still_rejects_unlisted_generic_ingredient():
    product = Product(product_name="채소주스", category_detail="과채음료")
    ingredients = [{"name": "채소주스", "amount": "150ml"}]
    assert not composition_issues(ingredients, ["채소주스를 부어요."], product=product)
    assert "unresolved_ingredient_reference" in composition_issues(ingredients, ["채소주스를 붓고 채소를 섞어요."], product=product)
