"""Ingredient/instruction checks shared by templates and LLM drafts.

These reject inconsistent instructions, not certify nutrition or food safety.
Unknown LLM prose must additionally pass the closed draft grammar.
"""

import re

from app.services.recipe_validation import valid_amount, unlisted_additions, unsupported_claim


_VEGETABLES = {"오이", "양배추", "토마토", "양파", "당근", "상추", "양상추", "감자", "애호박"}
_FRUITS = {"사과", "바나나", "레몬", "레몬즙"}


def _plain_names(ingredients):
    return {re.sub(r"^(?:삶은|익힌|구운|데친)\s*", "", item["name"]) for item in ingredients}


def product_method_conflict(steps, product, ingredients=()):
    if product is None:
        return False
    category = next((value.lower() for value in (
        product.category_detail, product.category_small,
        product.prdlst_dcnm, product.product_name,
    ) if value), "")
    if any(term in category for term in ("과자", "스낵", "에너지바", "단백질바", "두유바", "칩", "빵", "크래커")):
        return False
    if not any(term in category for term in ("우유", "두유", "음료", "드링크", "주스")):
        return False
    product_name = product.product_name or ""
    others = _plain_names(ingredients) - {product_name}
    for step in steps:
        # A generic reference can include the liquid; do not assume it names
        # another ingredient, even if the action is far from the reference.
        refers_to_product = (product_name and product_name in step) or re.search(
            r"(?<![가-힣])(?:이\s*제품|제품|모든\s*재료|재료)(?:을|를|은|는|과|와|에|로|가|이)?(?:\s|[.,]|$)", step,
        )
        action_text = step.replace(product_name, "제품") if product_name else step
        if re.search(r"부숴|부수|부순|자르|잘라|썰|굽|구워|구운|볶", action_text):
            # An omitted subject also cannot silently exclude the selected
            # liquid. Only an explicitly named, listed non-liquid is separate.
            separate_ingredient = any(name in step for name in others)
            separate_ingredient |= "채소" in step and bool(others & _VEGETABLES)
            separate_ingredient |= "과일" in step and bool(others & _FRUITS)
            if refers_to_product or not separate_ingredient:
                return True
    return False


def composition_issues(ingredients, steps, substitutions=(), product=None):
    """Accept dict ingredient entries from either generation path."""
    names = [item["name"] for item in ingredients]
    issues = []
    if not names or len(names) != len(set(names)) or any(not valid_amount(item["amount"]) for item in ingredients):
        issues.append("invalid_ingredients")
    text = [*steps, *substitutions]
    if unlisted_additions(text, names):
        issues.append("unlisted_ingredient")
    if unsupported_claim(" ".join(text)):
        issues.append("unsupported_claim")
    plain_names = _plain_names(ingredients)
    # Product names such as 채소주스/과일소스 refer to listed ingredients,
    # not an instruction to introduce a separate vegetable or fruit.
    reference_text = list(text)
    for name in sorted(set(names), key=len, reverse=True):
        reference_text = [step.replace(name, "재료항목") for step in reference_text]
    for generic, category_names in (("채소", _VEGETABLES), ("과일", _FRUITS)):
        if any(generic in step for step in reference_text) and not plain_names & category_names:
            issues.append("unresolved_ingredient_reference")
    if product_method_conflict(text, product, ingredients):
        issues.append("product_method_conflict")
    return issues
