"""Six clarification/exclusion regressions, including saved-store reopening."""

import pytest

from app.core.models import ChatRequest, Product, Profile
from app.main import chat
from app.services.chat import generate_chat_response
from app.services.diet_conversation_context import apply_vegetarian_context, stated_vegetarian_allowance
from app.services.response_helpers import evaluate_special_diets
from test_food_chat_condition_lifecycle import saved_chat, state


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}
UNCERTAIN = "땅콩 알레르기는 있고 우유 알레르기가 있는지는 모르겠어. "


def stored_state(response):
    from app.services.conversations import get_conversation_repository
    return get_conversation_repository().get(101, response["conversation_id"]).state


@pytest.mark.parametrize("question", [
    "이 제품으로 만들어 먹을 요리 알려줘", "이 제품 어때?", "이 제품 먹을까?",
])
def test_uncertainty_guard_is_not_a_separate_request_keyword_list(saved_chat, question):
    response = saved_chat(UNCERTAIN + question, ME, barcode="P00156")
    assert response["recipe"] is None and not response["recommended_products"]
    assert response["overall_decision"] == "check_required"
    assert "우유" in response["answer"] and "확인" in response["answer"]
    assert stored_state(response)["pending_allergy"]["uncertain"] == ["우유"]
    assert saved_chat.detail()["messages"][-1]["response"]["recipe"] is None


@pytest.mark.parametrize("reply", ["아이 거야", "아이 거야. 이 제품 먹어도 돼?", "아이"])
def test_owner_answer_resumes_original_request_and_is_consumed_once(saved_chat, reply):
    first = saved_chat("우유 알레르기도 있는데 이 제품 먹어도 돼?", ME,
                       profiles=[ME, CHILD], barcode="P00156")
    assert stored_state(first)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    response = saved_chat(reply, ME, profiles=[ME, CHILD])
    assert response["overall_decision"] == "avoid"
    by_name = {p["profile_name"]: p for p in response["per_profile_results"]}
    assert by_name["아이"]["allergy_status"] == "conflict"
    assert by_name["나"]["allergy_status"] == "no_known_match"
    assert response["ui_guidance"]["profile_id"] == 2
    assert "pending_allergy" not in stored_state(response)
    saved_chat.detail()
    next_turn = saved_chat("이 제품 먹어도 돼?", ME, profiles=[ME, CHILD])
    assert next_turn["allergy_status"] == "no_known_match"
    assert ME["allergies"] == CHILD["allergies"] == []


def test_stateless_owner_and_certainty_answers_round_trip():
    profiles = [Profile(**p) for p in (ME, CHILD)]
    first = chat(ChatRequest(message=UNCERTAIN + "이 제품 먹어도 돼?", profiles=profiles, barcode="P00156"))
    second = chat(ChatRequest(message="아이 거야", profiles=profiles, conversation_state=state(first)))
    assert second.overall_decision == "check_required"
    assert second.conversation_state.pending_allergy.owner_key == "id:2"
    third = chat(ChatRequest(message="우유 알레르기도 있어", profiles=profiles, conversation_state=state(second)))
    assert third.overall_decision == "avoid"
    assert next(p for p in third.per_profile_results if p.profile_id == 2).allergy_status == "conflict"
    assert third.conversation_state.pending_allergy is None


def test_off_owner_does_not_reassign_allergy_to_the_remaining_profile(saved_chat):
    saved_chat("우유 알레르기도 있는데 이 제품 먹어도 돼?", ME,
               profiles=[ME, CHILD], barcode="P00156")
    saved_chat.detail()
    response = saved_chat("아이 거야", ME, profiles=[ME, {**CHILD, "is_active": False}])
    assert response["overall_decision"] == "check_required"
    assert response["allergy_status"] != "conflict"
    assert "owner_key" not in stored_state(response)["pending_allergy"]


@pytest.mark.parametrize("question", ["이 제품 먹어도 될까요?", "이 제품으로 레시피 추천해줄래?",
                                       "이 제품으로 만들어 먹을 요리 알려주세요."])
def test_polite_same_product_question_keeps_registered_conflict(saved_chat, question):
    me = {**ME, "allergies": ["새우"]}
    saved_chat("이 제품 먹어도 돼?", me, profiles=[me, CHILD], barcode="8801043035989")
    saved_chat.detail()
    response = saved_chat("땅콩 알레르기도 있는데 " + question, me, profiles=[me, CHILD])
    assert response["current_product"]["barcode"] == "8801043035989"
    assert response["overall_decision"] == "avoid"
    assert "새우" in response["answer"] and "어느 프로필" in response["answer"]
    assert stored_state(response)["last_confirmed_product"]["barcode"] == "8801043035989"


@pytest.mark.parametrize("vegetarian", [False, True])
@pytest.mark.parametrize("message", [
    "유제품은 안 먹는데 이 제품으로 레시피 만들어줄래?",
    "유제품은 빼고 이 제품으로 레시피 추천해줄래?",
])
def test_declarative_exclusion_survives_question_ending(saved_chat, vegetarian, message):
    profile = {**ME, "special_diets": ["채식"] if vegetarian else [],
               "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True}
    response = saved_chat(message, profile, barcode="P00156")
    assert stored_state(response)["vegetarian_allowances"][0]["dairy"] is False
    assert response["recipe"] is not None
    assert response["recipe"]["source_product_blocked"]
    assert all("매일우유" not in p["name"] for p in response["recipe"]["ingredients"])
    assert response["allergy_status"] == "no_known_match"
    saved_chat.detail()
    following = saved_chat("다른 레시피 추천해줘", profile)
    assert following["recipe"] is None or following["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("vegetarian", [False, True])
def test_allergy_clarification_preserves_independent_dairy_exclusion(saved_chat, vegetarian):
    profile = {**ME, "special_diets": ["채식"] if vegetarian else [],
               "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True}
    first = saved_chat("유제품은 빼줘. " + UNCERTAIN + "이 제품 레시피 추천해줘", profile, barcode="P00156")
    assert first["recipe"] is None
    assert stored_state(first)["vegetarian_allowances"][0]["dairy"] is False
    saved_chat.detail()
    response = saved_chat("우유 알레르기는 없어. 다른 레시피 추천해줘", profile)
    assert stored_state(response)["vegetarian_allowances"][0]["dairy"] is False
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]
    assert response["overall_decision"] != "no_known_conflict"
    assert response["allergy_status"] != "conflict"  # Exclusion is not an allergy.


@pytest.mark.parametrize("field,word,ingredient", [("dairy", "유제품", "우유"), ("egg", "달걀", "난백")])
def test_nonvegetarian_exclusions_share_recipe_and_product_evaluator(field, word, ingredient):
    original = Profile(**ME)
    effective, entries, _ = apply_vegetarian_context([original], f"{word}은 빼줘.", {})
    product = Product(product_name="검사 식품", rawmtrl_nm=ingredient, facts_verified=True)
    assert evaluate_special_diets(effective[0], product, "")[0]
    assert not original.resolved_special_diets() and not original.resolved_allergies()
    assert not original._conversation_exclusions
    assert effective[0]._conversation_exclusions == (field,)
    assert not Profile.model_validate({**ME, "_conversation_exclusions": [field]})._conversation_exclusions
    response = generate_chat_response(original, product, f"{word}은 빼줘. 이 제품 먹어도 돼?")
    assert response.diet_match == "not_match"
    assert response.allergy_status == "no_known_match"


@pytest.mark.parametrize("message", ["유제품은 먹어도 돼?", "유제품은 안 먹어?", "유제품을 빼도 돼?"])
def test_a_real_allowance_question_is_not_a_declaration(message):
    assert stated_vegetarian_allowance(message) == {}


def test_direct_service_collects_exclusion_before_allergy_guard():
    profile = Profile(**ME)
    product = Product(product_name="우유", rawmtrl_nm="우유", allergy=["우유"], facts_verified=True)
    response = generate_chat_response(profile, product, "유제품은 빼줘. " + UNCERTAIN + "이 제품 요리 알려줘")
    assert response.conversation_state.vegetarian_allowances[0].dairy is False
    assert response.conversation_state.pending_allergy.uncertain == ["우유"]
    assert not response.recipe


def test_owner_clarification_then_product_selection_keeps_temporary_allergy(saved_chat):
    saved_chat("새우 알레르기도 있는데 새우깡 먹어도 돼?", ME, profiles=[ME, CHILD])
    saved_chat.detail()
    candidates = saved_chat("아이 거야", ME, profiles=[ME, CHILD])
    assert len(candidates["product_candidates"]) >= 2
    saved_chat.detail()
    result = saved_chat("첫 번째", ME, profiles=[ME, CHILD])
    assert result["intent"] == "can_i_eat"
    assert result["overall_decision"] == "avoid"
    assert next(p for p in result["per_profile_results"] if p["profile_id"] == 2)["allergy_status"] == "conflict"
    assert "pending_allergy" not in stored_state(result)


@pytest.mark.parametrize("reply", ["엄마 거야", "아이 거야?", "응", "잘 모르겠는데요"])
def test_unresolved_owner_answer_does_not_guess_or_discard_pending_condition(saved_chat, reply):
    saved_chat("우유 알레르기도 있는데 이 제품 먹어도 돼?", ME, profiles=[ME, CHILD], barcode="P00156")
    saved_chat.detail()
    response = saved_chat(reply, ME, profiles=[ME, CHILD])
    assert response["overall_decision"] == "check_required"
    assert response["recipe"] is None and not response["recommended_products"]
    assert stored_state(response)["pending_allergy"]["confirmed"] == ["우유"]


def test_new_topic_does_not_inherit_unresolved_allergy(saved_chat):
    saved_chat("우유 알레르기도 있는데 이 제품 먹어도 돼?", ME, profiles=[ME, CHILD], barcode="P00156")
    saved_chat.detail()
    response = saved_chat("그 질문은 취소할게. 저염식이 뭐야?", ME, profiles=[ME, CHILD])
    assert "pending_allergy" not in stored_state(response)
    assert response["intent"] == "general_diet_guide"


def test_pending_condition_schema_is_bounded_and_excludes_injected_facts():
    req = ChatRequest(message="아이 거야", conversation_state={"pending_allergy": {
        "message": "x" * 10000, "confirmed": ["우유", "우유", "가짜성분"],
        "uncertain": ["땅콩"], "owner_key": "id:2", "product": {"allergy": []},
        "resolved": True, "sql": "untrusted",
    }})
    pending = req.conversation_state["pending_allergy"]
    assert len(pending["message"]) == 4000
    assert pending["confirmed"] == ["우유"]
    assert set(pending) == {"message", "confirmed", "uncertain", "owner_key"}


def test_excluded_dairy_is_filtered_from_nonvegetarian_alternative_candidates():
    from app.services.product import recommend_catalog_products
    from test_recommendation_policy_regressions import _product, _CandidateRepository
    profiles, _, _ = apply_vegetarian_context([Profile(**ME)], "유제품은 빼줘", {})
    milk = _product(8401, "우유 과자", ingredients="우유, 귀리", allergy=["우유"])
    oats = _product(8402, "귀리 과자", ingredients="귀리, 코코아")
    cards = recommend_catalog_products("과자 추천해줘", profiles, repository=_CandidateRepository([milk, oats]))
    assert [p["product_name"] for p in cards] == ["귀리 과자"]


def test_request_paraphrase_guard_runs_before_search_or_external_ai(monkeypatch):
    import app.services.chat as service
    import app.services.conversation_router as router

    def forbidden(*args, **kwargs):
        pytest.fail("Unresolved allergy must not invoke search or external AI")

    monkeypatch.setattr(router, "find_product_candidates_from_message", forbidden)
    monkeypatch.setattr(service, "generate_final_response", forbidden)
    response = chat(ChatRequest(message=UNCERTAIN + "이 제품으로 만들어 먹을 요리 알려줘", profile=Profile(**ME), barcode="P00156"))
    assert response.overall_decision == "check_required"


@pytest.mark.parametrize("message", ["원재료와 알레르기 표시 알려줘", "이 제품 알레르기 성분 알려줘", "알레르기란 뭐야?"])
def test_allergy_information_question_does_not_claim_a_personal_condition(message):
    from app.services.message_constraint_guard import build_message_constraint_guard_response
    assert build_message_constraint_guard_response(message, profiles=[Profile(**ME)]) is None


def test_nonvegetarian_exclusion_can_be_explicitly_withdrawn(saved_chat):
    first = saved_chat("유제품은 빼줘. 이 제품 레시피 추천해줘", ME, barcode="P00156")
    assert first["recipe"]["source_product_blocked"]
    saved_chat.detail()
    withdrawn = saved_chat("유제품은 먹어. 다른 레시피 추천해줘", ME)
    assert withdrawn["recipe"] and not withdrawn["recipe"]["source_product_blocked"]
    assert stored_state(withdrawn)["vegetarian_allowances"][0]["dairy"] is True


def test_account_vegetarian_flag_alone_is_not_an_independent_exclusion():
    profile = Profile(**ME, vegetarian_dairy_allowed=False)
    effective, _, _ = apply_vegetarian_context([profile], "이 제품 먹어도 돼?", {})
    milk = Product(product_name="우유", rawmtrl_nm="우유", facts_verified=True)
    assert not effective[0]._conversation_exclusions
    assert not evaluate_special_diets(effective[0], milk, "")[0]


def test_nonvegetarian_exclusion_still_rejects_llm_recipe_substitution():
    import json
    from app.infrastructure.llm import set_llm_client
    from test_food_chat_revalidation import draft

    set_llm_client(lambda _: json.dumps(draft(substitutions=["우유로 바꿔요."]), ensure_ascii=False))
    try:
        response = chat(ChatRequest(message="유제품은 빼줘. 저염 레시피 추천해줘", profile=Profile(**ME)))
    finally:
        set_llm_client(None)
    assert response.answer_source == "rules"
    assert response.fallback_reason == "recipe_llm_diet_conflict"
    assert all("우유" not in step for step in response.recipe.steps)
