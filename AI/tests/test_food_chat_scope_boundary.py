"""Features intentionally outside the allergy-first food chatbot scope."""

from app.core.models import ChatRequest, Profile
from app.main import chat


def test_current_severe_allergic_reaction_keeps_minimal_emergency_guidance():
    response = chat(ChatRequest(
        message="새우를 먹고 지금 숨이 안 쉬어지고 목이 붓는 것 같아요",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert response.intent == "emergency_safety"
    assert response.safety_guidance is not None
    assert response.safety_guidance.urgency == "emergency"
    assert "119" in response.answer
    assert response.answer_source == "rules"


def test_current_food_reaction_wording_keeps_minimal_emergency_guidance():
    response = chat(ChatRequest(
        message="음식을 먹고 목이 붓는 느낌이고 숨쉬기 힘들어요",
    ))

    assert response.intent == "emergency_safety"
    assert response.safety_guidance is not None
    assert response.safety_guidance.urgency == "emergency"
    assert "119" in response.answer


def test_current_named_allergen_reaction_accepts_after_exposure_wording():
    response = chat(ChatRequest(
        message="새우를 먹은 뒤 입술이 붓고 호흡이 어려워요",
    ))

    assert response.intent == "emergency_safety"
    assert response.safety_guidance is not None
    assert "119" in response.answer


def test_medication_question_is_not_treated_as_a_supported_chatbot_feature():
    response = chat(ChatRequest(
        message="타이레놀을 두 알 먹었는데 한 알 더 먹어도 돼?",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent in {"out_of_domain", "unknown"}
    assert response.intent != "emergency_safety"
    assert "복용량" not in response.reasons


def test_broad_health_advice_is_not_a_supported_chatbot_feature():
    response = chat(ChatRequest(message="건강에 좋은 간식 추천해줘"))

    assert response.intent == "out_of_domain"
    assert response.assessment_summary is not None
    assert response.assessment_summary.safety_assessed is False


def test_food_expiry_is_not_presented_as_a_catalog_based_safety_judgment():
    response = chat(ChatRequest(
        message="개봉한 우유가 상한 것 같은데 먹어도 돼?",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent in {"out_of_domain", "unknown", "missing_product"}
    assert "보관 이력" not in response.answer
    assert "폐기" not in response.answer


def test_product_label_report_has_no_free_text_field():
    from pydantic import ValidationError

    from app.core.public_api_schema import ProductLabelMismatchReportRequest

    try:
        ProductLabelMismatchReportRequest.model_validate(
            {
                "field": "allergens",
                "comment": "제 포장지에는 새우가 적혀 있어요",
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("product-label reports must reject free-form text")
