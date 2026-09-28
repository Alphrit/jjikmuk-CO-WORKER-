"""Build a user-facing explanation of what a chatbot result actually means.

The legacy ``confidence`` value is a product-field completeness heuristic.  It
must not be presented as a probability that a health answer is correct.  This
module keeps that compatibility field intact while exposing a categorical,
scope-aware summary that a client can render without implying medical certainty.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.core.models import (
    AssessmentSummary,
    ChatResponse,
    Product,
    ProductDataTrust,
    Profile,
)
from app.core.nutrition_basis import (
    nutrition_basis_type,
)
from app.core.public_product import internal_product_reference, public_product_payload
from app.core.public_product_schema import PublicNutritionFacts, PublicProduct
from app.services.response_helpers import assess_product_data_confidence
from app.core.policy_manifest import (
    response_evidence_is_complete,
    response_evidence_matches_manifest,
)
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    label_provenance_limitation_reason,
    label_provenance_supports,
    product_label_content_revision_sha256,
)


_OVERALL_INTAKE_INTENTS = {"can_i_eat", "profile_based_recheck"}
_GENERAL_INTENTS = {
    "general_allergy_guide",
    "general_diet_guide",
    "general_nutrition_guide",
    "alternative_food_recommend",
    "out_of_domain",
}


def _scope(response: ChatResponse) -> str:
    if (
        response.task_type == "safety_triage"
        or response.intent == "emergency_safety"
        or (
            response.safety_guidance is not None
            and response.safety_guidance.urgency in {"urgent", "emergency"}
        )
    ):
        return "emergency_guidance"
    if response.product_candidates:
        return "product_selection"
    # An explicitly unsupported result means no safety assessment was completed
    # for the user's requested scope. Keep it distinct from a partially complete
    # supported check that happens to have missing data.
    if response.assessment_status == "unsupported":
        return "unsupported"
    if response.intent == "recipe_recommend":
        return "recipe"
    if response.intent == "food_compare":
        return "comparison"
    if response.intent in _OVERALL_INTAKE_INTENTS:
        return "overall_intake"
    if response.intent == "ingredient_check":
        return "ingredient"
    if response.intent == "nutrition_explain":
        return "nutrition"
    if response.intent == "product_summary":
        return "product_information"
    if response.intent in _GENERAL_INTENTS or response.intent.startswith("general_"):
        return "general_guidance"
    return "unknown"


def _safety_assessed(scope: str) -> bool:
    return scope in {
        "overall_intake",
        "ingredient",
        "nutrition",
        "recipe",
        "comparison",
    }


def _response_policy_trace_is_exact(response: ChatResponse) -> bool:
    if response.provenance_status != "complete" or not response.applied_rule_ids:
        return False
    try:
        return response_evidence_is_complete(response)
    except (LookupError, ValueError):
        return False


def _response_policy_subset_is_exact(response: ChatResponse) -> bool:
    if not response.applied_rule_ids:
        return False
    try:
        return response_evidence_matches_manifest(response)
    except (LookupError, ValueError):
        return False


def _decision(
    response: ChatResponse,
    scope: str,
    *,
    missing_information: Sequence[str],
) -> str:
    if scope == "emergency_guidance":
        return "urgent_action"
    if scope in {"recipe", "comparison"}:
        # These handlers already aggregate the actual recipe/all comparison
        # targets. Do not reinterpret them as an unknown single-product task.
        return {
            "avoid": "avoid", "caution": "caution",
            "no_known_conflict": "no_conflict_found",
            "check_required": "insufficient_information",
        }[response.overall_decision]
    if not _safety_assessed(scope):
        if scope in {"product_selection", "product_information", "general_guidance"}:
            return "not_assessed"
        return "insufficient_information"
    if response.risk_level == "high":
        return "avoid"
    if response.risk_level == "medium":
        return "caution"
    if (
        response.risk_level == "low"
        and response.assessment_status == "complete"
        and not missing_information
    ):
        return (
            "no_conflict_found"
            if response.provenance_status == "complete"
            else "insufficient_information"
        )
    return "insufficient_information"


def _evidence_status(
    response: ChatResponse,
    *,
    scope: str,
    decision: str,
    product: Optional[Product],
    missing_information: Sequence[str],
) -> str:
    if scope == "emergency_guidance":
        return (
            "sufficient_for_scope"
            if _response_policy_trace_is_exact(response)
            else "insufficient"
        )
    if not _safety_assessed(scope):
        return "not_applicable"
    if scope in {"recipe", "comparison"}:
        return "limited" if decision in {"avoid", "caution", "no_conflict_found"} else "insufficient"
    if product is not None and not product.facts_verified:
        return "insufficient"
    trace_is_exact = _response_policy_trace_is_exact(response)
    if response.applied_rule_ids and not _response_policy_subset_is_exact(response):
        # A response that claims named rules but cannot reproduce their exact
        # manifest evidence is a broken trace, not merely sparse evidence.
        return "insufficient"
    if (
        scope == "nutrition"
        and trace_is_exact
        and product is not None
        and label_provenance_supports(
            product,
            ("nutrition", "nutrition_basis"),
        )
        and not missing_information
    ):
        # This means only that the displayed label value and its basis are
        # traceable. It does not classify the amount as high, low, or suitable.
        return "sufficient_for_scope"
    if missing_information:
        return "limited" if decision in {"avoid", "caution"} else "insufficient"
    if response.assessment_status == "unsupported":
        return "limited" if decision in {"avoid", "caution"} else "insufficient"
    if response.assessment_status == "incomplete":
        return "limited" if decision in {"avoid", "caution"} else "insufficient"
    if decision in {"avoid", "caution"}:
        return "limited"
    return "insufficient"


def _missing_profile_information(profiles: Sequence[Profile]) -> list[str]:
    if not profiles:
        return ["개인 알레르기 프로필"]

    missing: list[str] = []
    if any(not profile.allergy_context_provided for profile in profiles):
        missing.append("프로필의 알레르기 입력 여부")
    return missing


def _missing_information(
    response: ChatResponse,
    *,
    scope: str,
    profiles: Sequence[Profile],
    product: Optional[Product],
) -> list[str]:
    missing: list[str] = list(response.missing_information)

    if scope == "product_selection":
        missing.append("정확한 제품 선택")
    if scope == "unknown" and response.assessment_status != "complete":
        missing.append("질문 의도 또는 평가 기준")
    if scope in {"recipe", "comparison"}:
        missing.extend(_missing_profile_information(profiles))
        if response.recipe is not None:
            missing.extend(response.recipe.diet_limitations)
        elif scope == "recipe" and response.assessment_status == "incomplete" and not missing:
            missing.extend(response.reasons or ["조건을 확인할 수 있는 레시피 재료 정보"])
        if scope == "comparison" and not response.comparison_products:
            missing.append("비교할 제품 2개 이상의 식별 정보")

    product_scopes = {
        "overall_intake",
        "ingredient",
        "nutrition",
        "product_information",
    }
    if scope in product_scopes:
        if product is None:
            missing.append("DB에서 검증된 제품 정보")
        elif not product.facts_verified:
            missing.append("DB에서 검증된 제품 식별 정보")
        else:
            if scope in {"overall_intake", "ingredient", "product_information"}:
                if not product.ingredient_text_sources():
                    missing.append("원재료명")
                if not product.resolved_allergies():
                    missing.append("알레르기 표시")
                if not label_provenance_supports(
                    product,
                    ("ingredients", "allergens"),
                ):
                    missing.append(
                        label_provenance_limitation_reason(
                            product,
                            ("ingredients", "allergens"),
                        )
                    )
            if scope in {
                "overall_intake",
                "nutrition",
                "product_information",
            }:
                if not product.nutrition:
                    missing.append("영양성분")
                elif (
                    scope == "nutrition"
                    or any(
                        {"저염식", "저당식", "저칼로리", "저지방", "고단백"}.intersection(
                            profile.resolved_special_diets()
                        )
                        for profile in profiles
                    )
                ):
                    if nutrition_basis_type(product) == "unknown":
                        missing.append(
                            "영양성분의 1회 제공량·100g/100ml·총 내용량 기준"
                        )
                if product.nutrition and not label_provenance_supports(
                    product,
                    ("nutrition", "nutrition_basis"),
                ):
                    missing.append(
                        label_provenance_limitation_reason(
                            product,
                            ("nutrition", "nutrition_basis"),
                        )
                    )

    if scope == "overall_intake":
        missing.extend(_missing_profile_information(profiles))

    if response.assessment_status == "unsupported":
        missing.append("현재 지원하지 않는 요청 범위")
    if scope in {"overall_intake", "ingredient", "nutrition"} and not _response_policy_trace_is_exact(response):
        missing.append("적용 정책과 제품 정보의 출처·최신성 확인")
    return list(dict.fromkeys(missing))[:20]


def _explanation(decision: str, scope: str) -> str:
    if decision == "urgent_action":
        return (
            "진단 결과가 아니라, 현재 표현된 증상에서 지연하지 말아야 할 행동을 "
            "공식 안내에 따라 우선 제시한 결과예요."
        )
    if scope == "product_selection":
        return "제품 후보만 제시한 단계이며, 아직 어떤 제품의 섭취 안전성도 평가하지 않았어요."
    if scope == "product_information":
        return "제품 정보를 요약한 단계이며, 개인별 섭취 안전성은 아직 평가하지 않았어요."
    if scope == "general_guidance":
        return "일반 정보 안내이며, 특정 제품이나 개인에 대한 섭취 안전성 판정이 아니에요."
    if scope == "nutrition":
        return (
            "제품에 표시된 영양 수치를 해당 기준량 그대로 설명한 결과예요. "
            "수치만으로 높음·낮음이나 개인 적합성을 판정한 것은 아니에요."
        )
    if decision == "avoid":
        return (
            "확인된 평가 범위에서 피해야 할 근거가 있어요. 다른 미확인 항목이 "
            "있더라도 이 위험 근거를 약하게 해석하면 안 돼요."
        )
    if decision == "caution":
        return "확인된 평가 범위에서 주의할 근거가 있어 추가 확인이나 섭취 조절이 필요해요."
    if decision == "no_conflict_found":
        return (
            "평가한 범위에서 충돌 근거를 찾지 못했다는 뜻이며, 완전한 안전이나 "
            "의학적 정답 확률을 뜻하지 않아요. 제품 표시와 제공된 프로필을 대조한 결과예요."
        )
    return "확인하지 못한 정보나 지원하지 않는 기준이 있어 안전하다고 단정할 수 없어요."


def attach_response_assessment(
    response: ChatResponse,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    product: Optional[Product] = None,
) -> ChatResponse:
    """Attach additive, backward-compatible trust metadata to a response."""
    effective_profiles = list(profiles or [])
    scope = _scope(response)
    missing = _missing_information(
        response,
        scope=scope,
        profiles=effective_profiles,
        product=product,
    )
    decision = _decision(
        response,
        scope,
        missing_information=missing,
    )

    data_completeness = None
    data_completeness_reasons: list[str] = []
    if product is not None and product.facts_verified:
        data_completeness, data_completeness_reasons = (
            assess_product_data_confidence(product)
        )

    summary = AssessmentSummary(
        scope=scope,
        decision=decision,
        evidence_status=_evidence_status(
            response,
            scope=scope,
            decision=decision,
            product=product,
            missing_information=missing,
        ),
        safety_assessed=_safety_assessed(scope),
        personalized=bool(effective_profiles),
        repository_identity_matched=(
            product.facts_verified
            if product is not None
            else None
        ),
        label_lineage_status=(
            effective_label_lineage_status(product)
            if product is not None
            else None
        ),
        label_freshness_status=(
            label_freshness_status(product)
            if product is not None
            else None
        ),
        product_data_verified=(
            product.facts_verified
            and effective_label_lineage_status(product) == "complete"
            and label_provenance_supports(
                product,
                PRODUCT_LABEL_FACT_GROUPS,
            )
            if product is not None
            else None
        ),
        explanation=_explanation(decision, scope),
        missing_information=missing,
    )
    updates = {
        "current_product": (
            PublicProduct.model_validate(public_product_payload(product))
            if product is not None and response.current_product is not None
            else response.current_product
        ),
        "product_reference": (
            internal_product_reference(product)
            if product is not None and response.current_product is not None
            else response.product_reference
        ),
        "product_snapshot_provenance": (
            product.label_provenance.model_copy(update={
                "content_revision_sha256": (
                    product_label_content_revision_sha256(product)
                ),
                "lineage_status": effective_label_lineage_status(product),
                "content_revision_binding_status": (
                    content_revision_binding_status(product)
                ),
            })
            if product is not None and response.current_product is not None
            else response.product_snapshot_provenance
        ),
        "product_snapshot_nutrition_basis": (
            product.nutrition_basis.model_copy(deep=True)
            if product is not None and response.current_product is not None
            else response.product_snapshot_nutrition_basis
        ),
        "assessment_summary": summary,
        "data_completeness": data_completeness,
        "data_completeness_reasons": data_completeness_reasons,
        "product_data_trust": (
            ProductDataTrust(
                repository_identity_matched=product.facts_verified,
                label_lineage_status=effective_label_lineage_status(product),
                label_freshness_status=label_freshness_status(product),
                label_observed_at=product.label_provenance.label_observed_at,
                content_revision_sha256=(
                    product_label_content_revision_sha256(product)
                ),
                content_revision_binding_status=(
                    content_revision_binding_status(product)
                ),
            )
            if product is not None
            else None
        ),
        "nutrition_facts": (
            PublicNutritionFacts.model_validate(
                public_product_payload(product)["nutrition"]
            )
            if product is not None and response.current_product is not None
            else response.nutrition_facts
        ),
        "missing_information": list(dict.fromkeys([
            *response.missing_information,
            *missing,
        ])),
        "provenance_status": (
            response.provenance_status
            if response.provenance_status in {"complete", "partial"}
            else "unavailable"
        ),
    }

    # Existing clients may still render only risk_level, assessment_status, and
    # confidence. Do not let those legacy fields say the opposite of the new
    # trust contract: an unproven low result is unknown/incomplete, while the
    # product-field score remains available separately as data_completeness.
    if (
        _safety_assessed(scope)
        and decision == "insufficient_information"
    ):
        updates.update({
            "risk_level": (
                "unknown"
                if response.risk_level == "low"
                else response.risk_level
            ),
            "assessment_status": (
                response.assessment_status
                if response.assessment_status == "unsupported"
                else "incomplete"
                if response.risk_level in {"low", "unknown"}
                else response.assessment_status
            ),
            "confidence": None,
            "confidence_reasons": list(dict.fromkeys([
                *response.confidence_reasons,
                "정책·제품 출처의 최신성이 충분히 추적되지 않아 기존 신뢰도 필드를 제공하지 않음",
            ])),
            "per_profile_results": [
                {
                    **result,
                    "risk_level": (
                        "unknown" if result.get("risk_level") == "low"
                        else result.get("risk_level")
                    ),
                    "assessment_status": (
                        "incomplete"
                        if result.get("risk_level") == "low"
                        and result.get("assessment_status") == "complete"
                        else result.get("assessment_status")
                    ),
                }
                for result in response.per_profile_results
            ],
        })

    return response.model_copy(update=updates)
