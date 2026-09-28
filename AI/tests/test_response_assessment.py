from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.api import main as api_main
from app.api.main import chat
from app.core.models import ChatRequest, ChatResponse, NutritionBasis, Product, Profile
from app.infrastructure.conversations import ConversationMessage
from app.services.chat import generate_chat_response
from app.services.response_assessment import attach_response_assessment


def _complete_profile(*, allergies: list[str] | None = None) -> Profile:
    return Profile(
        profile_name="나",
        allergies=allergies or [],
        conditions=[],
        special_diets=[],
    )


def _complete_product(*, ingredients: str = "귀리, 코코아", allergy: str = "땅콩") -> Product:
    return Product(
        product_id=9101,
        product_name="테스트 귀리바",
        rawmtrl_nm=ingredients,
        allergy=[allergy],
        nutrition={"kcal": 120, "sugars": 4, "protein": 6, "sodium": 90},
        nutrition_basis={"basis_type": "per_serving", "serving_size": "40g"},
        barcode="8800000009101",
    )


def test_complete_low_result_fails_closed_until_policy_and_product_provenance_exist():
    response = generate_chat_response(
        profile=_complete_profile(),
        product=_complete_product(),
        message="이 제품 먹어도 돼?",
    )

    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "overall_intake"
    assert summary.decision == "insufficient_information"
    assert summary.evidence_status == "insufficient"
    assert summary.safety_assessed is True
    assert summary.personalized is True
    assert summary.repository_identity_matched is True
    assert summary.label_lineage_status == "unavailable"
    assert summary.product_data_verified is False
    assert any("출처·최신성" in item for item in summary.missing_information)
    assert response.data_completeness == 1.0
    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.confidence is None
    assert "크게 걱정" not in response.answer


def test_known_allergen_conflict_is_an_avoid_decision_even_when_other_data_is_missing():
    response = generate_chat_response(
        profile=_complete_profile(allergies=["우유"]),
        product=_complete_product(ingredients="우유, 설탕", allergy="우유"),
        message="이거 먹어도 돼?",
    )

    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "overall_intake"
    assert summary.decision == "avoid"
    assert summary.safety_assessed is True
    assert response.risk_level == "high"


def test_missing_profile_never_becomes_a_personalized_safe_decision():
    response = generate_chat_response(
        profile=None,
        product=_complete_product(),
        message="이 제품 먹어도 돼?",
    )

    summary = response.assessment_summary
    assert summary is not None
    assert summary.decision == "insufficient_information"
    assert summary.evidence_status == "insufficient"
    assert summary.personalized is False
    assert "개인 알레르기 프로필" in summary.missing_information


def test_product_summary_explicitly_says_safety_was_not_assessed():
    base = ChatResponse(
        task_type="product_chat",
        intent="product_summary",
        answer="제품 정보를 요약했어요.",
        risk_level="unknown",
        assessment_status="incomplete",
    )

    response = attach_response_assessment(
        base,
        profiles=[_complete_profile()],
        product=_complete_product(),
    )

    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "product_information"
    assert summary.decision == "not_assessed"
    assert summary.safety_assessed is False
    assert "안전성은 아직 평가하지 않았어요" in summary.explanation


def test_explicitly_unsupported_scope_never_claims_safety_was_assessed():
    base = ChatResponse(
        task_type="product_chat",
        intent="health_risk_check",
        answer="현재 지원하지 않는 요청 범위예요.",
        risk_level="unknown",
        assessment_status="unsupported",
    )

    response = attach_response_assessment(
        base,
        profiles=[_complete_profile()],
        product=_complete_product(),
    )

    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "unsupported"
    assert summary.decision == "insufficient_information"
    assert summary.safety_assessed is False
    assert summary.evidence_status == "not_applicable"


def test_nutrition_result_without_serving_basis_cannot_claim_sufficient_evidence():
    base = ChatResponse(
        task_type="product_chat",
        intent="nutrition_explain",
        answer="영양 수치를 정리했어요.",
        risk_level="low",
        assessment_status="complete",
    )

    response = attach_response_assessment(
        base,
        profiles=[_complete_profile()],
        product=_complete_product().model_copy(
            update={"nutrition_basis": NutritionBasis()}
        ),
    )

    summary = response.assessment_summary
    assert summary is not None
    assert any(
        "영양성분" in item and "기준" in item
        for item in summary.missing_information
    )
    assert summary.decision == "insufficient_information"
    assert summary.evidence_status == "insufficient"


def test_pending_product_candidates_are_selection_not_a_safety_result():
    response = chat(ChatRequest(message="단백질바 먹어도 돼?"))

    assert response.product_candidates
    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "product_selection"
    assert summary.decision == "not_assessed"
    assert summary.safety_assessed is False
    assert "정확한 제품 선택" in summary.missing_information


def test_emergency_pipeline_exposes_urgent_action_scope():
    response = chat(ChatRequest(message="방금 땅콩 먹고 입술이 붓고 숨을 못 쉬어"))

    summary = response.assessment_summary
    assert summary is not None
    assert summary.scope == "emergency_guidance"
    assert summary.decision == "urgent_action"
    assert summary.evidence_status == "sufficient_for_scope"
    assert response.safety_guidance is not None


def test_legacy_confidence_is_still_bounded_but_not_the_decision():
    with pytest.raises(ValidationError):
        ChatResponse(
            intent="unknown",
            answer="확인이 필요해요.",
            risk_level="unknown",
            confidence=1.1,
        )

    base = ChatResponse(
        intent="product_summary",
        answer="제품 정보를 요약했어요.",
        risk_level="unknown",
        assessment_status="incomplete",
        confidence=1.0,
    )
    response = attach_response_assessment(base, product=_complete_product())

    assert response.data_completeness == 1.0
    assert response.assessment_summary is not None
    assert response.assessment_summary.decision == "not_assessed"


def test_saved_assistant_snapshot_restores_the_same_trust_contract():
    response = generate_chat_response(
        profile=_complete_profile(allergies=["우유"]),
        product=_complete_product(ingredients="우유, 설탕", allergy="우유"),
        message="이거 먹어도 돼?",
    )
    metadata = api_main._assistant_message_metadata(
        response,
        request_fingerprint="f" * 64,
        write_attempt_id="a" * 32,
    )
    stored = ConversationMessage(
        message_id="message-1",
        conversation_id="conversation-1",
        sequence=2,
        role="assistant",
        content=response.answer,
        metadata=metadata,
        turn_id="turn-1",
        created_at=datetime.now(timezone.utc),
    )

    restored = api_main._response_from_assistant_message(stored)

    assert restored.assessment_summary == response.assessment_summary
    assert restored.data_completeness == response.data_completeness
    assert restored.data_completeness_reasons == response.data_completeness_reasons
