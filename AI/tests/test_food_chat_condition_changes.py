"""Request/condition changes from the 1,482-test review (no live providers)."""

import pytest

from app.core.models import ChatRequest, Profile
from app.main import chat
from app.services.message_constraint_guard import _message_allergens
from app.services.diet_conversation_context import stated_vegetarian_allowance
from test_food_chat_condition_lifecycle import saved_chat, state
from test_food_chat_pending_conditions import stored_state


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}


def finish_selection(send, response, profile=ME, **extra):
    assert send.detail()["messages"][-1]["response"] == response
    if response["product_candidates"]:
        response = send("1번으로 할게", profile, **extra)
    assert send.detail()["messages"][-1]["response"] == response
    return response


def blocked(response):
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("stop", ["확인 질문만 취소하고", "확인만 그만하고", "확인 질문만은 취소하고", "제품 검색만 취소하고", "질문만 취소하고"])
def test_partial_stop_never_withdraws_pending_allergy(saved_chat, stop):
    saved_chat("우유 알레르기가 있는지 모르겠어. 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    saved_chat.detail()
    r = saved_chat(stop + " 이 제품으로 레시피 만들어줘", ME, barcode="P00156")
    assert r["recipe"] is None
    assert stored_state(r)["pending_allergy"]["uncertain"] == ["우유"]
    assert saved_chat.detail()["messages"][-1]["response"] == r


@pytest.mark.parametrize("condition", ["우유 알레르기는 있어", "유제품은 빼줘", "바나나 알레르기는 있어"])
def test_search_stop_collects_new_conditions_before_acknowledgement(saved_chat, condition):
    saved_chat("미등록마법제품으로 레시피 추천해줘", ME)
    saved_chat.detail()
    stopped = saved_chat(condition + ". 제품 검색 취소해줘", ME)
    assert stopped["recipe"] is None and not stopped["product_candidates"]
    s = stored_state(stopped)
    if "유제품" in condition:
        assert s["vegetarian_allowances"][0]["dairy"] is False
    else:
        assert s["pending_allergy"]
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME))
    if "바나나" in condition:
        assert r["recipe"] is None and "바나나" in r["answer"]
    else:
        blocked(r)


@pytest.mark.parametrize("word", ["우유", "바나나"])
@pytest.mark.parametrize("predicate", ["증상이 있어", "증상이 나타나", "가 없지 않아", "가 없다고는 못 해", "가 없다고 할 수는 없어"])
def test_personal_or_uncertain_allergy_never_becomes_absent(saved_chat, word, predicate):
    phrase = word + " 알레르기" + (" " if not predicate.startswith("가") else "") + predicate
    parsed = _message_allergens(phrase)
    assert word in parsed.confirmed | parsed.uncertain
    assert word not in parsed.denied
    r = saved_chat(phrase + ". 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    blocked(r)
    if word == "바나나" or "없" in predicate:
        assert r["recipe"] is None
    assert saved_chat.detail()["messages"][-1]["response"] == r


@pytest.mark.parametrize("word", ["우유", "바나나"])
@pytest.mark.parametrize("question", ["원인 알려줘", "증상 알려줘", "증상이 뭐야?", "에 대해 알려줘"])
def test_information_query_does_not_assign_personal_allergy(word, question):
    message = word + " 알레르기 " + question
    parsed = _message_allergens(message)
    assert not parsed.confirmed and not parsed.uncertain
    r = chat(ChatRequest(message=message, profile=Profile(**ME)))
    assert r.ui_guidance is None
    assert "임시로 반영" not in r.answer


@pytest.mark.parametrize("condition", ["유제품을 넣지 말아줘", "유제품은 사용하지 말아주세요", "유제품을 넣지 말아줄래?", "유제품과 달걀은 사용하지 마"])
def test_negative_ingredient_action_blocks_milk(saved_chat, condition):
    assert stated_vegetarian_allowance(condition)["dairy"] is False
    r = saved_chat(condition + ". 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    blocked(r)
    assert stored_state(r)["vegetarian_allowances"][0]["dairy"] is False
    saved_chat.detail()
    blocked(saved_chat("다른 레시피 더 있어?", ME))


def test_comparison_checks_exclusions_without_vegetarian_identity(saved_chat):
    saved_chat("유제품은 빼줘. 이 제품 먹어도 돼?", ME, barcode="P00156")
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat("매일우유 오리지널 200ml과 매일 아몬드브리즈 언스위트 비교해줘", ME))
    for message in [None, "나트륨으로 비교해줘"]:
        if message:
            r = saved_chat(message, ME)
        assert len(r["comparison_products"]) == 2
        assert r["diet_match"] == "not_match" and r["allergy_status"] == "no_known_match"
        assert r["overall_decision"] == "caution" and "유제품" in r["answer"]
        assert r["per_profile_results"][0]["diet_match"] == "not_match"
        assert saved_chat.detail()["messages"][-1]["response"] == r


@pytest.mark.parametrize("source", ["매일우유 오리지널 200ml", "또미등록제품"])
@pytest.mark.parametrize("change", ["저염 말고 고단백", "저염은 빼고 고단백"])
def test_source_and_goal_change_preserve_unfinished_allergy(saved_chat, source, change):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat(f"{source}로 {change} 레시피 만들어줘", ME))
    assert stored_state(r)["recipe_requested_diets"] == ["고단백"]
    if source == "또미등록제품":
        assert r["intent"] == "missing_product" and r["recipe"] is None
        assert stored_state(r)["pending_allergy"]["confirmed"] == ["우유"]
        r = finish_selection(saved_chat, saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME))
    assert r["allergy_status"] == "conflict"
    blocked(r)
    assert stored_state(r)["recipe_requested_diets"] == ["고단백"]


@pytest.mark.parametrize("prefix", ["저염은 빼고 고단백으로 해줘", "고단백만 해줘"])
def test_goal_withdrawal_replaces_pending_goal(saved_chat, prefix):
    saved_chat("미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat(prefix + ". 매일우유 오리지널 200ml 레시피 있어?", ME))
    assert stored_state(r)["recipe_requested_diets"] == ["고단백"]
    assert r["recipe"] is not None and r["recipe"]["requested_diets"] == ["고단백"]


@pytest.mark.parametrize("cancel", ["이전 요청은 취소할게", "이전 제품 검색은 취소할게"])
def test_current_goals_before_cancellation_survive(saved_chat, cancel):
    r = saved_chat("저염 고단백으로 해줘. " + cancel + ". 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    assert set(stored_state(r)["recipe_requested_diets"]) == {"저염식", "고단백"}
    assert set(r["recipe"]["requested_diets"]) == {"저염식", "고단백"}
    assert saved_chat.detail()["messages"][-1]["response"] == r


@pytest.mark.parametrize("message,expected", [
    ("저염은 빼고 고단백으로 해줘", ["고단백"]),
    ("저염 대신 고단백으로 해줘", ["고단백"]),
    ("저염 조건은 취소하고 고단백으로 해줘", ["고단백"]),
    ("저염 목표만 취소할게요. 고단백으로 해줘", ["고단백"]),
    ("고단백도 추가해줘", ["저염식", "고단백"]),
    ("고단백만 해줘", ["고단백"]),
    ("저염은 필요 없고 고단백만 해줘", ["고단백"]),
    ("저염은 빼지 말아줘", ["저염식"]),
    ("식이 목표는 초기화하지 마", ["저염식"]),
    ("식이 목표는 초기화해줘", []),
])
def test_goal_only_updates_resume_source_and_keep_allergy(saved_chat, message, expected):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    saved_chat.detail()
    updated = saved_chat(message, ME)
    assert updated["intent"] == "missing_product" and updated["recipe"] is None
    assert stored_state(updated)["pending_allergy"]["confirmed"] == ["우유"]
    assert set(stored_state(updated)["recipe_requested_diets"]) == set(expected)
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME))
    assert r["allergy_status"] == "conflict"
    assert set(stored_state(r)["recipe_requested_diets"]) == set(expected)
    blocked(r)


@pytest.mark.parametrize("message,expected", [
    ("저염, 저당 말고 고단백 레시피", ["고단백"]),
    ("저염 그리고 저당은 빼고 고단백으로", ["고단백"]),
    ("고단백만 해줘. 저당도 추가해줘", ["고단백", "저당식"]),
    ("저염은 빼지 말아줘", ["저염식"]),
    ("저염 조건 취소하지 말고 계속해줘", ["저염식"]),
    ("저염은 빼줘. 저염으로 다시 해줘", ["저염식"]),
    ("저염으로 해줘. 식이 목표는 초기화해줘", []),
    ("식이 목표는 초기화하고 고단백으로 해줘", ["고단백"]),
])
def test_goal_update_operations_are_ordered_and_scoped(message, expected):
    from app.services.diet_goal_context import read_diet_goal_update
    assert set(read_diet_goal_update(message).apply()) == set(expected)


@pytest.mark.parametrize("diet", ["저염식", "저당식", "고단백", "글루텐프리"])
def test_goal_withdrawal_never_removes_account_restriction(diet):
    from app.services.response_helpers import resolve_profile_diets
    p = Profile(**ME, special_diets=[diet])
    assert diet in resolve_profile_diets(p, f"{diet}은 빼줘")
    assert p.resolved_special_diets() == [diet]


@pytest.mark.parametrize("phrase", ["증상을 겪고 있어", "증상이 없지 않아"])
def test_symptom_report_is_not_an_information_query(phrase):
    assert "바나나" in _message_allergens("바나나 알레르기 " + phrase).uncertain


@pytest.mark.parametrize("message", ["유제품은 넣어도 돼?", "유제품을 사용하지 않아도 돼?", "유제품을 넣지 말아도 돼?"])
def test_ingredient_permission_question_is_not_exclusion(message):
    assert stated_vegetarian_allowance(message) == {}


def test_search_stop_retains_new_allergy_owner_without_reassignment(saved_chat):
    saved_chat("미등록마법제품으로 레시피 추천해줘", ME, profiles=[ME, CHILD])
    saved_chat.detail()
    r = saved_chat("아이 우유 알레르기는 있어. 제품 검색만 취소해줘", ME, profiles=[ME, CHILD])
    assert stored_state(r)["pending_allergy"]["owner_key"] == "id:2"
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME, profiles=[ME, CHILD]), profiles=[ME, CHILD])
    by_name = {p["profile_name"]: p["allergy_status"] for p in r["per_profile_results"]}
    assert by_name == {"나": "no_known_match", "아이": "conflict"}
    assert ME["allergies"] == CHILD["allergies"] == []


def test_search_stop_does_not_assign_off_owner_to_remaining_profile(saved_chat):
    off = {**CHILD, "is_active": False}
    saved_chat("미등록마법제품으로 레시피 추천해줘", ME, profiles=[ME, off])
    saved_chat.detail()
    stopped = saved_chat("아이 우유 알레르기는 있어. 제품 검색 취소해줘", ME, profiles=[ME, off])
    assert not stored_state(stopped)["pending_allergy"].get("owner_key")
    saved_chat.detail()
    r = saved_chat("매일우유 오리지널 200ml 레시피 있어?", ME, profiles=[ME, off])
    assert r["recipe"] is None and "어느 프로필" in r["answer"]


@pytest.mark.parametrize("condition", ["우유 알레르기는 있어", "유제품은 빼줘", "고단백도 추가해줘"])
def test_search_stop_parses_conditions_without_search_or_generation(monkeypatch, condition):
    def forbidden(*args, **kwargs):
        raise AssertionError("stopping search must never search or generate")
    monkeypatch.setattr("app.services.conversation_router.find_product_candidates_from_message", forbidden)
    monkeypatch.setattr("app.services.chat.generate_final_response", forbidden)
    previous = {"last_intent": "missing_product", "pending_intent": "recipe_recommend",
                "pending_user_message": "미등록마법제품으로 저염 레시피 추천해줘",
                "recipe_requested_diets": ["저염식"], "recipe_goals_initialized": True}
    r = chat(ChatRequest(message=condition + ". 제품 검색 취소해줘", profile=Profile(**ME), conversation_state=previous))
    assert r.recipe is None
    s = state(r)
    if "알레르기" in condition:
        assert s["pending_allergy"]["confirmed"] == ["우유"]
    elif "유제품" in condition:
        assert s["vegetarian_allowances"][0]["dairy"] is False
    else:
        assert set(s["recipe_requested_diets"]) == {"저염식", "고단백"}


def test_stateless_source_change_keeps_conditions():
    p = Profile(**ME)
    first = chat(ChatRequest(message="우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", profile=p))
    second = chat(ChatRequest(message="매일우유 오리지널 200ml로 저염 대신 고단백 레시피 만들어줘", profile=p, conversation_state=state(first)))
    if second.product_candidates:
        second = chat(ChatRequest(message="1번으로 할게", profile=p, conversation_state=state(second)))
    assert second.allergy_status == "conflict"
    assert state(second)["recipe_requested_diets"] == ["고단백"]


def test_direct_service_keeps_goals_before_cancel_and_stopped_search():
    from app.services.chat import generate_chat_response
    from app.services.product import find_product_by_barcode
    r = generate_chat_response(Profile(**ME), find_product_by_barcode("P00156"),
        "저염 고단백으로 해줘. 이전 요청은 취소할게. 이 제품으로 레시피 추천해줘")
    assert set(r.recipe.requested_diets) == {"저염식", "고단백"}
    r = generate_chat_response(Profile(**ME), None, "우유 알레르기는 있어. 제품 검색 취소해줘",
        conversation_state={"last_intent": "missing_product", "pending_intent": "recipe_recommend",
                            "pending_user_message": "미등록마법제품으로 레시피 추천해줘"})
    assert state(r)["pending_allergy"]["confirmed"] == ["우유"]


@pytest.mark.parametrize("stop", ["확인 질문만 취소해줘", "질문만 취소해줘", "확인만 취소해주세요"])
def test_standalone_clarification_stop_does_not_deny_allergy(saved_chat, stop):
    saved_chat("우유 알레르기가 있는지 모르겠어. 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    saved_chat.detail()
    r = saved_chat(stop, ME)
    assert r["recipe"] is None and stored_state(r)["pending_allergy"]["uncertain"] == ["우유"]
    saved_chat.detail()
    r = saved_chat("계속해줘", ME)
    assert r["recipe"] is None and stored_state(r)["pending_allergy"]["uncertain"] == ["우유"]


@pytest.mark.parametrize("withdrawal", ["저염 목표는 해제해줘", "저염 조건 없이", "저염 목표만 초기화해줘"])
def test_named_goal_removal_keeps_other_goals(saved_chat, withdrawal):
    from app.services.diet_goal_context import read_diet_goal_update
    assert read_diet_goal_update(withdrawal).apply(["저염식", "고단백"]) == ["고단백"]
    saved_chat("미등록마법제품으로 저염 고단백 레시피 추천해줘", ME)
    saved_chat.detail()
    r = finish_selection(saved_chat, saved_chat(withdrawal + ". 매일우유 오리지널 200ml 레시피 있어?", ME))
    assert r["recipe"]["requested_diets"] == ["고단백"]
    assert stored_state(r)["recipe_requested_diets"] == ["고단백"]
