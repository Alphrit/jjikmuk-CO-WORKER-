"""Conservative validation of untrusted recipe text, independent of generation."""

import re
from fractions import Fraction


def valid_amount(value: str) -> bool:
    """Accept a positive quantity/unit or a small, explicit culinary measure."""
    value = value.strip()
    if value in {"약간", "한 줌", "두 줌", "반 줌"}:
        return True
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?(?:/\d+)?)\s*(g|kg|mg|ml|mL|L|l|개|장|컵|큰술|작은술|공기|인분|줌|쪽|조각)",
        value,
    )
    if not match:
        return False
    try:
        # Operational bounds for a single-serving draft, not nutritional advice.
        limits = {"kg": 2, "L": 2, "l": 2, "g": 2000, "mg": 2000000,
                  "ml": 2000, "mL": 2000, "컵": 8, "큰술": 8, "작은술": 24,
                  "인분": 1, "공기": 2}
        return 0 < Fraction(match[1]) <= limits.get(match[2], 30)
    except (ValueError, ZeroDivisionError):
        return False


def unsupported_claim(text: str) -> bool:
    # Generated recipes have no certified nutrition calculation or independent
    # safety evidence. Claims belong to rule-owned label evidence, never a draft.
    return bool(re.search(
        r"안전|무해|인증|보장|안심|걱정\s*없|알레르기\s*(?:없|제로)|\bsafe\b|allergen.free|certif|"
        r"\d\s*(?:kcal|칼로리|킬로칼로리)|"
        r"(?:나트륨|당류|당분|열량|칼로리|지방|단백질)[^.!?\n]{0,30}\d\s*(?:밀리그램|그램)|"
        r"(?:나트륨|당류|당분|열량|칼로리|지방|단백질)[^.!?\n]{0,18}\d\s*(?:mg|g|kcal)|"
        r"(?:sodium|sugar|fat|protein|calorie)[^.!?\n]{0,18}\d\s*(?:mg|g|kcal)|"
        r"(?:저염|저당|저지방|고단백|글루텐프리)[^.!?\n]{0,12}(?:충족|확정|확인됨)",
        text, re.IGNORECASE,
    ))


# A closed cooking vocabulary supplements the diagnostic blacklist below.
# Unknown prose is rejected rather than assuming an unknown noun is harmless.
_ACTIONS = {
    "mix": r"섞|버무|혼합|비비|비벼",
    "boil": r"끓|삶|삶아",
    "grill": r"굽|구워|구운",
    "fry": r"볶",
    "steam": r"찜|찌|쪄",
    "blend": r"갈아|갈고|갈아요|갈기|믹서",
}


def cooking_operations(steps: list[str]) -> list[str]:
    """Canonical operations; presentation/cutting alone do not make a new dish."""
    text = " ".join(steps)
    return sorted(key for key, pattern in _ACTIONS.items() if re.search(pattern, text))


def grounded_draft(draft, variants: list[dict]) -> bool:
    """Allow only reviewed ingredient quantities and bounded cooking language.

    The LLM may compose a subset of reviewed ingredients for one serving. It
    cannot invent quantities, portions, claims, or arbitrary ingredient prose.
    Unknown expressions fall back to the rule recipe; this is deliberately not
    a general natural-language safety classifier.
    """
    if draft.servings.strip() != "1인분":
        return False
    quantities: dict[str, set[str]] = {}
    for variant in variants:
        for item in variant["ingredients"]:
            quantities.setdefault(item["name"], set()).add(item["amount"].replace(" ", ""))
    names = [item["name"] for item in draft.ingredients]
    if len(names) != len(set(names)) or any(
        item["amount"].replace(" ", "") not in quantities.get(item["name"], set())
        for item in draft.ingredients
    ):
        return False
    aliases = set(names)
    for name in names:
        aliases.add(re.sub(r"^(?:삶은|익힌|구운|데친)\s*", "", name))
    title_aliases = aliases | {word for name in names for word in name.split() if len(word) > 1}

    def mask(text, terms):
        for term in sorted(terms, key=len, reverse=True):
            text = text.replace(term, " 재료 ")
        return text

    title = mask(draft.title, title_aliases)
    if any(token not in {
        "재료", "채소", "과일", "볼", "컵", "샐러드", "수프", "쌈", "덮밥", "접시",
        "스무디", "귀리죽", "토핑", "바삭", "담백한", "따뜻한", "구운", "레시피",
    } for token in re.findall(r"[^\s·,]+", title)):
        return False
    token = re.compile(
        r"(?:재료|모든|제품|이|채소|과일|물기|팬|접시|컵|믹서|위|불|약불|중불|크기|순서)"
        r"(?:을|를|은|는|과|와|에|로|으로|대로|의|가|이|만|도)?|"
        r"(?:을|를|은|는|과|와|에|로|으로|가|이)|"
        r"(?:잘게|작게|가볍게|골고루|함께|조금씩|노릇하게|마른|먼저|다음|마지막에)|"
        r"(?:썰|섞|버무리|버무려|혼합하|비비|비벼|담|올리|올려|넣|뿌리|뿌려|곁들이|곁들여|"
        r"씻|헹구|헹궈|끓이|끓여|삶|삶아|굽|구워|볶|찌|쪄|익히|익혀|부수|부숴|"
        r"자르|잘라|갈|갈아|붓|부어|불리|불려|준비하|빼|둘러|마무리하)"
        r"(?:고|어|어요|아|아요|요|해요|세요|주세요|습니다|어요|아주세요|어주세요)?"
    )
    for text in [*draft.steps, *draft.substitutions]:
        masked = mask(text, aliases)
        words = re.findall(r"[^\s.,·!?]+", masked)
        if not words or any(token.fullmatch(word) is None for word in words):
            return False
    return True


def unlisted_additions(texts: list[str], ingredient_names: list[str]) -> list[str]:
    """Reject food additions absent from the declared ingredient list.

    Explicit object phrases and culinary amounts are checked as well as common
    condiments. Unknown addition phrases fail closed; rejected prose is replaced
    by a reviewed rule recipe, not silently stripped of the offending ingredient.
    """
    aliases = set(ingredient_names)
    for name in ingredient_names:
        aliases.add(re.sub(r"^(?:삶은|익힌|구운|데친)\s*", "", name))
    generic = {"재료", "모든 재료", "제품", "이 제품", "채소", "과일", "물기", "불", "접시", "컵", "팬", "함께", "잘", "골고루", "가볍게", "조금씩", "썰어", "둘러", "부숴", "볶아", "잘라", "끓여"}
    findings = []
    for text in texts:
        for condiment in ("소금", "설탕", "시럽", "꿀", "버터", "식용유", "참기름", "카레가루", "카레분", "조미료", "고추장", "된장", "레몬", "허브"):
            if condiment in text and not any(condiment in name for name in aliases):
                # Omission instructions do not add a new ingredient.
                if not re.search(re.escape(condiment) + r"[^.!?]{0,30}(?:넣지|추가하지|빼|생략|제외)", text):
                    findings.append(condiment)
        # Mask exact declared names before extracting newly introduced objects.
        masked = text
        for name in sorted(aliases, key=len, reverse=True):
            masked = masked.replace(name, "재료")
        for match in re.finditer(
            r"([가-힣A-Za-z]+)(?:\s+\d+(?:/\d+)?\s*(?:g|ml|큰술|작은술|개|컵))?(?:을|를)\s*"
            r"(?:조금씩\s*|잘게\s*|가볍게\s*)?(?:넣|추가|뿌|곁들|섞|올|대체)",
            masked,
        ):
            if match[1] not in generic and "재료" not in match[1]:
                findings.append(match[1])
        # The object particle is commonly omitted: "가루 1큰술 추가".
        for match in re.finditer(
            r"([가-힣A-Za-z]+)\s*\d+(?:/\d+)?\s*(?:g|ml|큰술|작은술|개|컵)(?:을|를)?\s*(?:넣|추가|뿌|곁들|섞)",
            masked,
        ):
            if match[1] not in generic:
                findings.append(match[1])
        for match in re.finditer(r"([가-힣A-Za-z]+)(?:과|와|하고)\s*(?:재료|제품|채소)", masked):
            if match[1] not in generic:
                findings.append(match[1])
        for match in re.finditer(r"([가-힣A-Za-z]+)\s+(?:추가(?!하지)|뿌려|곁들여|넣어|섞어)", masked):
            noun = re.sub(r"(?:을|를|에|과|와)$", "", match[1])
            if noun not in generic:
                findings.append(noun)
    return list(dict.fromkeys(findings))
