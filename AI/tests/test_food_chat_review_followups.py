"""Audit reproductions: unsupported conditions and unfinished-request boundaries."""

import pytest

from app.core.models import ChatRequest, Product, Profile
from app.main import chat
from app.services.chat import generate_chat_response
from app.services.diet_conversation_context import apply_vegetarian_context, stated_vegetarian_allowance
from app.services.message_constraint_guard import _message_allergens, build_message_constraint_guard_response
from app.services.response_helpers import evaluate_special_diets
from test_food_chat_condition_lifecycle import saved_chat, state
from test_food_chat_pending_conditions import stored_state
from test_recommendation_policy_regressions import _product, _CandidateRepository


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}
VEG = {**ME, "special_diets": ["채식"], "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True}


@pytest.mark.parametrize("registered", [[], ["새우"]])
def test_unsupported_new_allergy_is_not_hidden_by_registered_allergy(saved_chat, registered):
    profile = {**ME, "allergies": registered}
    first = saved_chat("바나나 알레르기도 있어. 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert first["recipe"] is None and not first["recommended_products"]
    assert first["overall_decision"] == "check_required"
    assert "바나나" in first["answer"] and "지원" in first["answer"]
    assert stored_state(first)["pending_allergy"]["unsupported_confirmed"] == ["바나나"]
    saved_chat.detail()
    again = saved_chat("바나나 알레르기는 있어", profile)
    assert again["recipe"] is None
    assert stored_state(again)["pending_allergy"]["unsupported_confirmed"] == ["바나나"]
    assert profile["allergies"] == registered


@pytest.mark.parametrize("message", [
    "나는 우유 알레르기가 있어. 알레르기 있는 사람도 먹을 레시피 추천해줘",
    "알레르기 있는 사람도 먹을 레시피 추천해줘. 나는 우유 알레르기가 있어.",
    "나는 우유 알레르기가 있고 알레르기 있는 사람도 먹을 레시피 추천해줘",
])
def test_general_clause_does_not_erase_personal_assertion(saved_chat, message):
    response = saved_chat(message, ME, barcode="P00156")
    assert response["allergy_status"] == "conflict"
    assert response["overall_decision"] == "avoid"
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("message", [
    "우유 알레르기 있는 사람은 뭘 확인해야 해?",
    "바나나 알레르기가 있는 사람은 뭘 확인해야 해?",
    "바나나 알레르기는 없어. 이 제품 먹어도 돼?",
    "원재료와 알레르기 표시 알려줘", "이 제품 알레르기 성분 알려줘",
])
def test_general_information_and_denial_do_not_add_an_allergy(message):
    assert not _message_allergens(message).confirmed
    assert build_message_constraint_guard_response(message, profiles=[Profile(**ME)]) is None


@pytest.mark.parametrize("reply", ["유제품은 먹어", "유제품은 먹어. 이 제품으로 레시피 추천해줘"])
def test_diet_clarification_preserves_allergy_until_original_request_completes(saved_chat, reply):
    first = saved_chat("우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", VEG, barcode="P00156")
    assert first["overall_decision"] == "avoid" and first["recipe"] is None
    assert stored_state(first)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    result = saved_chat(reply, VEG)
    assert result["overall_decision"] == "avoid"
    assert result["allergy_status"] == "conflict"
    assert result["recipe"] is None or result["recipe"]["source_product_blocked"]
    assert "pending_allergy" not in stored_state(result)
    saved_chat.detail()
    independent = saved_chat("이 제품 먹어도 돼?", VEG, barcode="P00156")
    assert independent["allergy_status"] == "no_known_match"
    assert VEG["allergies"] == []


def test_stateless_diet_reply_keeps_temporary_allergy():
    profile = Profile(**VEG)
    first = chat(ChatRequest(profile=profile, barcode="P00156", message="우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘"))
    second = chat(ChatRequest(profile=profile, message="유제품은 먹어", conversation_state=state(first)))
    assert second.allergy_status == "conflict"
    assert second.recipe is None or second.recipe.source_product_blocked


@pytest.mark.parametrize("ingredient", ["원유", "탈지분유", "전지분유", "생크림", "유단백"])
def test_dairy_aliases_block_product_and_candidates_without_claiming_an_allergy(ingredient):
    from app.services.product import recommend_catalog_products
    effective, _, _ = apply_vegetarian_context([Profile(**ME)], "유제품은 빼줘", {})
    product = _product(8501, "코코아 과자", ingredients=f"귀리, {ingredient}")
    assert evaluate_special_diets(effective[0], product, "")[0]
    assert not recommend_catalog_products("과자 추천해줘", effective, repository=_CandidateRepository([product]))
    response = generate_chat_response(Profile(**ME), product, "유제품은 빼줘. 이 제품 먹어도 돼?")
    assert response.diet_match == "not_match"
    assert response.allergy_status == "no_known_match"
    recipe = generate_chat_response(Profile(**ME), product, "유제품은 빼줘. 이 제품으로 레시피 추천해줘")
    assert recipe.recipe is None or recipe.recipe.source_product_blocked


@pytest.mark.parametrize("word,ingredient", [("달걀", "난백"), ("달걀", "난황"), ("유제품", "탈지분유")])
@pytest.mark.parametrize("location", ["steps", "substitutions"])
def test_recipe_steps_and_substitutions_use_the_same_exclusion_dictionary(word, ingredient, location):
    from app.services.recipe import _diet_findings, _draft_validation_product, _RecipeDraft
    effective, _, _ = apply_vegetarian_context([Profile(**ME)], f"{word}은 빼줘", {})
    draft = _RecipeDraft(title="검사용", product_name="검사 제품", servings="1인분", ingredients=[{"name": "귀리", "amount": "30g"}],
                        steps=["귀리를 섞어요."], substitutions=[])
    draft = draft.model_copy(update={location: [f"귀리에 {ingredient}를 넣어요."]})
    assert _diet_findings(effective, _draft_validation_product(draft), "")[0]


@pytest.mark.parametrize("message", ["유제품은 빼줄래?", "유제품은 제외해줄래?", "유제품은 빼 주시겠어요?"])
def test_polite_exclusion_question_blocks_source_recipe(saved_chat, message):
    response = saved_chat("이 제품으로 레시피 추천해줘. " + message, VEG, barcode="P00156")
    assert stored_state(response)["vegetarian_allowances"][0]["dairy"] is False
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]
    assert response["allergy_status"] == "no_known_match"


@pytest.mark.parametrize("message", ["유제품은 먹어도 돼?", "유제품은 안 먹어?", "유제품을 빼도 돼?"])
def test_permission_question_still_does_not_assert_an_exclusion(message):
    assert stated_vegetarian_allowance(message) == {}


@pytest.mark.parametrize("question", ["이 제품 먹어도 괜찮을까요?", "이 제품으로 뭘 만들어 먹으면 좋을까?"])
def test_deictic_rephrasing_keeps_known_product_and_registered_conflict(saved_chat, question):
    me = {**ME, "allergies": ["새우"]}
    saved_chat("이 제품 먹어도 돼?", me, profiles=[me, CHILD], barcode="8801043035989")
    saved_chat.detail()
    response = saved_chat("땅콩 알레르기도 있는데 " + question, me, profiles=[me, CHILD])
    assert response["current_product"]["barcode"] == "8801043035989"
    assert response["overall_decision"] == "avoid"
    assert "새우" in response["answer"] and "어느 프로필" in response["answer"]


@pytest.mark.parametrize("reply", ["1번으로 할게", "농심 새우깡 90g으로 선택할게"])
def test_candidate_choice_after_owner_clarification_uses_shared_parser(saved_chat, reply):
    saved_chat("새우 알레르기도 있는데 새우깡 먹어도 돼?", ME, profiles=[ME, CHILD])
    saved_chat.detail()
    candidates = saved_chat("아이 거야", ME, profiles=[ME, CHILD])
    assert len(candidates["product_candidates"]) >= 2
    saved_chat.detail()
    result = saved_chat(reply, ME, profiles=[ME, CHILD])
    assert result["current_product"]["barcode"] == "8801043035989"
    assert result["overall_decision"] == "avoid"
    assert next(p for p in result["per_profile_results"] if p["profile_id"] == 2)["allergy_status"] == "conflict"
    assert "pending_allergy" not in stored_state(result)


@pytest.mark.parametrize("reply,conflict", [("있어", True), ("없어", False)])
def test_short_certainty_answer_resolves_one_known_owners_pending_allergy(saved_chat, reply, conflict):
    first = saved_chat("우유 알레르기가 있는지는 모르겠어. 이 제품 먹어도 돼?", ME, barcode="P00156")
    assert stored_state(first)["pending_allergy"]["uncertain"] == ["우유"]
    saved_chat.detail()
    result = saved_chat(reply, ME)
    assert result["allergy_status"] == ("conflict" if conflict else "no_known_match")
    assert "pending_allergy" not in stored_state(result)


def test_short_denial_does_not_remove_registered_or_other_confirmed_allergies(saved_chat):
    from app.services.food_conditions import prepare_food_conditions
    profile = {**ME, "allergies": ["우유"]}
    first = saved_chat("땅콩 알레르기는 있어. 우유 알레르기가 있는지는 모르겠어. 이 제품 먹어도 돼?", profile, barcode="P00156")
    prepared = prepare_food_conditions("없어", [Profile(**profile)], stored_state(first))
    assert set(prepared.profiles[0].resolved_allergies()) == {"우유", "땅콩"}
    saved_chat.detail()
    response = saved_chat("없어", profile)
    assert response["allergy_status"] == "conflict" and response["overall_decision"] == "avoid"
    assert profile["allergies"] == ["우유"]


@pytest.mark.parametrize("ambiguous", ["owner", "multiple", "off"])
def test_short_certainty_never_guesses_an_owner_or_multiple_conditions(saved_chat, ambiguous):
    profiles = [ME, CHILD] if ambiguous == "owner" else [ME]
    condition = "땅콩 알레르기도 있는지 모르겠어. " if ambiguous == "multiple" else ""
    saved_chat(condition + "우유 알레르기가 있는지는 모르겠어. 이 제품 먹어도 돼?", ME,
               profiles=profiles, barcode="P00156")
    saved_chat.detail()
    if ambiguous == "off":
        profiles = [{**ME, "is_active": False}, CHILD]
    response = saved_chat("있어", profiles[0], profiles=profiles)
    assert response["overall_decision"] == "check_required"
    assert stored_state(response)["pending_allergy"]["uncertain"]
    assert response["allergy_status"] != "conflict"


@pytest.mark.parametrize("ingredient", ["원유", "탈지분유", "전지분유", "생크림"])
def test_saved_exclusion_uses_label_aliases_after_store_reopening(saved_chat, ingredient):
    from app.services.product import get_product_repository, set_product_repository
    product = _product(8501, "코코아 과자", ingredients=f"귀리, {ingredient}")

    class Catalog(_CandidateRepository):
        def search_by_text(self, text):
            return None

        def get_by_barcode(self, barcode):
            return product if barcode == product.barcode else None

        def get_by_product_id(self, product_id):
            return product if product_id == product.product_id else None

        def get_by_report_number(self, report_number):
            return product if report_number == product.report_number else None

    previous = get_product_repository()
    set_product_repository(Catalog([product]))
    try:
        first = saved_chat("유제품은 빼줘. 이 제품 먹어도 돼?", ME, barcode=product.barcode)
        assert first["diet_match"] == "not_match"
        assert first["allergy_status"] == "no_known_match"
        detail = saved_chat.detail()
        assert detail["messages"][-1]["response"]["diet_match"] == "not_match"
        result = saved_chat("이 제품으로 레시피 추천해줘", ME)
        assert result["recipe"] is None or result["recipe"]["source_product_blocked"]
        assert stored_state(result)["vegetarian_allowances"][0]["dairy"] is False
    finally:
        set_product_repository(previous)


def test_product_name_or_shared_facility_is_not_direct_ingredient_evidence():
    effective, _, _ = apply_vegetarian_context([Profile(**ME)], "유제품은 빼줘", {})
    for product in [
        Product(product_name="우유맛 식물성 음료", rawmtrl_nm="귀리, 정제수"),
        Product(product_name="귀리 음료", rawmtrl_nm="귀리, 정제수", allergy_warning="우유를 사용하는 제품과 같은 제조시설에서 제조"),
    ]:
        assert not evaluate_special_diets(effective[0], product, "")[0]


def test_product_selection_then_diet_answer_keeps_the_same_temporary_allergy(saved_chat):
    child = {**VEG, "profile_id": 2, "profile_name": "아이"}
    saved_chat("새우 알레르기도 있는데 새우깡 먹어도 돼?", ME, profiles=[ME, child])
    saved_chat.detail()
    candidates = saved_chat("아이 거야. 아이는 유제품은 먹는지 모르겠어.", ME, profiles=[ME, child])
    assert candidates["product_candidates"]
    assert stored_state(candidates)["pending_allergy"]["confirmed"] == ["새우"]
    saved_chat.detail()
    second = saved_chat("1번으로 할게", ME, profiles=[ME, child])
    assert second["recipe"] is None and second["allergy_status"] == "conflict"
    assert stored_state(second)["pending_allergy"]["confirmed"] == ["새우"]
    assert not stored_state(second).get("pending_product_candidates")
    saved_chat.detail()
    third = saved_chat("아이는 유제품은 먹어", ME, profiles=[ME, child])
    assert third["allergy_status"] == "conflict"
    assert next(p for p in third["per_profile_results"] if p["profile_id"] == 2)["allergy_status"] == "conflict"
    assert not third["product_candidates"]
    assert "pending_allergy" not in stored_state(third)


def test_successive_diet_answers_preserve_allergy_until_both_are_resolved(saved_chat):
    first = saved_chat("우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 달걀은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", VEG, barcode="P00156")
    assert first["recipe"] is None
    saved_chat.detail()
    second = saved_chat("유제품은 먹어", VEG)
    assert second["recipe"] is None
    assert stored_state(second)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    third = saved_chat("달걀은 먹어", VEG)
    assert third["allergy_status"] == "conflict"
    assert third["recipe"] is None or third["recipe"]["source_product_blocked"]
    assert "pending_allergy" not in stored_state(third)


def test_pending_candidate_language_check_never_queries_the_catalog(monkeypatch):
    from app.services import conversation_router as router

    def forbidden(*args, **kwargs):
        raise AssertionError("Language interpretation must not query the catalog")

    monkeypatch.setattr(router, "_resolve_authoritative_product_reference", forbidden)
    pending = {"pending_product_candidates": [
        {"product_name": "농심 새우깡 90g", "barcode": "8801043035989"},
        {"product_name": "농심 새우깡 대용량", "barcode": "not-trusted"},
    ]}
    assert router.is_pending_candidate_reply("1번으로 할게", pending)
    assert router.is_pending_candidate_reply("농심 새우깡 90g으로 선택할게", pending)
    assert not router.is_pending_candidate_reply("첫 번째와 두 번째 비교해줘", pending)
    assert not router.is_pending_candidate_reply("첫 번째는 싫어", pending)


def test_unknown_conditions_remain_separate_and_bounded_in_public_state():
    req = ChatRequest(message="알려줘", conversation_state={"pending_allergy": {
        "message": "바나나 알레르기가 있어", "confirmed": ["우유", "가짜성분"],
        "unsupported_confirmed": ["바나나", "바나나", "우유", "bad\nvalue", "x" * 50],
        "unsupported_uncertain": ["키위"], "product": {"allergy": []},
    }})
    pending = req.conversation_state["pending_allergy"]
    assert pending["confirmed"] == ["우유"]
    assert pending["unsupported_confirmed"] == ["바나나"]
    assert pending["unsupported_uncertain"] == ["키위"]
    assert "product" not in pending


@pytest.mark.parametrize("subject", ["엄마는", "아빠", "아이가", "부모님은"])
def test_a_person_before_unspecified_allergy_is_not_an_unknown_food(subject):
    parsed = _message_allergens(f"{subject} 알레르기가 있어.")
    assert not parsed.confirmed and parsed.unspecified


def test_new_unknown_condition_does_not_overflow_registered_allergy_list():
    profile = Profile(**{**ME, "allergies": [f"기존항목{index}" for index in range(32)]})
    response = generate_chat_response(profile, None, "바나나 알레르기도 있어. 레시피 추천해줘")
    assert response.recipe is None and not response.recommended_products
    assert response.conversation_state.pending_allergy.unsupported_confirmed == ["바나나"]
