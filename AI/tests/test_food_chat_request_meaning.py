"""Request meaning and boundaries from the 1,118-test review."""

import pytest

from app.core.models import ChatRequest, Profile
from app.main import chat
from app.services.message_constraint_guard import _message_allergens
from app.services.diet_conversation_context import stated_vegetarian_allowance
from app.services.request_language import request_is_cancelled
from test_food_chat_condition_lifecycle import saved_chat, state
from test_food_chat_pending_conditions import stored_state


ME = {"profile_id": 1, "profile_name": "나", "allergies": []}
CHILD = {"profile_id": 2, "profile_name": "아이", "allergies": []}
VEG = {**ME, "special_diets": ["채식"], "vegetarian_dairy_allowed": True, "vegetarian_egg_allowed": True}


@pytest.mark.parametrize("statement", [
    "바나나 알레르기가 심해", "바나나 알레르기야", "바나나 알레르기입니다",
    "바나나 알레르기가 아주 심한 편이야",
])
@pytest.mark.parametrize("registered", [[], ["새우"]])
def test_unsupported_assertion_is_not_absent(saved_chat, statement, registered):
    profile = {**ME, "allergies": registered}
    response = saved_chat(statement + ". 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert response["recipe"] is None and not response["recommended_products"]
    assert response["overall_decision"] == "check_required"
    assert stored_state(response)["pending_allergy"]["unsupported_confirmed"] == ["바나나"]
    assert saved_chat.detail()["messages"][-1]["response"]["recipe"] is None
    again = saved_chat("계속해줘", profile)
    assert again["recipe"] is None and "바나나" in again["answer"]
    assert profile["allergies"] == registered


@pytest.mark.parametrize("message", [
    "나는 우유 알레르기가 있는 사람이야. 이 제품으로 레시피 추천해줘",
    "이 제품으로 레시피 추천해줘. 저는 우유 알레르기가 있는 사람이에요.",
    "나는 우유 알레르기가 있는 사람인데 이 제품으로 레시피 추천해줘",
])
def test_personal_copula_is_a_condition(saved_chat, message):
    response = saved_chat(message, ME, barcode="P00156")
    assert response["allergy_status"] == "conflict"
    assert response["overall_decision"] == "avoid"
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]
    assert saved_chat.detail()["messages"][-1]["response"]["overall_decision"] == "avoid"


@pytest.mark.parametrize("statement", [
    "나는 우유 알레르기가 있는 사람인지 모르겠어.",
    "나는 우유 알레르기가 있는 사람이야?",
    "나는 우유 알레르기가 있는 사람은 아닌지 모르겠어.",
])
def test_uncertain_personal_copula_is_not_absent_or_confirmed(saved_chat, statement):
    response = saved_chat(statement + " 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    assert response["recipe"] is None and response["allergy_status"] == "unknown"
    assert stored_state(response)["pending_allergy"]["uncertain"] == ["우유"]
    saved_chat.detail()
    confirmed = saved_chat("있어", ME)
    assert confirmed["allergy_status"] == "conflict"
    assert confirmed["recipe"] is None or confirmed["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("message", [
    "우유 알레르기가 있는 사람은 뭘 확인해야 해?",
    "바나나 알레르기 있는 사람이 먹어도 돼?",
    "바나나 알레르기 증상 알려줘", "바나나 알레르기는 없어",
    "나는 우유 알레르기가 있는 사람은 아니야",
])
def test_general_or_denied_condition_is_not_asserted(message):
    assert not _message_allergens(message).confirmed


@pytest.mark.parametrize("reply", [
    "유제품은 먹어. 레시피 요청 취소하지 말고 계속해줘",
    "유제품은 먹어. 질문 그만하고 레시피 만들어줘",
    "유제품은 먹어. 취소하지 마. 계속해줘",
])
def test_negated_cancellation_preserves_request_allergy(saved_chat, reply):
    first = saved_chat("우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘", VEG, barcode="P00156")
    assert stored_state(first)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    response = saved_chat(reply, VEG)
    assert response["overall_decision"] == "avoid"
    assert response["allergy_status"] == "conflict"
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]
    assert "pending_allergy" not in stored_state(response)
    saved_chat.detail()
    new = saved_chat("이 제품 먹어도 돼?", VEG, barcode="P00156")
    assert new["allergy_status"] == "no_known_match"


@pytest.mark.parametrize("message,cancelled", [
    ("레시피 요청 취소해줘", True), ("그만. 다른 질문 할게", True),
    ("취소하지 말고 계속해줘", False), ("질문 그만하고 레시피 만들어줘", False),
    ("그만하지 말고 계속해줘", False),
    ("취소하지 말고 새 질문에 답해줘", True),
])
def test_cancellation_target_and_negation(message, cancelled):
    assert request_is_cancelled(message) is cancelled


def test_stopping_questions_does_not_resolve_an_unanswered_condition(saved_chat):
    saved_chat("우유 알레르기가 있는지 모르겠어. 이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    saved_chat.detail()
    response = saved_chat("질문 그만하고 레시피 만들어줘", ME)
    assert response["recipe"] is None
    assert stored_state(response)["pending_allergy"]["uncertain"] == ["우유"]


@pytest.mark.parametrize("exclusion", [
    "유제품 없이 만들어줄래?", "유제품은 빼주실 수 있나요?",
    "유제품은 제외해 주실 수 있을까요?", "유제품 없이 조리해줄래?",
])
@pytest.mark.parametrize("profile", [ME, VEG])
def test_exclusion_request_blocks_milk_and_survives_storage(saved_chat, exclusion, profile):
    response = saved_chat("이 제품으로 레시피 추천해줘. " + exclusion, profile, barcode="P00156")
    assert stored_state(response)["vegetarian_allowances"][0]["dairy"] is False
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]
    saved_chat.detail()
    following = saved_chat("다른 레시피 더 있어?", profile)
    assert following["recipe"] is None or following["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("question", ["유제품을 빼도 돼?", "유제품 없이 만들어도 돼?", "달걀은 안 먹어?"])
def test_permission_question_is_not_a_directive(question):
    assert stated_vegetarian_allowance(question) == {}


@pytest.mark.parametrize("question", [
    "이 제품으로 만들 수 있는 요리 추천해줘",
    "그 제품으로 요리를 만들어 주세요",
    "이 제품으로 어떤 요리를 만들면 좋을까요?",
])
def test_deictic_request_preserves_registered_warning(saved_chat, question):
    me = {**ME, "allergies": ["새우"]}
    saved_chat("이 제품 먹어도 돼?", me, profiles=[me, CHILD], barcode="8801043035989")
    saved_chat.detail()
    response = saved_chat("땅콩 알레르기도 있는데 " + question, me, profiles=[me, CHILD])
    assert response["current_product"]["barcode"] == "8801043035989"
    assert response["overall_decision"] == "avoid"
    assert "새우" in response["answer"] and "어느 프로필" in response["answer"]
    assert response["recipe"] is None
    assert saved_chat.detail()["messages"][-1]["response"]["overall_decision"] == "avoid"


def test_stateless_original_reproductions():
    profile = Profile(**ME)
    for statement in ["바나나 알레르기야", "바나나 알레르기가 심해"]:
        response = chat(ChatRequest(message=statement + ". 이 제품으로 레시피 추천해줘", profile=profile, barcode="P00156"))
        assert response.recipe is None
    first = chat(ChatRequest(message="우유 알레르기는 있어. 유제품은 먹는지 모르겠어. 이 제품으로 레시피 추천해줘",
                             profile=Profile(**VEG), barcode="P00156"))
    second = chat(ChatRequest(message="유제품은 먹어. 취소하지 말고 계속해줘",
                              profile=Profile(**VEG), conversation_state=state(first)))
    assert second.allergy_status == "conflict"


@pytest.mark.parametrize("message", [
    "서울우유로 레시피 추천해줘", "서울우유를 이용한 레시피 추천해줘",
    "서울우유로 저염 레시피 추천해줘",
])
def test_instrumental_particle_resolves_to_catalog_candidates(saved_chat, message):
    response = saved_chat(message, ME)
    assert response["recipe"] is None
    assert any("서울우유" in candidate["product_name"] for candidate in response["product_candidates"])
    assert stored_state(response)["pending_intent"] == "recipe_recommend"
    saved_chat.detail()
    result = saved_chat("1번으로 할게", ME)
    assert result["current_product"]["barcode"] == "SEOUL_NA100_200"
    assert result["recipe"] is not None
    if "저염" in message:
        assert "저염식" in result["recipe"]["requested_diets"]


@pytest.mark.parametrize("message", [
    "미등록마법제품으로 레시피 추천해줘", "미등록마법제품을 이용한 레시피 추천해줘",
])
def test_unknown_source_never_silently_becomes_generic_recipe(saved_chat, message):
    first = saved_chat("이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    assert stored_state(first)["recipe_product"]["barcode"] == "P00156"
    response = saved_chat(message, ME)
    assert response["recipe"] is None and response["current_product"] is None
    assert response["intent"] == "missing_product"
    assert "확인하지 못해" in response["answer"]
    pending = stored_state(response)
    assert "last_confirmed_product" not in pending and "recipe_product" not in pending
    saved_chat.detail()
    following = saved_chat("이 제품으로 만들 수 있는 요리 추천해줘", ME)
    assert following["recipe"] is None and following["current_product"] is None
    saved_chat.detail()
    generic = saved_chat("일반 레시피 추천해줘", ME)
    assert generic["recipe"] is not None and generic["recipe"]["product_name"] == "기본 식재료"
    assert "pending_user_message" not in stored_state(generic)


@pytest.mark.parametrize("reply,extra", [
    ("이거야", {"barcode": "P00156"}), ("매일우유 오리지널 200ml", {}),
])
def test_source_correction_preserves_temporary_allergy_and_goals(saved_chat, reply, extra):
    response = saved_chat("우유 알레르기는 있어. 미등록마법제품으로 저염 레시피 추천해줘", ME)
    assert response["recipe"] is None and response["current_product"] is None
    assert stored_state(response)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    result = saved_chat(reply, ME, **extra)
    if result["product_candidates"]:
        saved_chat.detail()
        result = saved_chat("1번으로 할게", ME)
    assert result["allergy_status"] == "conflict"
    assert result["recipe"] is None or result["recipe"]["source_product_blocked"]
    assert "저염식" in stored_state(result)["recipe_requested_diets"]
    assert "pending_allergy" not in stored_state(result)
    saved_chat.detail()
    independent = saved_chat("이 제품 먹어도 돼?", ME, barcode="P00156")
    assert independent["allergy_status"] == "no_known_match"


def test_failed_source_identifier_keeps_unfinished_condition(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", ME)
    saved_chat.detail()
    failed = saved_chat("이거야", ME, barcode="MISSING_AUDIT_BARCODE")
    assert failed["recipe"] is None and failed["current_product"] is None
    assert stored_state(failed)["pending_allergy"]["confirmed"] == ["우유"]
    saved_chat.detail()
    resolved = saved_chat("이거야", ME, barcode="P00156")
    assert resolved["allergy_status"] == "conflict"
    assert resolved["recipe"] is None or resolved["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("message", ["저염 레시피 추천해줘", "일반 레시피 추천해줘", "레시피 추천해줘"])
def test_intentionally_productless_recipe_still_works(message):
    response = chat(ChatRequest(message=message, profile=Profile(**ME)))
    assert response.recipe is not None and response.recipe.product_name == "기본 식재료"


@pytest.mark.parametrize("message", ["뽀로로 레시피 추천해줘", "뽀로로로 레시피 추천해줘"])
def test_real_product_name_ending_ro_is_preserved(message):
    from app.services.product import find_product_candidates_from_message
    from test_recommendation_policy_regressions import _product, _CandidateRepository
    product = _product(8502, "뽀로로", ingredients="귀리")
    result = find_product_candidates_from_message(message, repository=_CandidateRepository([product]))
    assert result and result[0].product_id == 8502


def test_stateless_missing_source_keeps_conditions_through_correction():
    profile = Profile(**ME)
    first = chat(ChatRequest(message="우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", profile=profile))
    assert first.recipe is None
    second = chat(ChatRequest(message="이거야", barcode="P00156", profile=profile, conversation_state=state(first)))
    assert second.allergy_status == "conflict"
    assert second.recipe is None or second.recipe.source_product_blocked


@pytest.mark.parametrize("question", [
    "이 제품 말고 서울우유로 요리 추천해줘",
    "이 제품이랑 서울우유 비교해줘",
    "이 제품으로 서울우유 요리 추천해줘",
])
def test_other_target_is_not_a_deictic_followup(question):
    from app.services.request_language import refers_only_to_current_product
    assert not refers_only_to_current_product(question)


@pytest.mark.parametrize("continuation", ["레시피 요청 취소해줘", "다른 질문 할게", "레시피 취소", "레시피 취소해 주세요"])
def test_real_cancellation_clears_request_allergy(saved_chat, continuation):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", ME)
    saved_chat.detail()
    response = saved_chat(continuation, ME)
    assert "pending_allergy" not in stored_state(response)
    assert response["recipe"] is None
    saved_chat.detail()
    independent = saved_chat("이 제품으로 레시피 추천해줘", ME, barcode="P00156")
    assert independent["allergy_status"] == "no_known_match"
    assert independent["recipe"] is not None and not independent["recipe"]["source_product_blocked"]


def test_cancellation_does_not_query_catalog_or_external_ai(monkeypatch):
    import app.services.conversation_router as router
    from app.services.chat import generate_chat_response
    def forbidden(*args, **kwargs):
        raise AssertionError("Cancellation must not query products or generate answers")
    monkeypatch.setattr(router, "find_product_candidates_from_message", forbidden)
    monkeypatch.setattr("app.services.chat.generate_final_response", forbidden)
    response = chat(ChatRequest(message="레시피 요청 취소해줘", profile=Profile(**ME)))
    assert response.recipe is None and "취소했어요" in response.answer
    direct = generate_chat_response(Profile(**ME), None, "레시피 요청 취소해줘")
    assert direct.recipe is None and "취소했어요" in direct.answer


def test_exclusion_prefix_does_not_become_a_new_product_name(saved_chat):
    saved_chat("이 제품 먹어도 돼?", ME, barcode="P00156")
    saved_chat.detail()
    result = saved_chat("유제품 없이 이 제품으로 레시피 추천해줘", ME)
    assert result["current_product"]["barcode"] == "P00156"
    assert stored_state(result)["vegetarian_allowances"][0]["dairy"] is False
    assert result["recipe"] is None or result["recipe"]["source_product_blocked"]


@pytest.mark.parametrize("message", ["제품 없이 일반 레시피 추천해줘", "일반 레시피 추천해줘"])
def test_explicit_generic_recipe_does_not_inherit_a_previous_product(saved_chat, message):
    saved_chat("이 제품 먹어도 돼?", ME, barcode="P00156")
    saved_chat.detail()
    response = saved_chat(message, ME)
    assert response["recipe"]["product_name"] == "기본 식재료"
    assert response["current_product"] is None


def test_source_correction_does_not_assign_an_off_owners_allergy_to_someone_else(saved_chat):
    saved_chat("우유 알레르기는 있어. 미등록마법제품으로 레시피 추천해줘", ME)
    saved_chat.detail()
    off = {**ME, "is_active": False}
    result = saved_chat("이거야", off, profiles=[off, CHILD], barcode="P00156")
    assert result["recipe"] is None
    assert result["allergy_status"] != "conflict"
    assert "어느 프로필" in result["answer"]
