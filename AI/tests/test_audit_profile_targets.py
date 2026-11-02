import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.core.models import Profile
from app.services.chat import generate_chat_response
from app.services.condition_subject import resolve_condition_subject, unresolved_profile_target_response
from test_chatbot import _verified_product


@pytest.mark.parametrize("message", [
    "엄마는 먹어도 돼?", "그럼 엄마는?", "엄마 기준으로 다시 봐줘",
    "엄마랑 아빠 둘 다 먹어도 돼?", "엄마한테는 괜찮아?",
    "엄마를 기준으로 다시 봐줘", "엄마하고 아빠는 먹어도 돼?",
    "엄마에게도 괜찮아?", "엄마와 아빠 둘 다 먹어도 돼?",
    "엄마만은 먹어도 돼?",
])
@pytest.mark.parametrize("include_off", [True, False])
def test_unselected_target_is_not_replaced_by_selected_dad(message, include_off, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_DB_PROVIDER", "memory")
    mom = Profile(profile_name="엄마", profile_id=11, allergies=["우유"], is_active=False)
    dad = Profile(profile_name="아빠", profile_id=12, allergies=["땅콩"])
    known = [mom, dad] if include_off else [dad]
    state = {"last_confirmed_product": {"barcode": "880000007001"}, "last_intent": "can_i_eat"}
    response = generate_chat_response(
        dad, _verified_product(ingredients="원유", allergy=["우유"]), message,
        active_profiles=[dad], known_profiles=known, conversation_state=state,
    )
    assert response.overall_decision == "check_required"
    assert response.allergy_status == "unknown"
    assert response.per_profile_results == []
    assert response.active_profile_names == ["아빠"]
    assert "엄마" in response.answer and "ON" in response.answer
    assert response.internal_conversation_state == state

    def unexpected_lookup(**kwargs):
        pytest.fail("unselected target must be clarified before product lookup")
    monkeypatch.setattr(api, "resolve_product_context", unexpected_lookup)
    with TestClient(api.app) as client:
        public = client.post("/chat", json={
            "message": message, "profiles": [p.model_dump(mode="json") for p in known],
            "conversation_state": state,
        })
    assert public.status_code == 200
    assert public.json()["overall_decision"] == "check_required"
    assert public.json()["per_profile_results"] == []


@pytest.mark.parametrize("message", [
    "그럼 먹어도 돼?", "아빠는 먹어도 돼?", "엄마손파이 먹어도 돼?",
    "엄마가 산 과자 원재료 알려줘", "우유 알레르기는 무슨 뜻이야?",
    "엄마 말고 아빠 기준으로 봐줘", "엄마는 끄고 아빠 기준으로 봐줘",
    "엄마가 산 과자 먹어도 돼?",
    "엄마를 제외하고 아빠 기준으로 봐줘", "엄마와플 먹어도 돼?",
])
def test_target_guard_leaves_current_and_factual_requests_alone(message):
    dad = Profile(profile_name="아빠", allergies=[])
    mom = Profile(profile_name="엄마", is_active=False)
    assert unresolved_profile_target_response(message, [dad], [mom, dad]) is None


@pytest.mark.parametrize("message", [
    "엄마한테는 우유 알레르기가 있어", "엄마에게도 우유 알레르기가 있어",
    "엄마랑 아빠 모두 우유 알레르기가 있어",
])
def test_unselected_condition_owner_with_particle_is_not_assigned_to_dad(message):
    dad = Profile(profile_name="아빠", allergies=[])
    mom = Profile(profile_name="엄마", is_active=False)
    assert resolve_condition_subject(message, [dad], [mom, dad]) is None


def test_emergency_takes_precedence_over_off_target():
    response = generate_chat_response(
        Profile(profile_name="아빠", allergies=[]), None,
        "엄마가 땅콩을 먹고 지금 숨이 안 쉬어져요", known_profiles=[Profile(profile_name="엄마", is_active=False)],
    )
    assert response.intent == "emergency_safety"
