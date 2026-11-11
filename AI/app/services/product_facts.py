"""Sentences of registered label facts for one product.

Used by the backend-verdict path (backend_analysis), which is the only place a
v2 request states product facts; legacy handlers serve the legacy contract.
Facts come only from the request's product; absence is never asserted from an
unverified label.
"""

import re

# Product.nutrition keys → backend nutrition-v1 value names.
_CONTRACT_KEYS = {"kcal": "energyKcal", "carbohydrate": "carbsG", "protein": "proteinG", "fat": "fatG",
                  "sugars": "sugarG", "sodium": "sodiumMg", "cholesterol": "cholesterolMg"}

_INTOLERANCE_INGREDIENTS = ((r"(?:유당|락토스|젖당)\s*불내증", "우유"), (r"글루텐\s*불내증|셀리악", "밀"))


def product_facts_text(product, message, intent):
    """The label facts an intent asks for, as lines ending in a newline."""
    if product is None:
        return ""
    if intent == "nutrition_explain":
        from app.intent.text import detect_nutrition_keywords
        from app.services.handlers import NUTRITION_LABELS, NUTRITION_UNITS
        from app.data.dictionaries import NUTRITION_KEYWORDS
        # Nutrients the backend never sends: say so instead of answering '지방'
        # for '트랜스지방' (substring) or listing every nutrient.
        missing = [name for name in ("트랜스지방", "식이섬유") if name in message.replace(" ", "")]
        for name in missing:
            message = message.replace(name, " ")
        requested = set(detect_nutrition_keywords(message))
        if missing and not requested:
            found = [label_text_value(product, name) for name in missing]
            if all(found):
                text = ("영양성분 표시 원문 기준 — " + ", ".join(f"{name}: {value}" for name, value in zip(missing, found))
                        + (f" ({label_text_basis(product)[2:]})" if label_text_basis(product) else "") + ".")
                if "트랜스지방" in missing and re.fullmatch(r"0(?:\.0+)?g", found[missing.index("트랜스지방")]):
                    text += " 트랜스지방 0g은 0.2g 미만일 때도 이렇게 표시할 수 있어요."
                return text + "\n"
            return "등록된 영양정보 — " + ", ".join(f"{name}: {value or '정보 없음'}" for name, value in zip(missing, found)) + ". 포장지의 영양성분표를 확인해 주세요.\n"
        # The detector names nutrients by NUTRITION_KEYWORDS ('칼로리'), the
        # labels by display name ('열량'); match through the aliases. Show the
        # requested nutrients, or all of them when none matched (never an
        # empty list, which printed '등록된 영양정보 — .').
        keys = [key for key, label in NUTRITION_LABELS.items()
                if any(label in NUTRITION_KEYWORDS.get(name, (name,)) for name in requested)] or list(NUTRITION_LABELS)
        from app.core.nutrition_basis import has_explicit_nutrition_basis, label_text_serving, nutrition_basis_label
        contract = getattr(product, "nutrition_contract", None)
        basis_known = has_explicit_nutrition_basis(product)
        # No basis field, but the label text the backend sent may state it ('30g당 160kcal').
        serving = None if basis_known else label_text_serving(product)
        package = contract.per_package if contract is not None else None
        hundred = (contract.per100g or contract.per100ml) if contract is not None else None
        wants_hundred = bool(re.search(r"100\s*(?:g|ml|그램|밀리)", message))

        def number(value):
            return f"{round(float(value), 1):,.10g}"

        def line(key):
            label, unit = NUTRITION_LABELS[key], NUTRITION_UNITS[key]
            value = product.nutrition.get(key)
            if value is None:
                # Missing structured value: the label text the backend sent may still have it.
                text_value = label_text_value(product, label)
                return f"{label}: {text_value} (영양성분 표시 원문{label_text_basis(product)})" if text_value else f"{label}: 정보 없음"
            if serving is not None:
                return f"{label}: {number(value)}{unit} ({serving[0]}당)"
            if not basis_known:
                # The closing sentence says the basis is unknown once, not on every line.
                return f"{label}: {number(value)}{unit}"
            text = f"{label}: {number(value)}{unit} ({nutrition_basis_label(product)})"
            # Backend-computed values; never multiplied again here.
            contract_key = _CONTRACT_KEYS.get(key)
            if package is not None and package.values.get(contract_key) is not None:
                text += f", 한 봉지(총 {number(package.amount)}{package.unit}) 환산 {number(package.values[contract_key])}{unit}"
            if (wants_hundred and hundred is not None and hundred.values.get(contract_key) is not None
                    and nutrition_basis_label(product) != f"100{hundred.unit} 기준"):
                text += f", 100{hundred.unit}당 환산 {number(hundred.values[contract_key])}{unit}"
            return text

        order = ["kcal", "carbohydrate", "sugars", "protein", "fat", "saturated_fat", "sodium", "cholesterol"]
        keys = sorted(keys, key=lambda key: order.index(key) if key in order else len(order))
        lines = [line(key) for key in keys]
        if all(text.endswith(": 정보 없음") for text in lines):
            # Nothing to state a basis for: say what is missing, not why it cannot be scaled.
            missing_labels = ", ".join(NUTRITION_LABELS[key] for key in keys)
            return f"등록된 영양정보에 {missing_labels} 값이 없어요. 포장지의 영양성분표를 확인해 주세요.\n"
        if serving is not None:
            tail = f" 포장지 영양성분 표시의 {serving[0]}당 수치예요" + (f"(총 내용량 {serving[1]})" if serving[1] else "") + "."
            if wants_hundred and serving[0] not in {"100g", "100ml"}:
                tail += f" 100g당 환산값은 전달되지 않아 표시 기준인 {serving[0]}당 수치로 안내했어요."
            elif re.search(r"봉지|한\s*통|전체|다\s*먹", message):
                tail += " 한 봉지 전체 값은 따로 전달되지 않아 계산하지 않았어요."
        elif not basis_known:
            tail = " 기준량이 전달되지 않아 1회분·한 봉지 섭취량으로 환산할 수 없어요."
        elif package is not None:
            tail = " 환산값은 원본 수치를 계산한 값이라 포장지 표기와 소수점이 다를 수 있어요."
        else:
            tail = {
                "PACKAGE_QUANTITY_UNKNOWN": " 상품 전체 용량이 없어 한 봉지 기준으로는 환산하지 못했어요.",
                "UNIT_MISMATCH": " 기준량과 전체 용량의 단위(g/ml)가 달라 한 봉지 기준으로는 환산하지 못했어요.",
            }.get(contract.package_calculation_reason if contract is not None else None,
                  " 한 봉지 기준 환산값은 전달되지 않았어요.")
        return "등록된 영양정보 — " + ", ".join(lines) + "." + tail + "\n"
    if intent in {"ingredient_check", "product_summary"}:
        ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
        allergens = product.resolved_allergies()
        text = asked_ingredient_text(product, message) if intent == "ingredient_check" else ""
        text += (("등록된 원재료: " + _readable_ingredients(ingredients) + "\n") if ingredients
                 else "이 상품은 원재료가 등록되어 있지 않아 성분을 안내할 수 없어요. 포장지의 원재료명을 확인해 주세요.\n")
        if allergens:
            text += "등록된 알레르기 표시: " + ", ".join(allergens) + "\n"
        return text
    return ""


def _readable_ingredients(text, limit=15):
    """'향료, 향료, …' → each item once, the first `limit` shown and the rest counted."""
    items = list(dict.fromkeys(item.strip() for item in re.split(r"\s*,\s*", text[:1500]) if item.strip()))
    shown = ", ".join(items[:limit])
    return shown + (f" 외 {len(items) - limit}가지" if len(items) > limit else "")


def label_text_value(product, label):
    """'트랜스지방0g' from the backend's raw label text, or None."""
    text = getattr(product, "nutrient_text", None) or ""
    found = re.search(rf"(?<![가-힣]){re.escape(label)}\s*([\d.,]+)\s*(mg|g|kcal)", text)
    return f"{found[1]}{found[2]}" if found else None


def label_text_basis(product):
    """', 30g당' when the raw label text states its basis, else ''."""
    text = getattr(product, "nutrient_text", None) or ""
    found = re.search(r"([\d.]+\s*(?:g|ml|mL))\s*당", text)
    return f", {found[1].replace(' ', '')}당" if found else ""


def asked_ingredient_text(product, message):
    """Answer the asked ingredient from registered label text; never assert absence."""
    from app.intent.text import detect_ingredient_keyword
    message = strip_product_name_words(product, message)
    ingredient = detect_ingredient_keyword(message)
    if not ingredient and re.search(r"유당|락토스|젖당", message):
        return "유당은 우유에 들어 있는 당이라 우유 표시로 확인했어요. " + ingredient_label_text(product, "우유")
    if not ingredient and "글루텐" in message:
        # Gluten is checked through its labelled source; 보리·호밀 are not label allergens.
        return "글루텐은 밀·보리·호밀에 들어 있는 단백질이라 밀 표시로 확인했어요. " + ingredient_label_text(product, "밀")
    return ingredient_label_text(product, ingredient) if ingredient else ""


def strip_product_name_words(product, message):
    """'새우깡' must not read as a question about 새우, even when the selected
    product is '농심 새우깡 90G' or '쌀새우깡'. Words that are ingredients
    themselves ('바나나맛우유에 우유 있어?' → 우유) are kept."""
    from app.data.dictionaries import INGREDIENT_KEYWORDS
    name = product.product_name or ""
    if name:
        message = message.replace(name, " ")
    compact_name = re.sub(r"\s+", "", name)
    words = re.findall(r"[가-힣A-Za-z0-9]+", message)
    for word in words:
        stem = re.sub(r"(?:은|는|이|가|에|에는|의|도|을|를)$", "", word)
        if len(stem) >= 2 and stem in compact_name and stem not in INGREDIENT_KEYWORDS:
            message = re.sub(rf"(?<![가-힣A-Za-z0-9]){re.escape(word)}(?![가-힣A-Za-z0-9])", " ", message)
    return message


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
        # A fact about the product; whether to avoid it is said per person
        # (unknown_allergy_guidance), only to those allergic to it.
        return (f"제품명에 '{ingredient}'{subject} 들어 있어 {ingredient}{subject} 들어 있을 가능성이 높아요. "
                "다만 등록된 원재료·알레르기 표시로는 확인되지 않았으니 포장지의 알레르기 표시를 확인해 주세요.\n")
    shared = find_cross_contamination_aliases(product, ingredient)
    if shared:
        return f"{ingredient}의 직접 함유 표기는 찾지 못했지만 교차오염 가능성 표시가 있어요: {', '.join(shared[:3])}.\n"
    return (f"등록된 원재료·알레르기 표시에서 {ingredient} 관련 표기는 찾지 못했어요. "
            "표시 정보가 빠졌을 수 있어 들어 있지 않다고 단정할 수는 없어요.\n")


def name_only_allergens(product, profile):
    """Profile allergens named in the product name but missing from its label ('바나나맛우유', '아몬드빼빼로' for 견과류)."""
    from app.services.response_helpers import aliases_for_allergen, allergy_terms_from_product_name, find_matching_aliases
    terms = allergy_terms_from_product_name(product)
    found = []
    for allergen in profile.resolved_allergies():
        # A parent such as 견과류 expands to its children (아몬드, 호두…).
        aliases = {alias.lower() for alias in aliases_for_allergen(allergen)}
        if find_matching_aliases(product, allergen):
            continue
        found.extend(term for term in terms if term.lower() in aliases and term not in found)
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
                "등록된 알레르기 표시가 없어 확인되지는 않았어요.")
    from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
    from app.services.response_helpers import find_cross_contamination_aliases
    names = list(dict.fromkeys(allergen_display_name(item) for item in profile.resolved_allergies()))
    if not names or any(name not in ALLERGEN_CANONICAL_ALIASES for name in names):
        # An allergy this service cannot match keeps the generic UNKNOWN text.
        return None
    shared = [name for name in names if find_cross_contamination_aliases(product, name)]
    if shared:
        listed_shared = ", ".join(shared)
        return (f"등록된 표시에 {listed_shared}{subject(shared[-1])} 같은 제조시설 등에서 섞일 수 있다는 주의 문구가 있어요. "
                "드시기 전에 제품 포장지의 알레르기 표시를 꼭 확인해 주세요.")
    listed = ", ".join(names)
    labels = product.resolved_allergies()
    if labels:
        from app.services.response_helpers import find_matching_aliases
        if any(find_matching_aliases(product, name) for name in names):
            return None
        # Lead with what was found; name the label that kept the verdict open.
        unclear = [label for label in labels if "불명" in label]
        known = [label for label in labels if label not in unclear] or labels
        text = f"등록된 알레르기 표시({', '.join(known)})에는 {listed}{subject(names[-1])} 없어요. "
        if unclear:
            text += f"다만 '{unclear[0]}'처럼 원료 출처가 표시되지 않은 항목이 있어 최종 확인이 필요해요. "
        else:
            text += "다만 등록된 정보가 실제 포장과 다를 수 있어요. "
        return text + "드시기 전에 포장지의 알레르기 표시를 한 번 확인해 주세요."
    if product.rawmtrl_nm or product.ingredients:
        from app.services.response_helpers import find_matching_aliases
        if any(find_matching_aliases(product, name) for name in names):
            return None
        # Lead with what was checked; the open part is only the missing allergy label.
        return (f"원재료 목록에서 {listed}{object_particle(names[-1])} 찾지 못했어요. "
                f"다만 '{names[0]} 함유' 같은 알레르기 표시가 등록되어 있지 않아, "
                f"드시기 전에 포장지의 알레르기 표시에서 {listed}{subject(names[-1])} 있는지 한 번 확인해 주세요.")
    # Say what was checked and why it is not a 'no': the label is simply absent.
    return (f"이 상품은 원재료와 알레르기 정보가 등록되어 있지 않아 {listed}{subject(names[-1])} 들어 있는지 알 수 없어요. "
            f"드시기 전에 포장지의 원재료명과 알레르기 표시에서 {listed}{subject(names[-1])} 있는지 확인해 주세요.")
