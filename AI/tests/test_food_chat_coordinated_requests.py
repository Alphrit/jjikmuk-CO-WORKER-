"""The six request/condition failures found after the 1,233-test review."""

import pytest

from app.core.models import ChatRequest, Profile
from app.main import chat
from app.services.diet_conversation_context import stated_vegetarian_allowance
from app.services.message_constraint_guard import _message_allergens
from app.services.request_language import request_is_cancelled
from test_food_chat_condition_lifecycle import saved_chat, state
from test_food_chat_pending_conditions import stored_state


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}
VEG = {**ME, "special_diets": ["채식"], "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True}


def assert_blocked(response):
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("foods", [
    "새우, 땅콩", "새우랑 땅콩", "새우와 땅콩", "새우하고 땅콩", "새우 및 땅콩",
    "새우랑땅콩", "땅콩, 새우", "땅콩과 새우", "우유, 새우, 땅콩",
])
def test_allergy_list_never_drops_shrimp(saved_chat, foods):
    result = saved_chat(f"{foods} 알레르기가 있어. 이 제품으로 레시피 추천해줘", ME, barcode="8801043035989")
    assert result["allergy_status"] == "conflict"
    assert result["overall_decision"] == "avoid"
    assert "새우" in result["answer"]
    assert_blocked(result)
    assert saved_chat.detail()["messages"][-1]["response"] == result
    assert ME["allergies"] == []


@pytest.mark.parametrize("statement,confirmed,denied", [
    ("새우 알레르기는 없어, 땅콩 알레르기는 있어", {"땅콩"}, {"새우"}),
    ("새우 알레르기는 없지만, 땅콩 알레르기는 있어", {"땅콩"}, {"새우"}),
    ("새우, 땅콩 알레르기는 없어", set(), {"새우", "땅콩"}),
    ("새우 알레르기는 있고, 땅콩과 우유 알레르기는 없어", {"새우"}, {"땅콩", "우유"}),
    ("새우, 땅콩 알레르기는 없어. 우유 알레르기는 있어", {"우유"}, {"새우", "땅콩"}),
    ("사과와 바나나 알레르기가 있어", {"사과", "바나나"}, set()),
])
def test_coordination_does_not_cross_a_predicate(statement, confirmed, denied):
    parsed = _message_allergens(statement)
    assert parsed.confirmed == confirmed
    assert parsed.denied == denied
    assert not parsed.uncertain


@pytest.mark.parametrize("foods", ["새우, 땅콩", "새우랑 땅콩"])
def test_uncertain_list_is_not_confirmed(foods):
    parsed = _message_allergens(f"{foods} 알레르기가 있는지 모르겠어")
    assert parsed.uncertain == {"새우", "땅콩"}
    assert not parsed.confirmed


@pytest.mark.parametrize("off", [False, True])
def test_lists_do_not_merge_different_owners(saved_chat, off):
    child = {**CHILD, "is_active": not off}
    response = saved_chat("나는 새우 알레르기가 있어. 아이는 땅콩 알레르기가 있어. 이 제품으로 레시피 추천해줘",
                          ME, profiles=[ME, child], barcode="8801043035989")
    assert response["recipe"] is None
    assert "어느 프로필" in response["answer"]


@pytest.mark.parametrize("registered", [[], ["새우"]])
@pytest.mark.parametrize("statement", [
    "바나나 알레르기로 고생하고 있어", "바나나 알레르기를 앓고 있어",
    "나는 바나나 알레르기로 힘들어",
])
def test_uninterpreted_personal_allergy_stays_pending(saved_chat, registered, statement):
    profile = {**ME, "allergies": registered}
    response = saved_chat(statement + ". 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert response["recipe"] is None and not response["recommended_products"]
    pending = stored_state(response)["pending_allergy"]
    assert "바나나" in pending.get("unsupported_uncertain", []) + pending.get("unsupported_confirmed", [])
    saved_chat.detail()
    again = saved_chat("계속해줘", profile)
    assert again["recipe"] is None and "바나나" in again["answer"]
    assert profile["allergies"] == registered


@pytest.mark.parametrize("text", [
    "바나나 알레르기 증상 알려줘", "바나나 알레르기가 있는 사람은 뭘 확인해야 해?",
    "바나나 알레르기로 고생하는 사람은 뭘 확인해야 해?",
    "바나나 알레르기는 없어", "사과, 바나나 알레르기 원인 알려줘",
])
def test_general_information_is_not_a_personal_list(text):
    parsed = _message_allergens(text)
    assert not parsed.confirmed and not parsed.uncertain


@pytest.mark.parametrize("reply", [
    "매일우유 오리지널 200ml로 레시피 만들어줘",
    "매일우유 오리지널 200ml를 이용한 요리 추천해줘",
    "매일우유 오리지널 200ml 레시피 추천해줘",
])
def test_complete_source_reply_preserves_unfinished_conditions(saved_chat, reply):
    first = saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    assert first["intent"] == "missing_product"
    saved_chat.detail()
    response = saved_chat(reply, ME)
    if response["product_candidates"]:
        assert stored_state(response)["pending_allergy"]["confirmed"] == ["우유"]
        saved_chat.detail()
        response = saved_chat("1번으로 할게", ME)
    assert response["allergy_status"] == "conflict"
    assert_blocked(response)
    assert "저염식" in stored_state(response)["recipe_requested_diets"]
    assert "pending_allergy" not in stored_state(response)
    assert saved_chat.detail()["messages"][-1]["response"] == response
    independent = saved_chat("이 제품 먹어도 돼?", ME, barcode="P00156")
    assert independent["allergy_status"] == "no_known_match"


def test_repeated_failed_source_reply_keeps_conditions(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    for message in ["또미등록제품 레시피 추천해줘", "여전히미등록제품으로 레시피 만들어줘"]:
        saved_chat.detail()
        response = saved_chat(message, ME)
        assert response["intent"] == "missing_product" and response["recipe"] is None
        assert stored_state(response)["pending_allergy"]["confirmed"] == ["우유"]
        assert "저염식" in stored_state(response)["recipe_requested_diets"]


def test_full_source_reply_does_not_reassign_off_owner(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    off = {**ME, "is_active": False}
    response = saved_chat("매일우유 오리지널 200ml로 레시피 만들어줘", off, profiles=[off, CHILD])
    assert response["recipe"] is None and "어느 프로필" in response["answer"]


@pytest.mark.parametrize("reply", [
    "유제품은 먹어. 레시피 요청은 취소해줘",
    "레시피 요청은 취소해 주세요. 유제품은 먹어요",
    "유제품은 먹어, 레시피 요청 취소할게",
])
def test_compound_cancellation_stops_generation(saved_chat, reply):
    saved_chat("우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", VEG, barcode="P00156")
    saved_chat.detail()
    response = saved_chat(reply, VEG)
    assert response["recipe"] is None and not response["recommended_products"]
    assert "취소했어요" in response["answer"]
    pending = stored_state(response)
    assert not any(key in pending for key in ("pending_allergy", "pending_user_message", "pending_intent", "recipe_product"))
    assert saved_chat.detail()["messages"][-1]["response"] == response


@pytest.mark.parametrize("negation", [
    "취소하지 않을게", "취소하지 않겠습니다", "취소하지 않아", "그만두지 않을게",
])
def test_negated_compound_cancellation_keeps_allergy(saved_chat, negation):
    saved_chat("우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", VEG, barcode="P00156")
    saved_chat.detail()
    response = saved_chat(f"유제품은 먹어. 레시피 요청을 {negation}. 계속해줘", VEG)
    assert response["allergy_status"] == "conflict" and response["overall_decision"] == "avoid"
    assert_blocked(response)
    assert saved_chat.detail()["messages"][-1]["response"] == response


@pytest.mark.parametrize("exclusion", [
    "유제품, 달걀 없이 만들어줄래?", "달걀, 유제품 없이 만들어줄래?",
    "유제품과 달걀은 빼줘", "유제품이랑 달걀 없이 만들어줄래?",
    "유제품과달걀 없이 만들어줄래?",
    "유제품, 달걀은 빼주실 수 있나요?",
])
@pytest.mark.parametrize("profile", [ME, VEG])
def test_coordinated_exclusions_block_milk(saved_chat, exclusion, profile):
    response = saved_chat("이 제품으로 레시피 추천해줘. " + exclusion, profile, barcode="P00156")
    assert stated_vegetarian_allowance(exclusion) == {"dairy": False, "egg": False}
    assert_blocked(response)
    entry = stored_state(response)["vegetarian_allowances"][0]
    assert entry["dairy"] is False and entry["egg"] is False
    saved_chat.detail()
    following = saved_chat("다른 레시피 더 있어?", profile)
    assert_blocked(following)


@pytest.mark.parametrize("text,expected", [
    ("유제품은 먹어, 달걀 없이 만들어줄래?", {"dairy": True, "egg": False}),
    ("유제품 없이 만들어줘, 달걀은 먹어", {"dairy": False, "egg": True}),
    ("유제품, 달걀 빼도 돼?", {}), ("유제품, 달걀 먹어도 돼?", {}),
])
def test_separate_predicates_and_permission_questions(text, expected):
    assert stated_vegetarian_allowance(text) == expected


@pytest.mark.parametrize("prior_product", [False, True])
def test_unresolved_unmarked_source_never_becomes_generic_or_old(saved_chat, prior_product):
    if prior_product:
        saved_chat("이 제품으로 레시피 추천해줘", ME, barcode="P00156")
        saved_chat.detail()
    response = saved_chat("미등록마법제품 레시피 추천해줘", ME)
    assert response["recipe"] is None and response["current_product"] is None
    assert response["intent"] == "missing_product"
    assert "recipe_product" not in stored_state(response)
    assert "last_confirmed_product" not in stored_state(response)
    assert saved_chat.detail()["messages"][-1]["response"] == response
    following = saved_chat("이 제품으로 요리 추천해줘", ME)
    assert following["recipe"] is None
    saved_chat.detail()
    generic = saved_chat("제품 없이 일반 레시피 추천해줘", ME)
    assert generic["recipe"]["product_name"] == "기본 식재료"


@pytest.mark.parametrize("message", [
    "저염 고단백 레시피 추천해줘", "간단한 채식 레시피 추천해줘", "일반 레시피 추천해줘",
    "저염으로 레시피 추천해줘",
])
def test_generic_recipe_is_not_a_missing_catalog_name(message):
    response = chat(ChatRequest(message=message, profile=Profile(**VEG)))
    assert response.recipe is not None and response.recipe.product_name == "기본 식재료"


def test_stateless_condition_and_source_boundaries():
    profile = Profile(**ME)
    first = chat(ChatRequest(message="우유 알레르기는 있어. 미등록마법제품 저염 레시피 추천해줘", profile=profile))
    second = chat(ChatRequest(message="매일우유 오리지널 200ml로 레시피 만들어줘", profile=profile, conversation_state=state(first)))
    if second.product_candidates:
        second = chat(ChatRequest(message="1번으로 할게", profile=profile, conversation_state=state(second)))
    assert second.allergy_status == "conflict"
    assert second.recipe is None or second.recipe.source_product_blocked
    assert "저염식" in state(second)["recipe_requested_diets"]


def test_compound_cancellation_cannot_search_or_generate(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("cancelled requests cannot query products or generate")
    monkeypatch.setattr("app.services.conversation_router.find_product_candidates_from_message", forbidden)
    monkeypatch.setattr("app.services.chat.generate_final_response", forbidden)
    from app.services.chat import generate_chat_response
    text = "유제품은 먹어. 레시피 요청은 취소해줘"
    assert request_is_cancelled(text)
    assert "취소했어요" in chat(ChatRequest(message=text, profile=Profile(**ME))).answer
    assert "취소했어요" in generate_chat_response(Profile(**ME), None, text).answer


@pytest.mark.parametrize("text", [
    "레시피 요청은 취소할게. 저염식이 뭐야?",
    "레시피 요청 취소하고 일반 레시피 추천해줘",
])
def test_cancellation_then_new_task_does_not_resume_old_allergy(saved_chat, text):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat(text, ME)
    assert "pending_allergy" not in stored_state(response)
    if "일반 레시피" in text:
        assert response["recipe"]["product_name"] == "기본 식재료"
        assert response["recipe"]["requested_diets"] == []
    else:
        assert response["intent"] == "general_diet_guide"
    assert saved_chat.detail()["messages"][-1]["response"] == response


@pytest.mark.parametrize("off", [False, True])
def test_coordinated_exclusions_do_not_mix_profile_owners(saved_chat, off):
    child = {**CHILD, "is_active": not off}
    response = saved_chat("나는 유제품을 빼줘, 아이는 달걀을 빼줘. 이 제품으로 레시피 추천해줘",
                          ME, profiles=[ME, child], barcode="P00156")
    assert response["recipe"] is None
    assert not stored_state(response).get("vegetarian_allowances")


def test_list_parsing_does_not_backtrack_exponentially():
    from time import perf_counter
    start = perf_counter()
    # Previously even 20 known nouns followed by a different sentence took
    # more than two seconds; hundreds of nouns must remain a bounded scan.
    parsed = _message_allergens("새우, " * 300 + "우유는 괜찮아. 바나나 알레르기가 있어")
    assert parsed.confirmed == {"바나나"}
    assert perf_counter() - start < 2.0
    assert not _message_allergens("새우, " * 300 + "레시피 알려줘").confirmed
