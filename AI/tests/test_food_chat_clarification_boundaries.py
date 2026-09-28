"""Clarification must preserve certainty, known conflicts, scope and origin."""

import pytest

from app.core.models import ChatRequest, Product, Profile
from app.main import chat
from app.services.chat import generate_chat_response
from app.services.diet_conversation_context import apply_vegetarian_context
from app.services.message_constraint_guard import apply_turn_local_allergies, build_message_constraint_guard_response
from test_food_chat_condition_lifecycle import saved_chat, state


def vegetarian(**changes):
    return {
        "profile_id": 1, "profile_name": "나", "allergies": [],
        "special_diets": ["채식"], "vegetarian_dairy_allowed": True,
        "vegetarian_egg_allowed": True, **changes,
    }


@pytest.mark.parametrize("registered", [[], ["대두"]])
@pytest.mark.parametrize("question", ["이 제품 먹어도 돼?", "이 제품으로 레시피 추천해줘"])
def test_mixed_allergy_certainty_requires_clarification(saved_chat, registered, question):
    profile = {"profile_id": 1, "profile_name": "나", "allergies": registered}
    message = "땅콩 알레르기는 있고 우유 알레르기가 있는지는 모르겠어. " + question
    response = saved_chat(message, profile, barcode="P00156")
    assert response["recipe"] is None
    assert response["overall_decision"] == "check_required"
    assert response["allergy_status"] == "unknown"
    assert "우유" in response["answer"] and "확인" in response["answer"]
    assert not response["recommended_products"]
    assert any("우유" in item for item in response["missing_information"])
    detail = saved_chat.detail()
    assert detail["messages"][-1]["response"]["overall_decision"] == "check_required"
    assert profile["allergies"] == registered


def test_uncertain_allergen_is_not_applied_as_confirmed():
    profile = Profile(allergies=[])
    context = apply_turn_local_allergies(
        "땅콩 알레르기는 있고 우유 알레르기가 있는지는 모르겠어. 이거 먹어도 돼?", [profile],
    )
    assert context.profiles[0].resolved_allergies() == ["땅콩"]
    assert profile.resolved_allergies() == []


@pytest.mark.parametrize("question", ["이 제품 먹어도 돼?", "이 제품으로 레시피 추천해줘"])
def test_allergy_owner_question_retains_registered_conflict(saved_chat, question):
    me = {"profile_id": 1, "profile_name": "나", "allergies": ["새우"]}
    child = {"profile_id": 2, "profile_name": "아이", "allergies": []}
    response = saved_chat("땅콩 알레르기도 있는데 " + question, me,
                          profiles=[me, child], barcode="8801043035989")
    assert response["overall_decision"] == "avoid"
    assert response["allergy_status"] == "conflict"
    assert response["assessment_summary"]["decision"] == "avoid"
    assert "새우" in response["answer"] and "어느 프로필" in response["answer"]
    by_name = {item["profile_name"]: item for item in response["per_profile_results"]}
    assert by_name["나"]["allergy_status"] == "conflict"
    assert by_name["아이"]["allergy_status"] == "no_known_match"
    assert response["recipe"] is None and not response["recommended_products"]
    assert response["current_product"]["barcode"] == "8801043035989"
    detail = saved_chat.detail()
    assert detail["messages"][-1]["response"]["overall_decision"] == "avoid"
    assert me["allergies"] == ["새우"] and child["allergies"] == []


def test_pending_diet_does_not_hide_nutrition_facts(saved_chat):
    profile = vegetarian()
    first = saved_chat("유제품은 먹는지 잘 모르겠어. 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert first["recipe"] is None
    saved_chat.detail()
    facts = saved_chat("이 제품 나트륨 몇 mg야?", profile)
    assert facts["intent"] == "nutrition_explain"
    assert "100mg" in facts["answer"]
    assert facts["nutrition_facts"]["sodium"] == 100
    assert facts["diet_match"] != "match"
    saved_chat.detail()
    pending = saved_chat("다른 레시피 더 있어?", profile)
    assert pending["recipe"] is None
    assert "유제품" in pending["answer"] and "확인" in pending["answer"]


def test_current_profile_floor_is_not_a_conversational_exclusion(saved_chat):
    profile = vegetarian(vegetarian_dairy_allowed=False)
    first = saved_chat("유제품은 먹어. 이 제품으로 레시피 추천해줘", profile, barcode="P00156")
    assert first["recipe"] is None or first["recipe"]["source_product_blocked"]
    assert "이번 대화에서 제외한" not in first["answer"]
    saved_chat.detail()
    next_response = saved_chat("다른 레시피 더 있어?", {**profile, "vegetarian_dairy_allowed": True})
    assert next_response["recipe"] is not None
    assert not next_response["recipe"]["source_product_blocked"]
    assert any("매일우유" in item["name"] for item in next_response["recipe"]["ingredients"])


@pytest.mark.parametrize("field,word", [("dairy", "유제품"), ("egg", "달걀")])
def test_store_utterance_separately_from_effective_profile_floor(field, word):
    profile = Profile(**vegetarian(**{f"vegetarian_{field}_allowed": False}))
    effective, entries, _ = apply_vegetarian_context([profile], f"{word}은 먹어.", {})
    assert getattr(effective[0], f"vegetarian_{field}_allowed") is False
    assert entries[0][field] is True
    assert getattr(profile, f"vegetarian_{field}_allowed") is False


@pytest.mark.parametrize("statement", [
    "우유 알레르기가 있는지는 모르겠어. 땅콩 알레르기는 있어.",
    "땅콩 알레르기는 없고 우유 알레르기가 있는지는 모르겠어.",
    "땅콩 알레르기는 있어. 우유 알레르기가 있나요?",
    "땅콩 알레르기는 있고 우유 알레르기는 없지는 않아.",
    "우유 알레르기는 있고 우유 알레르기는 없어.",
    "내 알레르기 기준으로 보되 우유 알레르기는 있는지 모르겠어.",
])
def test_named_uncertainty_is_not_cancelled_by_other_claims(statement):
    response = build_message_constraint_guard_response(
        statement + " 이 제품 먹어도 돼?", profiles=[Profile(allergies=["대두"])],
    )
    assert response is not None
    assert "우유" in response.answer and "확인" in response.answer


def test_confirmed_turn_allergy_still_warns_while_another_is_uncertain(saved_chat):
    profile = {"profile_id": 1, "profile_name": "나", "allergies": []}
    response = saved_chat("우유 알레르기는 있고 땅콩 알레르기는 있는지 모르겠어. 이 제품 먹어도 돼?",
                          profile, barcode="P00156")
    assert response["allergy_status"] == "conflict"
    assert response["overall_decision"] == "avoid"
    assert "땅콩" in response["answer"] and "확인" in response["answer"]
    assert response["ui_guidance"]["profile_id"] == 1
    saved_chat.detail()
    following = saved_chat("이 제품 먹어도 돼?", profile, barcode="P00156")
    assert following["allergy_status"] == "no_known_match"  # Request-local, not a saved profile change.


def test_registered_conflict_is_not_erased_by_uncertainty(saved_chat):
    response = saved_chat(
        "땅콩 알레르기는 있고 우유 알레르기가 있는지는 모르겠어. 이 제품으로 레시피 추천해줘",
        {"allergies": ["우유"]}, barcode="P00156",
    )
    assert response["recipe"] is None and response["overall_decision"] == "avoid"
    assert response["assessment_summary"]["decision"] == "avoid"
    assert response["assessment_summary"]["scope"] == "recipe"
    assert set(response["missing_information"]) == set(response["assessment_summary"]["missing_information"])


def family():
    return [
        {"profile_id": 1, "profile_name": "나", "allergies": ["새우"]},
        {"profile_id": 2, "profile_name": "아이", "allergies": []},
    ]


def test_owner_clarification_reloads_previous_confirmed_product(saved_chat):
    profiles = family()
    saved_chat("이 제품 원재료 알려줘", profiles[0], profiles=profiles, barcode="8801043035989")
    saved_chat.detail()
    response = saved_chat("땅콩 알레르기도 있는데 이 제품 먹어도 돼?", profiles[0], profiles=profiles)
    assert response["overall_decision"] == "avoid"
    assert response["current_product"]["barcode"] == "8801043035989"


@pytest.mark.parametrize("extra,question", [
    ({"barcode": "NONEXISTENT-CLARIFICATION"}, "이 제품 먹어도 돼?"),
    ({}, "매일우유 먹어도 돼?"),
    ({"product": {"product_name": "새 제품", "allergy": ["새우"], "rawmtrl_nm": "새우"}}, "이 제품 먹어도 돼?"),
    ({"barcode": "8801043035989", "report_number": "NONEXISTENT-CLARIFICATION"}, "이 제품 먹어도 돼?"),
])
def test_clarification_does_not_invent_a_product_or_fallback_to_old_one(saved_chat, extra, question):
    profiles = family()
    saved_chat("이 제품 먹어도 돼?", profiles[0], profiles=profiles, barcode="8801043035989")
    response = saved_chat("땅콩 알레르기도 있는데 " + question, profiles[0], profiles=profiles, **extra)
    assert response["overall_decision"] == "check_required"
    assert response["current_product"] is None
    assert not response["recommended_products"] and not response["product_candidates"]
    saved_chat.detail()
    followup = saved_chat("땅콩 알레르기도 있는데 이 제품 먹어도 돼?", profiles[0], profiles=profiles)
    assert followup["overall_decision"] == "check_required"
    assert followup["current_product"] is None


@pytest.mark.parametrize("verified", [False, True])
def test_service_clarification_only_uses_verified_product_and_public_state(verified):
    profiles = [Profile(**item) for item in family()]
    product = Product(product_id=42, barcode="test-shrimp", product_name="시험용 새우 과자",
                      rawmtrl_nm="새우", allergy=["새우"], facts_verified=verified)
    response = generate_chat_response(profiles[0], product, "땅콩 알레르기도 있는데 이 제품 먹어도 돼?",
                                      active_profiles=profiles)
    assert response.overall_decision == ("avoid" if verified else "check_required")
    assert (response.current_product is not None) == verified
    if verified:
        assert "product_id" not in state(response)["last_confirmed_product"]
        assert response.internal_conversation_state["last_confirmed_product"]["product_id"] == 42


def test_allergy_clarification_does_not_search_or_call_external_ai(monkeypatch):
    import app.services.chat as chat_service
    import app.services.conversation_router as router

    def forbidden(*args, **kwargs):
        pytest.fail("Unresolved allergy must not search/recommend or call the final LLM path")

    monkeypatch.setattr(router, "find_product_candidates_from_message", forbidden)
    monkeypatch.setattr(chat_service, "recommend_catalog_products", forbidden)
    monkeypatch.setattr(chat_service, "generate_final_response", forbidden)
    response = chat(ChatRequest(
        message="땅콩 알레르기도 있는데 이 제품 먹어도 돼?", barcode="8801043035989",
        profiles=[Profile(**item) for item in family()],
    ))
    assert response.overall_decision == "avoid"


@pytest.mark.parametrize("question,intent,expected", [
    ("이 제품 나트륨 몇 mg야?", "nutrition_explain", "100mg"),
    ("이 제품 우유 들어있어?", "ingredient_check", "우유"),
    ("이 제품 요약해줘", "product_summary", "매일우유"),
])
def test_stateless_pending_diet_allows_rule_only_facts(monkeypatch, question, intent, expected):
    import app.services.chat as chat_service
    profile = Profile(**vegetarian())
    first = chat(ChatRequest(message="유제품은 먹는지 모르겠어. 이 제품 레시피 추천해줘",
                             profile=profile, barcode="P00156"))

    def forbidden(*args, **kwargs):
        pytest.fail("Pending-diet label facts must use the rules-only path")

    monkeypatch.setattr(chat_service, "extract_user_intent", forbidden)
    monkeypatch.setattr(chat_service, "generate_final_response", forbidden)
    facts = chat(ChatRequest(message=question, profile=profile, conversation_state=state(first)))
    assert facts.intent == intent
    assert expected in facts.answer
    assert facts.diet_match != "match"
    assert facts.conversation_state.vegetarian_allowances[0].dairy_pending is True
    assert not facts.recipe and not facts.recommended_products
    assert set(facts.missing_information) == set(facts.assessment_summary.missing_information)


def test_pending_diet_facts_keep_family_allergy_warning(saved_chat):
    me = vegetarian()
    child = {"profile_id": 2, "profile_name": "아이", "allergies": ["새우"]}
    saved_chat("나는 유제품을 먹는지 모르겠어. 이 제품 레시피 추천해줘",
               me, profiles=[me, child], barcode="8801043035989")
    response = saved_chat("이 제품 나트륨 몇 mg야?", me, profiles=[me, child])
    assert "mg" in response["answer"] and "새우" in response["answer"]
    assert response["overall_decision"] == "avoid"
    assert response["assessment_summary"]["decision"] == "avoid"
    by_name = {item["profile_name"]: item for item in response["per_profile_results"]}
    assert by_name["아이"]["allergy_status"] == "conflict"
    assert by_name["나"]["diet_match"] != "match"


@pytest.mark.parametrize("question", ["이 제품 먹어도 돼?", "다른 제품 추천해줘", "이 제품 나트륨 알려주고 레시피 추천해줘"])
def test_pending_diet_still_blocks_personalized_decisions(saved_chat, question):
    profile = vegetarian()
    saved_chat("유제품은 먹는지 모르겠어. 이 제품 레시피 추천해줘", profile, barcode="P00156")
    response = saved_chat(question, profile, barcode="P00156")
    assert response["recipe"] is None and not response["recommended_products"]
    assert "확인" in response["answer"]
    assert response["overall_decision"] != "no_known_conflict"


def test_explicit_exclusion_survives_lifting_profile_floor(saved_chat):
    profile = vegetarian(vegetarian_dairy_allowed=False)
    saved_chat("유제품은 빼줘. 이 제품 레시피 추천해줘", profile, barcode="P00156")
    saved_chat.detail()
    response = saved_chat("다른 레시피 더 있어?", {**profile, "vegetarian_dairy_allowed": True})
    assert response["recipe"] is None or response["recipe"]["source_product_blocked"]


def test_profile_floor_change_stateless_roundtrip():
    profile = Profile(**vegetarian(vegetarian_dairy_allowed=False))
    first = chat(ChatRequest(message="유제품은 먹어. 이 제품 레시피 추천해줘", profile=profile, barcode="P00156"))
    assert first.conversation_state.vegetarian_allowances[0].dairy is True
    changed = profile.model_copy(update={"vegetarian_dairy_allowed": True})
    response = chat(ChatRequest(message="다른 레시피 더 있어?", profile=changed, conversation_state=state(first)))
    assert response.recipe is not None and not response.recipe.source_product_blocked
