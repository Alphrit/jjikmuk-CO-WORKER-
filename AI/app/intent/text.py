import re
from typing import List, Optional

from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES, INGREDIENT_KEYWORDS, NUTRITION_KEYWORDS
from app.core.models import ChatHistoryItem, Product, Profile


def contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def normalize_message(message: str) -> str:
    text = message.strip().lower()
    replacements = {
        "들어있": "들어 있",
        "먹을수": "먹을 수",
        "먹을수있": "먹을 수 있",
        "괜찬": "괜찮",
        "드셔도되": "드셔도 되",
        "먹어도되": "먹어도 되",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    text = re.sub(r"먹어두(?=\s*(?:돼|되|괜찮))", "먹어도", text)
    return " ".join(text.split())


def detect_nutrition_keywords(message: str) -> list[str]:
    """Find every requested nutrient, consuming longer aliases first."""
    matches = []
    remaining = message.lower()
    aliases = sorted(((alias, nutrient) for nutrient, names in NUTRITION_KEYWORDS.items()
                      for alias in names), key=lambda item: len(item[0]), reverse=True)
    for alias, nutrient in aliases:
        pattern = re.escape(alias)
        if len(alias) <= 1:
            pattern = rf"(?<![가-힣A-Za-z]){pattern}(?=은|는|이|가|도|\s|[?!.]|$|함량|수치|얼마)"
        if re.search(pattern, remaining):
            matches.append(nutrient)
            remaining = re.sub(pattern, " ", remaining)
    return list(dict.fromkeys(matches))


def detect_ingredient_keyword(message: str) -> Optional[str]:
    for ingredient in INGREDIENT_KEYWORDS:
        if len(ingredient) == 1:
            aliases = ALLERGEN_CANONICAL_ALIASES.get("대두" if ingredient == "콩" else ingredient, ())
            if any(len(alias) > 1 and ingredient in alias and alias in message for alias in aliases):
                return ingredient
            # A single Korean syllable also occurs in ordinary conversation
            # (쉽게, 그게, 비밀). Accept food compounds and attached particles,
            # but never a syllable inside an unrelated word.
            ending = (
                r"(?=$|[^가-힣a-zA-Z]|알레르기|알러지|함유|포함|들어|없|있|"
                r"가루|분말|기름|단백질|소스|살|국수|국|죽|밥|빵|차|즙|"
                r"(?:은|는|이|가|을|를|도|와|과|만|에|로)(?=$|[^가-힣a-zA-Z]|알레르기|알러지|함유|포함|들어|없|있))"
            )
            if re.search(rf"(?<![가-힣a-zA-Z]){re.escape(ingredient)}{ending}", message):
                return ingredient
        elif ingredient in message:
            return ingredient
    return None


def detect_nutrition_keyword(message: str) -> Optional[str]:
    for nutrient, aliases in NUTRITION_KEYWORDS.items():
        for alias in aliases:
            if len(alias) <= 1:
                pattern = rf"{re.escape(alias)}(?:은|는|이|가|\s|[?!.]|$|함량|수치|어때|높|많|적|얼마|얼마나|몇)"
                if re.search(pattern, message):
                    return nutrient
                continue

            if alias in message:
                return nutrient
    return None


def is_missing_product(product: Optional[Product]) -> bool:
    if product is None:
        return False
    return product.product_found is False


def has_product_context(product: Optional[Product]) -> bool:
    return product is not None and product.product_found is True


def profile_identifiers(profile: Optional[Profile]) -> List[str]:
    if not profile:
        return []

    identifiers = [profile.profile_name, profile.nickname]
    return [
        identifier.strip().lower()
        for identifier in identifiers
        if isinstance(identifier, str) and identifier.strip()
    ]


def recent_user_message(chat_history: List[ChatHistoryItem]) -> Optional[str]:
    for item in reversed(chat_history):
        if item.role == "user":
            content = item.content.strip()
            if content:
                return content
    return None


def recent_assistant_message(chat_history: List[ChatHistoryItem]) -> Optional[str]:
    for item in reversed(chat_history):
        if item.role == "assistant":
            content = item.content.strip()
            if content:
                return content
    return None


def has_explicit_followup_cue(message: str) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if text in {"그럼", "그럼?", "그러면", "그러면?", "이건", "이건?", "그건", "그건?", "이거", "이거?", "괜찮아", "괜찮아?", "괜찮을까", "괜찮을까?"}:
        return True

    return len(text) <= 8 and any(token in text for token in ["그럼", "그러면", "이건", "그건", "이거"])


def is_context_dependent_followup(message: str, chat_history: List[ChatHistoryItem]) -> bool:
    if not chat_history:
        return False

    text = normalize_message(message)
    if not text:
        return False

    if detect_ingredient_keyword(text) is not None or detect_nutrition_keyword(text) is not None:
        return False

    if has_explicit_followup_cue(text):
        return True

    return len(text) <= 12 and text.endswith("?")


def recent_history_summary(chat_history: List[ChatHistoryItem], limit: int = 4) -> str:
    if not chat_history:
        return "없음"

    lines = []
    for item in chat_history[-limit:]:
        role = "사용자" if item.role == "user" else "챗봇"
        content = item.content.strip()
        if content:
            lines.append(f"{role}: {content}")

    return "\n".join(lines) or "없음"


def product_context_summary(product: Optional[Product]) -> str:
    if product is None:
        return "제품 정보 없음"

    if product.product_found is False:
        return "제품 정보 없음 또는 DB 미등록"

    parts = ["제품 정보 있음"]

    if product.product_name:
        parts.append(f"제품명: {product.product_name}")

    ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
    if ingredients:
        parts.append(f"원재료명: {ingredients}")

    allergies = product.resolved_allergies()
    if allergies:
        parts.append(f"알레르기 표시: {', '.join(allergies)}")

    if product.nutrition:
        nutrition_text = ", ".join(f"{key}: {value}" for key, value in product.nutrition.items())
        parts.append(f"영양정보: {nutrition_text}")

    return "\n".join(parts)


def profile_context_summary(profile: Optional[Profile]) -> str:
    if not profile:
        return "프로필 정보 없음"

    if profile.target_type == "group":
        header = f"현재 활성 프로필들: {profile.profile_name}"
    else:
        header = f"현재 프로필명: {profile.profile_name}"

    parts = [
        header,
        f"현재 프로필 별칭: {', '.join(profile_identifiers(profile)) or '없음'}",
        f"알레르기: {', '.join(profile.resolved_allergies()) or '없음'}",
        f"식이습관: {', '.join(profile.resolved_special_diets()) or '없음'}",
    ]
    return "\n".join(parts)
