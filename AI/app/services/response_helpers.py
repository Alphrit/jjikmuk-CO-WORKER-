from typing import List, Optional, Tuple

from app.core.models import ChatResponse, Product, Profile
from app.data.dictionaries import (
    ALLERGY_MAP,
    INGREDIENT_KEYWORDS,
    NUTRITION_RISK_RULES,
    SPECIAL_DIET_KEYWORDS,
    SPECIAL_DIET_RESTRICTIONS,
)
from app.intent.common import TARGET_REFERENCE_KEYWORDS


def get_profile_prefix(profile: Optional[Profile]) -> str:
    if profile and profile.profile_name:
        return f"'{profile.profile_name}' 기준으로 보면"
    return "지금 보이는 정보로는"


def normalize_items(product: Product) -> List[str]:
    items = []
    items.extend(product.ingredient_text_sources())
    items.extend(product.resolved_allergies())
    return [item.lower() for item in items if item]


def find_matching_aliases(product: Product, allergen: str) -> List[str]:
    items = normalize_items(product)
    aliases = ALLERGY_MAP.get(allergen, [allergen])

    matched = []
    for alias in aliases:
        alias_lower = alias.lower()
        for item in items:
            if alias_lower in item:
                matched.append(alias)

    return list(dict.fromkeys(matched))


def allergy_terms_from_product_name(product: Product) -> List[str]:
    if not product.product_name:
        return []

    name = product.product_name.lower()
    return [
        ingredient
        for ingredient in INGREDIENT_KEYWORDS
        if ingredient in name
    ]


def product_contains_allergen(product: Product, allergen: str) -> bool:
    return len(find_matching_aliases(product, allergen)) > 0


def find_triggered_allergies(profile: Profile, product: Product) -> List[Tuple[str, List[str]]]:
    triggered = []
    for allergy in profile.resolved_allergies():
        matched_aliases = find_matching_aliases(product, allergy)
        if matched_aliases:
            triggered.append((allergy, matched_aliases))
    return triggered


def has_meaningful_product_data(product: Optional[Product]) -> bool:
    if product is None:
        return False

    return any([
        bool(product.product_name),
        bool(product.ingredient_text_sources()),
        bool(product.resolved_allergies()),
        bool(product.nutrition),
    ])


def has_allergy_label_data(product: Product) -> bool:
    return bool(product.resolved_allergies())


def has_ingredient_data(product: Product) -> bool:
    return bool(product.ingredient_text_sources())


def format_structured_answer(conclusion: str, reasons: List[str], limitations: List[str], next_steps: List[str]) -> str:
    parts = [f"결론: {conclusion}"]
    if reasons:
        parts.append("이유는 " + " ".join(reasons))
    if limitations:
        parts.append("참고로 " + " ".join(limitations))
    if next_steps:
        parts.append("원하시면 " + " ".join(next_steps))
    return "\n".join(parts)


def compose_answer(*parts: Optional[str]) -> str:
    normalized = [part.strip() for part in parts if part and part.strip()]
    return "\n".join(normalized)


def format_can_i_eat_answer(conclusion: str, reasons: List[str], limitations: List[str], next_steps: List[str]) -> str:
    reason_line = " ".join(reasons[:4]) if reasons else ""
    limitation_line = " ".join(limitations[:2]) if limitations else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(conclusion, reason_line, limitation_line, guidance_line)


def format_binary_check_answer(primary: str, evidence: List[str], caveats: List[str], next_steps: List[str]) -> str:
    evidence_line = " ".join(evidence[:2]) if evidence else ""
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, evidence_line, caveat_line, guidance_line)


def format_nutrition_answer(primary: str, value_line: str, caveats: List[str], next_steps: List[str]) -> str:
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, value_line, caveat_line, guidance_line)


def format_health_answer(primary: str, reasons: List[str], caveats: List[str], next_steps: List[str]) -> str:
    reason_line = " ".join(reasons[:2]) if reasons else ""
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, reason_line, caveat_line, guidance_line)


def format_summary_answer(primary: str, details: List[str], closing: List[str]) -> str:
    detail_line = " ".join([detail for detail in details if detail]) if details else ""
    closing_line = " ".join(closing[:1]) if closing else ""
    return compose_answer(primary, detail_line, closing_line)


def format_general_answer(primary: str, details: List[str], caveats: List[str], next_steps: List[str]) -> str:
    detail_line = " ".join(details[:2]) if details else ""
    caveat_line = " ".join(caveats[:1]) if caveats else ""
    guidance_line = " ".join(next_steps[:1]) if next_steps else ""
    return compose_answer(primary, detail_line, caveat_line, guidance_line)


def resolve_profile_diets(profile: Optional[Profile], message: str) -> List[str]:
    resolved = []

    if profile:
        resolved.extend(profile.resolved_special_diets())

    text = message.strip().lower()
    for canonical, aliases in SPECIAL_DIET_KEYWORDS.items():
        if any(alias in text for alias in aliases):
            resolved.append(canonical)

    return list(dict.fromkeys(resolved))


def scan_disliked_ingredients(profile: Optional[Profile], product: Product) -> List[str]:
    if not profile:
        return []

    items = normalize_items(product)
    matched = []
    for ingredient in profile.disliked_ingredients:
        ingredient_lower = ingredient.lower()
        if any(ingredient_lower in item for item in items):
            matched.append(ingredient)
    return list(dict.fromkeys(matched))


def evaluate_special_diets(profile: Optional[Profile], product: Product, message: str) -> Tuple[List[str], List[str], List[str]]:
    diets = resolve_profile_diets(profile, message)
    items = normalize_items(product)
    allergens = [item.lower() for item in product.resolved_allergies()]

    hard_conflicts = []
    cautions = []
    limitations = []

    for diet in diets:
        restrictions = SPECIAL_DIET_RESTRICTIONS.get(diet)
        if restrictions:
            blocked_ingredients = restrictions.get("blocked_ingredients", [])
            blocked_allergens = restrictions.get("blocked_allergens", [])

            matched_ingredients = [term for term in blocked_ingredients if any(term.lower() in item for item in items)]
            matched_allergens = [term for term in blocked_allergens if any(term.lower() in item for item in allergens)]

            if matched_ingredients or matched_allergens:
                details = ", ".join((matched_ingredients + matched_allergens)[:3])
                hard_conflicts.append(f"{diet} 기준으로 제한되는 성분({details})이 확인됐어요.")

        rule = NUTRITION_RISK_RULES.get(diet)
        if not rule:
            continue

        value = product.nutrition.get(rule["key"])
        if value is None:
            limitations.append(f"{diet} 판단에 필요한 {rule['key']} 정보가 부족해요.")
            continue

        comparison = rule.get("comparison", "max")
        threshold = rule["threshold"]
        if comparison == "min":
            if value < threshold:
                cautions.append(f"{diet} 기준으로는 {rule['reason']} 현재 수치는 {value}예요.")
        else:
            if value >= threshold:
                cautions.append(f"{diet} 기준으로는 {rule['reason']} 현재 수치는 {value}예요.")

    return hard_conflicts, cautions, limitations


def message_mentions_other_target(message: str, profile: Optional[Profile]) -> bool:
    text = message.strip()

    if not any(keyword in text for keyword in TARGET_REFERENCE_KEYWORDS):
        return False

    if profile and profile.profile_name and profile.profile_name in text:
        return False

    return True


def infer_task_type(intent: str) -> str:
    if intent.startswith("general_"):
        return "general_chat"
    return "product_chat"


def build_response(
    intent: str,
    answer: str,
    risk_level: str,
    reasons: List[str],
    profile: Optional[Profile],
    task_type: Optional[str] = None,
    answer_source: str = "rules",
    pipeline_stage: str = "rules",
    recommended_questions: Optional[List[str]] = None,
) -> ChatResponse:
    return ChatResponse(
        task_type=task_type or infer_task_type(intent),
        answer_source=answer_source,
        pipeline_stage=pipeline_stage,
        intent=intent,
        answer=answer,
        risk_level=risk_level,
        reasons=reasons,
        profile_name=profile.profile_name if profile else None,
        recommended_questions=recommended_questions or [],
    )
