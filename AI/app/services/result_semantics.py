"""Independent, UI-facing result axes for the food chatbot.

Allergen decisions are derived only from selected/turn-local profiles and
repository-grounded label fields.  A missing match is intentionally not named
"safe" because absence in incomplete label data cannot prove safety.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.core.models import ChatResponse, Product, Profile
from app.services.response_helpers import (
    canonical_special_diet_name,
    evaluate_special_diets,
    find_cross_contamination_allergies,
    find_triggered_allergies,
    resolve_profile_diets,
    unsupported_profile_allergies,
)


def _has_allergen_evidence(product: Product) -> bool:
    return bool(product.rawmtrl_nm or product.allergy or product.allergy_warning)


def _allergy_status(product: Optional[Product], profiles: Sequence[Profile]) -> str:
    if product is None or not product.facts_verified:
        return "unknown"
    if any(find_triggered_allergies(profile, product) for profile in profiles):
        return "conflict"
    if any(find_cross_contamination_allergies(profile, product) for profile in profiles):
        return "cross_contact"
    if not profiles or any(
        not profile.allergy_context_provided or unsupported_profile_allergies(profile)
        for profile in profiles
    ):
        return "unknown"
    if not _has_allergen_evidence(product):
        return "unknown"
    return "no_known_match"


def _preference_match(product: Optional[Product], profiles: Sequence[Profile]) -> str:
    preferences = [
        value.strip().lower()
        for profile in profiles
        for value in profile.disliked_ingredients
        if value.strip()
    ]
    if not preferences or product is None or not product.facts_verified:
        return "unknown"
    ingredients = (product.rawmtrl_nm or "").lower()
    if not ingredients:
        return "unknown"
    if any(preference in ingredients for preference in preferences):
        return "disliked_found"
    return "matched"


def _diet_match(product: Optional[Product], profiles: Sequence[Profile], message: str = "") -> str:
    diets = list(dict.fromkeys(
        canonical
        for profile in profiles or [None]
        for raw_diet in resolve_profile_diets(profile, message)
        if (canonical := canonical_special_diet_name(raw_diet)) is not None
    ))
    exclusions = any(profile._conversation_exclusions for profile in profiles)
    if (not diets and not exclusions) or product is None or not product.facts_verified:
        return "unknown"

    caution = False
    for profile in profiles or [None]:
        conflicts, cautions, _ = evaluate_special_diets(profile, product, message)
        if conflicts:
            return "not_match"
        caution = caution or bool(cautions)
    if caution:
        return "caution"

    evidence = " ".join(filter(None, [
        product.product_name,
        product.clean_product_name,
        product.rawmtrl_nm,
        product.allergy_warning,
    ])).lower().replace(" ", "")
    ingredient_evidence = bool(product.rawmtrl_nm or product.ingredients)
    statuses: list[str] = []
    if exclusions:
        statuses.append("match" if ingredient_evidence else "unknown")
    for diet in diets:
        if diet == "글루텐프리":
            statuses.append(
                "match"
                if "글루텐프리" in evidence or "gluten-free" in evidence
                else "unknown"
            )
        elif diet == "채식":
            # Generic vegetarian does not reveal whether dairy/egg is allowed.
            statuses.append("match" if ingredient_evidence else "unknown")
        else:
            # Numeric diet labels are claims, not conclusions from an internal
            # cutoff. Without a clear package/product claim the result is unknown.
            aliases = {
                "저염식": ("저염", "저나트륨"),
                "저당식": ("저당", "저당류"),
                "저칼로리": ("저칼로리", "저열량"),
                "저지방": ("저지방",),
                "고단백": ("고단백",),
            }.get(diet, ())
            statuses.append(
                "match" if any(alias in evidence for alias in aliases) else "unknown"
            )

    if "not_match" in statuses:
        return "not_match"
    if "caution" in statuses:
        return "caution"
    if "unknown" in statuses:
        return "unknown"
    return "match"


def attach_result_semantics(
    response: ChatResponse,
    *,
    product: Optional[Product],
    profiles: Sequence[Profile],
    message: str = "",
) -> ChatResponse:
    if response.intent in {"food_compare", "recipe_recommend"} and response.per_profile_results:
        # The comparison handler evaluates every compared product.  Replacing
        # that aggregate with the router's single contextual product (or None)
        # would erase a real conflict found in another comparison target.
        return response
    if response.intent in {"out_of_domain", "emergency_safety", "unknown"}:
        return response.model_copy(update={
            "overall_decision": "check_required",
            "allergy_status": "unknown",
            "diet_match": "unknown",
            "preference_match": "unknown",
        })

    allergy_status = _allergy_status(product, profiles)
    diet_match = _diet_match(product, profiles, message)
    overall_decision = {
        "conflict": "avoid",
        "cross_contact": "caution",
        "no_known_match": "no_known_conflict",
        "unknown": "check_required",
    }[allergy_status]
    if allergy_status == "no_known_match":
        if diet_match in {"not_match", "caution"}:
            overall_decision = "caution"
        elif diet_match == "unknown" and any(
            resolve_profile_diets(profile, message) for profile in profiles or [None]
        ):
            overall_decision = "check_required"
    missing_information = list(response.missing_information)
    if product is not None and not product.facts_verified:
        missing_information.append("DB에서 확인된 제품 정보가 없어요.")
    elif product is not None and not _has_allergen_evidence(product):
        missing_information.append("원재료·알레르기 표시 정보가 부족해요.")

    return response.model_copy(update={
        "overall_decision": overall_decision,
        "allergy_status": allergy_status,
        "diet_match": diet_match,
        "preference_match": _preference_match(product, profiles),
        "missing_information": list(dict.fromkeys(missing_information)),
    })
