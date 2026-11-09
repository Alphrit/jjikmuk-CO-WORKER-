"""Sentences of registered label facts for one product.

Used by the backend-verdict path (backend_analysis), which is the only place a
v2 request states product facts; legacy handlers serve the legacy contract.
Facts come only from the request's product; absence is never asserted from an
unverified label.
"""

import re

_INTOLERANCE_INGREDIENTS = ((r"(?:유당|락토스|젖당)\s*불내증", "우유"), (r"글루텐\s*불내증|셀리악", "밀"))


def product_facts_text(product, message, intent):
    """The label facts an intent asks for, as lines ending in a newline."""
    if product is None:
        return ""
    if intent == "nutrition_explain":
        from app.intent.text import detect_nutrition_keywords
        from app.services.handlers import NUTRITION_LABELS, NUTRITION_UNITS
        requested = detect_nutrition_keywords(message)
        # detect_nutrition_keywords returns display names; expose only requested
        # nutrients, or all registered values for a broad nutrition request.
        keys = [key for key, label in NUTRITION_LABELS.items() if label in requested] if requested else list(NUTRITION_LABELS)
        lines = [f"{NUTRITION_LABELS[key]}: {product.nutrition[key]:g}{NUTRITION_UNITS[key]}"
                 if key in product.nutrition else f"{NUTRITION_LABELS[key]}: 정보 없음" for key in keys]
        return "등록된 영양정보 — " + ", ".join(lines) + ". 기준량이 전달되지 않아 1회분·한 봉지 섭취량으로 환산할 수 없어요.\n"
    if intent in {"ingredient_check", "product_summary"}:
        ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
        allergens = product.resolved_allergies()
        text = asked_ingredient_text(product, message) if intent == "ingredient_check" else ""
        text += ("등록된 원재료: " + ingredients[:1500] + "\n") if ingredients else "등록된 원재료 정보가 부족해요.\n"
        if allergens:
            text += "등록된 알레르기 표시: " + ", ".join(allergens) + "\n"
        return text
    return ""


def asked_ingredient_text(product, message):
    """Answer the asked ingredient from registered label text; never assert absence."""
    from app.intent.text import detect_ingredient_keyword
    if product.product_name:
        # '새우깡' must not read as a question about 새우.
        message = message.replace(product.product_name, " ")
    ingredient = detect_ingredient_keyword(message)
    return ingredient_label_text(product, ingredient) if ingredient else ""


def intolerance_text(product, message):
    """An intolerance is not a backend allergy axis: say so and show the related label fact."""
    for pattern, ingredient in _INTOLERANCE_INGREDIENTS:
        found = re.search(pattern, message)
        if found:
            return (f"{found[0]}은 알레르기 판정 항목이 아니어서 아래 판정에 반영되지 않았어요. "
                    + ingredient_label_text(product, ingredient))
    return ""


def ingredient_label_text(product, ingredient):
    from app.services.response_helpers import find_cross_contamination_aliases, find_matching_aliases
    matched = find_matching_aliases(product, ingredient)
    if matched:
        return f"등록된 표시에서 {ingredient} 관련 표기가 확인돼요: {', '.join(matched[:3])}.\n"
    shared = find_cross_contamination_aliases(product, ingredient)
    if shared:
        return f"{ingredient}의 직접 함유 표기는 찾지 못했지만 교차오염 가능성 표시가 있어요: {', '.join(shared[:3])}.\n"
    return (f"등록된 원재료·알레르기 표시에서 {ingredient} 관련 표기는 찾지 못했어요. "
            "표시 정보가 빠졌을 수 있어 들어 있지 않다고 단정할 수는 없어요.\n")
