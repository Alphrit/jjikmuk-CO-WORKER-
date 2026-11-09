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
        from app.data.dictionaries import NUTRITION_KEYWORDS
        requested = set(detect_nutrition_keywords(message))
        # The detector names nutrients by NUTRITION_KEYWORDS ('칼로리'), the
        # labels by display name ('열량'); match through the aliases. Show the
        # requested nutrients, or all of them when none matched (never an
        # empty list, which printed '등록된 영양정보 — .').
        keys = [key for key, label in NUTRITION_LABELS.items()
                if any(label in NUTRITION_KEYWORDS.get(name, (name,)) for name in requested)] or list(NUTRITION_LABELS)
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
    from app.services.response_helpers import allergy_terms_from_product_name, object_particle
    if ingredient in allergy_terms_from_product_name(product):
        # '땅콩빼빼로' with no 땅콩 in its registered label: the name itself is
        # evidence, so never let a missing label field read as reassurance.
        subject = "이" if object_particle(ingredient) == "을" else "가"
        return (f"제품명에 '{ingredient}'{subject} 들어 있어요. 등록된 표시에는 없지만 들어 있을 가능성이 높으니 "
                "포장지의 알레르기 표시를 꼭 확인하고, 확인하기 전에는 드시지 마세요.\n")
    shared = find_cross_contamination_aliases(product, ingredient)
    if shared:
        return f"{ingredient}의 직접 함유 표기는 찾지 못했지만 교차오염 가능성 표시가 있어요: {', '.join(shared[:3])}.\n"
    return (f"등록된 원재료·알레르기 표시에서 {ingredient} 관련 표기는 찾지 못했어요. "
            "표시 정보가 빠졌을 수 있어 들어 있지 않다고 단정할 수는 없어요.\n")


def name_only_allergens(product, profile):
    """Profile allergens named in the product name but missing from its label ('바나나맛우유')."""
    from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
    from app.services.response_helpers import allergy_terms_from_product_name, find_matching_aliases
    wanted = {allergen.lower() for allergen in profile.resolved_allergies()}
    found = []
    for term in allergy_terms_from_product_name(product):
        aliases = {alias.lower() for alias in ALLERGEN_CANONICAL_ALIASES.get("대두" if term == "콩" else term, ())} | {term}
        if aliases & wanted and not find_matching_aliases(product, term):
            found.append(term)
    return found


def allergen_display_name(allergen):
    """'milk' (a backend ID) → '우유'; unknown terms are returned as given."""
    from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
    lowered = allergen.lower()
    for name, aliases in ALLERGEN_CANONICAL_ALIASES.items():
        if lowered == name or lowered in {alias.lower() for alias in aliases}:
            return name
    return allergen


def unknown_allergy_guidance(product, profile):
    """What to do when the backend could not judge allergies for this person.

    The verdict stays UNKNOWN; this only replaces a generic sentence with the
    person's own next step: avoid when the name states their allergen, else
    check the package for their allergens by name.
    """
    from app.services.response_helpers import object_particle
    def subject(word):
        return "이" if object_particle(word) == "을" else "가"
    named = name_only_allergens(product, profile)
    if named:
        term = named[0]
        return (f"피하는 게 좋아요. 제품명에 '{term}'{subject(term)} 들어 있어 {term}{subject(term)} 들어 있을 가능성이 높아요. "
                "등록된 알레르기 표시가 없어 DB로는 확인되지 않았어요.")
    names = list(dict.fromkeys(allergen_display_name(item) for item in profile.resolved_allergies()))
    if not names:
        return None
    listed = ", ".join(names)
    labels = product.resolved_allergies()
    if labels:
        from app.services.response_helpers import find_matching_aliases
        if any(find_matching_aliases(product, name) for name in names):
            return None
        return (f"확인한 결과 등록된 알레르기 표시({', '.join(labels)})에는 {listed}{subject(names[-1])} 보이지 않아요. "
                "다만 표시 정보가 완전한지는 확인되지 않으니, 불안하시면 드시기 전에 제품 포장지를 확인해 주세요.")
    if product.rawmtrl_nm or product.ingredients:
        return None
    # Say what was checked and why it is not a 'no': the label is simply absent.
    return (f"확인한 결과 등록된 정보에서는 {listed} 성분이 보이지 않아요. 다만 이 상품은 알레르기 표시가 등록되어 "
            "있지 않으니, 불안하시면 드시기 전에 제품 포장지를 확인해 주세요.")
