"""Condition preservation and request updates from the 1,351-test review."""

import pytest

from app.core.models import ChatRequest, Profile
from app.main import chat
from app.services.diet_conversation_context import stated_vegetarian_allowance
from app.services.message_constraint_guard import _message_allergens
from app.services.product_search import recipe_source_query
from test_food_chat_condition_lifecycle import saved_chat, state
from test_food_chat_pending_conditions import stored_state


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}


def blocked(response):
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("list_text", ["새우 그리고 땅콩", "땅콩 그리고 새우", "새우, 우유 그리고 땅콩"])
def test_and_allergy_list_keeps_every_item(saved_chat, list_text):
    response = saved_chat(list_text + " 알레르기가 있어. 이 제품으로 레시피 추천해줘", ME, barcode="8801043035989")
    assert response["allergy_status"] == "conflict" and response["overall_decision"] == "avoid"
    blocked(response)
    assert saved_chat.detail()["messages"][-1]["response"] == response
    assert ME["allergies"] == []


@pytest.mark.parametrize("text", [
    "유제품 그리고 달걀 없이 만들어줄래?", "달걀 그리고 유제품은 빼줘",
    "유제품, 달걀 그리고 우유 없이 만들어줄래?",
])
def test_and_exclusion_list_blocks_milk(saved_chat, text):
    response = saved_chat("이 제품으로 레시피 추천해줘. " + text, ME, barcode="P00156")
    entry = stored_state(response)["vegetarian_allowances"][0]
    assert entry["dairy"] is False and entry["egg"] is False
    blocked(response)
    saved_chat.detail()
    blocked(saved_chat("다른 레시피 더 있어?", ME))


@pytest.mark.parametrize("text,confirmed,denied", [
    ("새우 알레르기는 없어 그리고 땅콩 알레르기는 있어", {"땅콩"}, {"새우"}),
    ("새우 그리고 땅콩 알레르기는 없어", set(), {"새우", "땅콩"}),
    ("새우 알레르기는 있어 그리고 땅콩 알레르기는 없어", {"새우"}, {"땅콩"}),
])
def test_and_does_not_join_separate_allergy_predicates(text, confirmed, denied):
    parsed = _message_allergens(text)
    assert parsed.confirmed == confirmed and parsed.denied == denied


@pytest.mark.parametrize("text,expected", [
    ("유제품은 먹어 그리고 달걀은 빼줘", {"dairy": True, "egg": False}),
    ("유제품은 빼줘 그리고 달걀은 먹어", {"dairy": False, "egg": True}),
    ("유제품 그리고 달걀 먹어도 돼?", {}),
])
def test_and_keeps_separate_diet_predicates_and_questions(text, expected):
    assert stated_vegetarian_allowance(text) == expected


@pytest.mark.parametrize("off", [False, True])
def test_and_does_not_merge_profile_owners(saved_chat, off):
    child = {**CHILD, "is_active": not off}
    response = saved_chat("나는 새우 알레르기가 있어 그리고 아이는 땅콩 알레르기가 있어. 이 제품으로 레시피 추천해줘",
                          ME, profiles=[ME, child], barcode="8801043035989")
    assert response["recipe"] is None and "어느 프로필" in response["answer"]


@pytest.mark.parametrize("text", [
    "바나나 알레르기를 가지고 있어", "바나나 알레르기를 갖고 있어",
    "바나나 알레르기로 고생하는 사람이야", "바나나 알레르기로 고생하는 사람이에요",
    "바나나 알레르기를 가지고 있는 사람이야",
    "바나나 알레르기로 불편함을 겪고 있어", "바나나 알레르기가 생겼어",
])
@pytest.mark.parametrize("registered", [[], ["새우"]])
def test_unsupported_personal_statement_never_disappears(saved_chat, text, registered):
    profile = {**ME, "allergies": registered}
    response = saved_chat(text + ". 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert response["recipe"] is None and not response["recommended_products"]
    pending = stored_state(response)["pending_allergy"]
    assert "바나나" in pending.get("unsupported_confirmed", []) + pending.get("unsupported_uncertain", [])
    saved_chat.detail()
    following = saved_chat("계속해줘", profile)
    assert following["recipe"] is None and "바나나" in following["answer"]
    assert profile["allergies"] == registered


@pytest.mark.parametrize("text", [
    "바나나 알레르기로 고생하는 사람은 뭘 확인해야 해?",
    "바나나 알레르기를 가지고 있는 사람이 먹어도 돼?",
    "바나나 알레르기로 고생하는 사람은 아니야",
    "바나나 알레르기 원인 알려줘",
    "바나나 알레르기에 대해 알려줘", "바나나 알레르기를 갖고 있는 사람은 뭘 확인해야 해?",
])
def test_general_person_or_denial_is_not_a_personal_condition(text):
    parsed = _message_allergens(text)
    assert not parsed.confirmed and not parsed.uncertain


@pytest.mark.parametrize("text", [
    "바나나 알레르기로 고생하는 사람이야?", "바나나 알레르기를 가지고 있는 사람인지 모르겠어",
])
def test_uncertain_personal_identity_stays_uncertain(text):
    assert _message_allergens(text).uncertain == {"바나나"}


@pytest.mark.parametrize("ending", ["레시피 있어?", "레시피 있나요?", "레시피가 있을까?", "요리 가능해?", "레시피 추천해줘"])
def test_recipe_source_identity_does_not_depend_on_request_verb(ending):
    assert recipe_source_query("미등록마법제품 " + ending) == "미등록마법제품"


@pytest.mark.parametrize("ending", ["레시피 있어?", "레시피 있나요?", "레시피가 있을까?", "요리 가능해?"])
def test_question_style_source_reply_keeps_allergy_and_goals(saved_chat, ending):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat("매일우유 오리지널 200ml " + ending, ME)
    if response["product_candidates"]:
        assert stored_state(response)["pending_allergy"]["confirmed"] == ["우유"]
        saved_chat.detail()
        response = saved_chat("1번으로 할게", ME)
    assert response["allergy_status"] == "conflict"
    blocked(response)
    assert "저염식" in stored_state(response)["recipe_requested_diets"]
    assert saved_chat.detail()["messages"][-1]["response"] == response
    next_request = saved_chat("이 제품 먹어도 돼?", ME, barcode="P00156")
    assert next_request["allergy_status"] == "no_known_match"


@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("ending", ["레시피 있어?", "레시피가 있을까?", "요리 가능해?"])
def test_unknown_question_style_source_cannot_use_old_or_generic_product(saved_chat, prior, ending):
    if prior:
        saved_chat("이 제품으로 레시피 추천해줘", ME, barcode="P00156")
        saved_chat.detail()
    response = saved_chat("미등록마법제품 " + ending, ME)
    assert response["recipe"] is None and response["current_product"] is None
    assert response["intent"] == "missing_product"
    pending = stored_state(response)
    assert "recipe_product" not in pending and "last_confirmed_product" not in pending
    assert saved_chat.detail()["messages"][-1]["response"] == response


@pytest.mark.parametrize("text", ["저염 레시피 있어?", "간단한 레시피 있나요?", "고단백 레시피가 있을까?", "일반 레시피 추천해줘"])
def test_generic_question_does_not_require_a_catalog_product(text):
    assert not recipe_source_query(text)
    response = chat(ChatRequest(message=text, profile=Profile(**ME)))
    assert response.recipe is not None and response.recipe.product_name == "기본 식재료"


@pytest.mark.parametrize("certainty", ["있어", "있는지 모르겠어"])
@pytest.mark.parametrize("stop", ["확인 질문은 취소하고", "확인 질문은 그만하고", "질문 그만하고"])
def test_stopping_clarification_never_resolves_allergy(saved_chat, certainty, stop):
    if certainty == "있어":
        saved_chat("우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", ME)
    else:
        saved_chat(f"우유 알레르기가 {certainty}. 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    saved_chat.detail()
    response = saved_chat(stop + " 이 제품으로 레시피 만들어줘", ME, barcode="P00156")
    if certainty == "있어":
        assert response["allergy_status"] == "conflict"
        blocked(response)
    else:
        assert response["recipe"] is None
        assert stored_state(response)["pending_allergy"]["uncertain"] == ["우유"]
    assert saved_chat.detail()["messages"][-1]["response"] == response


@pytest.mark.parametrize("cancel", ["이전 요청은 취소할게", "이전 제품 검색은 취소할게"])
@pytest.mark.parametrize("condition", ["유제품은 빼줘", "우유 알레르기는 있어"])
def test_cancellation_keeps_conditions_from_current_utterance(saved_chat, cancel, condition):
    response = saved_chat(f"{condition}. {cancel}. 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    blocked(response)
    if "알레르기" in condition:
        assert response["allergy_status"] == "conflict"
    else:
        assert stored_state(response)["vegetarian_allowances"][0]["dairy"] is False
    assert saved_chat.detail()["messages"][-1]["response"] == response


def test_latest_goals_survive_repeated_failed_source_lookup(saved_chat):
    saved_chat("미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat("또미등록제품으로 저염 고단백 레시피 만들어줘", ME)
    assert set(stored_state(response)["recipe_requested_diets"]) == {"저염식", "고단백"}
    assert "또미등록제품" in stored_state(response)["pending_user_message"]
    saved_chat.detail()
    again = saved_chat("계속해줘", ME)
    assert set(stored_state(again)["recipe_requested_diets"]) == {"저염식", "고단백"}
    saved_chat.detail()
    result = saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME)
    if result["product_candidates"]:
        saved_chat.detail()
        result = saved_chat("1번으로 할게", ME)
    assert result["recipe"] is not None
    assert set(result["recipe"]["requested_diets"]) == {"저염식", "고단백"}
    assert saved_chat.detail()["messages"][-1]["response"] == result


@pytest.mark.parametrize("initial", [
    "우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘",
    "우유 알레르기는 있어. 서울우유로 저염 레시피 추천해줘",
])
def test_search_cancellation_keeps_request_conditions_but_clears_candidates(saved_chat, initial):
    saved_chat(initial, ME)
    saved_chat.detail()
    stopped = saved_chat("제품 검색 취소해줘", ME)
    assert stopped["recipe"] is None and not stopped["product_candidates"]
    pending = stored_state(stopped)
    assert pending["pending_allergy"]["confirmed"] == ["우유"]
    assert not pending.get("pending_product_candidates")
    assert "recipe_product" not in pending and "last_confirmed_product" not in pending
    saved_chat.detail()
    response = saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME)
    if response["product_candidates"]:
        saved_chat.detail()
        response = saved_chat("1번으로 할게", ME)
    assert response["allergy_status"] == "conflict"
    blocked(response)
    assert "저염식" in stored_state(response)["recipe_requested_diets"]


@pytest.mark.parametrize("stop", ["이전 제품 검색은 취소하고", "검색 그만하고"])
def test_partial_search_cancellation_can_replace_source_in_same_message(saved_chat, stop):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat(stop + " 매일우유 오리지널 200ml 레시피 있어?", ME)
    if response["product_candidates"]:
        saved_chat.detail()
        response = saved_chat("1번으로 할게", ME)
    assert response["allergy_status"] == "conflict"
    blocked(response)


def test_full_cancellation_still_ends_temporary_allergy(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", ME)
    saved_chat.detail()
    stopped = saved_chat("레시피 요청은 취소해줘", ME)
    assert stopped["recipe"] is None and "pending_allergy" not in stored_state(stopped)
    saved_chat.detail()
    response = saved_chat("이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    assert response["allergy_status"] == "no_known_match"
    assert response["recipe"] is not None and not response["recipe"]["source_product_blocked"]


def test_source_update_does_not_move_an_off_profiles_allergy(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    off = {**ME, "is_active": False}
    response = saved_chat("매일우유 오리지널 200ml 레시피 있어?", off, profiles=[off, CHILD])
    assert response["recipe"] is None and "어느 프로필" in response["answer"]


@pytest.mark.parametrize("condition", ["아이 우유 알레르기는 있어", "아이 유제품은 빼줘"])
def test_current_condition_owner_survives_cancellation_rewrite(saved_chat, condition):
    response = saved_chat(condition + ". 이전 요청은 취소할게. 이 제품으로 레시피 추천해줘",
                          ME, profiles=[ME, CHILD], barcode="P00156")
    blocked(response)
    if "알레르기" in condition:
        assert response["allergy_status"] == "conflict"
        assert "아이" in response["answer"]
    else:
        entries = stored_state(response)["vegetarian_allowances"]
        assert any(item["profile_key"] == "id:2" and item["dairy"] is False for item in entries)
        assert not any(item["profile_key"] == "id:1" for item in entries)


def test_stateless_source_question_round_trip():
    profile = Profile(**ME)
    first = chat(ChatRequest(message="우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", profile=profile))
    second = chat(ChatRequest(message="매일우유 오리지널 200ml 레시피 있어?", profile=profile, conversation_state=state(first)))
    if second.product_candidates:
        second = chat(ChatRequest(message="1번으로 할게", profile=profile, conversation_state=state(second)))
    assert second.allergy_status == "conflict"
    assert second.recipe is None or second.recipe.source_product_blocked
    assert "저염식" in state(second)["recipe_requested_diets"]


@pytest.mark.parametrize("target", ["이전 요청", "이전 제품 검색"])
def test_direct_service_preserves_current_exclusion_when_rewriting_task(target):
    from app.services.chat import generate_chat_response
    from app.services.product import find_product_by_barcode
    response = generate_chat_response(Profile(**ME), find_product_by_barcode("P00156"),
                                      f"유제품은 빼줘. {target}은 취소할게. 이 제품으로 레시피 추천해줘")
    assert response.recipe is None or response.recipe.source_product_blocked
    assert state(response)["vegetarian_allowances"][0]["dairy"] is False


def test_standalone_search_cancellation_never_queries_catalog(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("search cancellation must not query or generate")
    monkeypatch.setattr("app.services.conversation_router.find_product_candidates_from_message", forbidden)
    monkeypatch.setattr("app.services.chat.generate_final_response", forbidden)
    response = chat(ChatRequest(message="제품 검색 취소해줘", profile=Profile(**ME)))
    assert response.recipe is None and "검색을 중단" in response.answer


def test_partial_clarification_cancellation_is_not_a_product_name(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat("확인 질문은 취소하고 이 제품으로 레시피 만들어줘", ME)
    pending = stored_state(response)
    assert response["recipe"] is None and response["intent"] == "missing_product"
    assert pending["pending_allergy"]["confirmed"] == ["우유"]
    assert "미등록마법제품" in pending["pending_user_message"]
    assert "취소" not in pending["pending_user_message"]
    saved_chat.detail()
    resolved = saved_chat("이거야", ME, barcode="P00156")
    assert resolved["allergy_status"] == "conflict"
    blocked(resolved)
