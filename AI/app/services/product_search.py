import re
from difflib import SequenceMatcher
from math import ceil
from typing import List, Optional

from app.core.models import Product


PRODUCT_CONFIRMATION_THRESHOLD = 0.9
SIZE_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?\s?(?:g|kg|ml|l)")
SHORT_FOOD_SEARCH_TOKENS = {"칩"}
GENERIC_PRODUCT_TOKENS = {
    "농심",
    "오리온",
    "매일",
    "서울우유",
    "남양",
    "동원",
    "롯데",
    "해태",
    "빙그레",
    "풀무원",
    "청정원",
    "오뚜기",
    "삼양",
    "팔도",
    "90g",
    "50g",
    "60g",
    "70g",
    "200ml",
    "1l",
}

CONVERSATIONAL_SEARCH_TOKENS = {
    "같이", "함께", "다시",
    "혹시",
    "제가",
    "저는",
    "나는",
    "내가",
    "지금",
    "오늘",
    "좀",
    "먹으려고",
    "먹으려는데",
    "먹어보려고",
    "마시려고",
    "마시려는데",
    "섭취하려고",
    "하는데",
    "하려는데",
    "궁금해",
    "궁금한데",
}

PRODUCT_REFERENCE_ONLY_TOKENS = {
    "이거",
    "이건",
    "그거",
    "그건",
    "이제품",
    "그제품",
    "현재제품",
    "방금제품",
    "이상품", "그상품", "현재상품", "해당상품", "해당제품",
}

NON_PRODUCT_QUERY_TOKENS = {
    "대신",
    "비슷한",
    "대체",
    "대체할",
    "다른",
    "더",
    "나은",
    "추천",
}

PRODUCT_FORM_TOKEN_GROUPS = (
    {"스낵", "바", "칩", "과자"},
)

_LEADING_CONVERSATION_PATTERN = re.compile(
    r"^(?:(?:혹시|저기|음|제가|저는|나는|내가|지금|오늘)\s*[,.:;!?~…-]*\s*)+",
    re.IGNORECASE,
)

_LEADING_PERSONAL_ALLERGY_PATTERN = re.compile(
    r"^.{1,80}?(?:알레르기|알러지)"
    # '땅콩 알레르기인데/라서' states the same condition as '알레르기가 있는데'.
    r"(?:(?:가|는|이)?\s*(?:있는데|있어서|있고|있으니|있다면|있어)|(?:인데|이라서|라서|이니까|니까|이어서))"
    r"\s*[,.:;!?~…-]*\s*",
    re.IGNORECASE,
)

# These patterns identify where a product mention ends and the user's request begins.
# A boundary must include a conversational predicate or a product-attribute clause so
# product names containing words such as "단백질" or "우유" are preserved.
def _asked_ingredient_boundary():
    """'새우깡에 대두 들어있어?': a named ingredient + a containment verb ends the product name.

    The verb must follow the ingredient directly, so names such as '땅콩버터'
    stay intact.
    """
    from app.data.dictionaries import INGREDIENT_KEYWORDS
    names = "|".join(re.escape(name) for name in sorted(set(INGREDIENT_KEYWORDS), key=len, reverse=True))
    return re.compile(
        rf"(?:에는|에도|에|엔)?\s+(?:{names})(?:이|가|은|는|도|을|를)?\s*"
        r"(?:들어|포함|함유|있|없|안\s*들어|성분)",
        re.IGNORECASE,
    )


_PRODUCT_QUERY_BOUNDARY_PATTERNS = [
    _asked_ingredient_boundary(),
    # '오레오 성분', '진라면 순한맛 나트륨', '새우깡 칼로리야?': a label field
    # as the last word ends the product name.
    re.compile(
        r"\s+(?:알레르기|알러지|원재료명?|성분표?|영양성분표?|영양정보|영양|나트륨|당류|단백질|포화지방|트랜스지방|식이섬유|지방|"
        r"탄수화물|칼로리|열량|콜레스테롤|글루텐)(?:\s*성분)?(?:은|는|이|가|야|이야|요|정보|표시)?\s*[?？!.~]*$",
        re.IGNORECASE,
    ),
    # '새우깡 칼로리랑 당류': a listed label field ends the name.
    re.compile(r"\s+(?:나트륨|당류|단백질|포화지방|트랜스지방|식이섬유|지방|탄수화물|칼로리|열량|콜레스테롤)\s*(?:이랑|랑|하고|와|과|,)"),
    # '새우깡 건강에 괜찮아?', '커피 하루에 몇잔까지': health or amount wording.
    re.compile(r"\s+(?:건강|몸)(?:에|엔|상|에는)?\s*(?:괜찮|좋|나쁘|안\s*좋|해롭)"),
    re.compile(r"\s+하루(?:에)?\s*(?:몇|얼마)"),
    # '새우깡 맛있어?', '새우깡 맛 어때': taste is not part of the name.
    re.compile(r"\s+맛(?:있|없|이\s*어때|은\s*어때|\s*어때)"),
    # '새우깡 몇 칼로리야'
    re.compile(r"\s+몇\s*(?:칼로리|kcal|그램|g|mg|밀리그램)", re.IGNORECASE),
    # '새우깡 100g당 칼로리', '우유 100ml 기준 당류', '새우깡 1회 제공량': a basis, not a size
    # ('포키스탠다드 42g' keeps its size because no 당/기준 follows).
    re.compile(r"\s+(?:\d+(?:\.\d+)?\s*(?:g|ml|그램|밀리리터)\s*(?:당|기준)|1\s*회\s*(?:제공량|분))", re.IGNORECASE),
    # '허니버터칩 한 봉지 칼로리', '코카콜라 당 얼마나 있어', '오레오 오즈 엄마랑 같이 먹어도 돼'
    re.compile(r"\s+(?:한|두|세|네|반|\d+)\s*(?:봉지|봉|개|팩|병|캔|통|줄|조각|컵|잔)(?=\s|$|[은는이가을를도의])"),
    re.compile(r"\s+당(?=\s*(?:얼마|몇|함량|수치|은\s|는\s|이\s|가\s|[?？]|$))"),
    re.compile(r"\s+(?:엄마|아빠|어머니|아버지|아이|애기|아기|동생|언니|오빠|누나|형|가족|우리)"
               r"(?:랑|하고|와|과|도|가|는|이|이랑)?\s*(?:같이|함께)?\s*(?=먹|마시|마셔|괜찮|섭취)"),
    re.compile(
        r"\s+(?:제품(?:을|으로)?\s*)?(?:이용|활용)(?:한|해서|해)\s*"
        r"(?:레시피|요리|조리법)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:제품\s*)?(?:레시피|조리법|요리법)\s*"
        r"(?:추천|알려|만들|있)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:이거|이건|그거|그건)?\s*"
        r"(?:먹으려고|먹으려는데|먹어\s*보려고|먹어도|먹어두|머거도|먹을까|먹을\s*수|"
        r"마시려고|마시려는데|마셔도|마셔두|섭취하려고|섭취하려는데|섭취해도)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:의|에서|에는)\s*(?:다른\s*)?"
        r"(?:알레르기|알러지|원재료|성분|영양성분|영양|나트륨|당류|단백질|"
        r"포화지방|트랜스지방|식이섬유|지방|탄수화물|칼로리|열량|콜레스테롤)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:알레르기|알러지|원재료|성분표?|영양성분표?|나트륨|당류|단백질|"
        r"포화지방|트랜스지방|식이섬유|지방|탄수화물|칼로리|열량|콜레스테롤|글루텐|유당|락토스)\s*"
        r"(?:(?:성분|정보|표시)\s*)?"
        r"(?:은|는|이|가|도|만|을|를|함량|수치|뭐|몇|얼마|어떤|어떠|어때|"
        r"들어|포함|있|없|높|낮|많|적|알려|보여|설명|확인|궁금|알고\s*싶)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:어때|괜찮아|괜찮을까|안전해|맞아|맞아요|맞나요|"
        r"찾아\s*줘|검색해\s*줘|추천해\s*줘|설명해\s*줘|알려\s*줘|확인해\s*줘|비교해\s*줘)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:은|는)?(?:이거|이건|그거|그건)?"
        r"(?:먹으려고|먹으려는데|먹어보려고|먹어도|먹어두|머거도|먹을까|먹을수|"
        r"마시려고|마시려는데|마셔도|마셔두|섭취하려고|섭취하려는데|섭취해도)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:의)?(?:알레르기|알러지|원재료|성분|영양성분|나트륨|당류|"
        r"칼로리|열량|콜레스테롤)"
        r"(?:(?:성분|정보|표시))?"
        r"(?:은|는|이|가|도|만|을|를|함량|수치|뭐|몇|얼마|어떤|어떠|어때|"
        r"들어|포함|있|없|높|낮|많|적|알려|보여|설명|확인)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:은|는)?(?:어때|괜찮아|괜찮을까|안전해|맞아|맞아요|맞나요|"
        r"찾아줘|검색해줘|추천해줘|설명해줘|알려줘|확인해줘|비교해줘)",
        re.IGNORECASE,
    ),
]


def strip_korean_particle(token: str) -> str:
    for suffix in (
        "이랑",
        "하고",
        "까지",
        "부터",
        "으로",
        "에서",
        "에게",
        "에는",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "도",
        "만",
        "랑",
    ):
        if len(token) > len(suffix) + 1 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def normalize_search_text(text: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in text).strip()


def compact_search_text(text: str) -> str:
    return normalize_search_text(text).replace(" ", "")


def tokenize_search_text(text: str) -> List[str]:
    stopwords = {
        "이",
        "이거",
        "이건",
        "제품",
        "어때",
        "괜찮아",
        "괜찮을까",
        "먹어",
        "먹을",
        "먹는",
        "먹어도",
        "섭취",
        "섭취해도",
        "돼",
        "되나요",
        "되나",
        "설명해줘",
        "추천해줘",
        "알려줘",
        "확인해줘",
        "먹어도돼",
        "들어",
        "있어",
        "없어",
        "포함",
        "있는",
        "사람",
        "사람은",
        "알레르기",
        "알러지",
        "있으면",
        "맞아",
        "맞아요",
        "맞습니다",
        "선택",
        "선택할게",
        "이걸로",
        *CONVERSATIONAL_SEARCH_TOKENS,
    }
    tokens = []
    for token in normalize_search_text(text).split():
        token = strip_korean_particle(token)
        if (len(token) >= 2 or token in SHORT_FOOD_SEARCH_TOKENS) and token not in stopwords:
            tokens.append(token)
    return tokens


def _strip_product_query_suffix(text: str) -> str:
    cleaned = text.strip(" \t\r\n\"'`.,!?~")
    cleaned = re.sub(r"\s+(?:제품|상품)$", "", cleaned).strip()

    tokens = cleaned.split()
    if not tokens:
        return ""

    last = tokens[-1]
    for suffix in ("이라는", "라는"):
        if len(last) > len(suffix) + 1 and last.endswith(suffix):
            last = last[: -len(suffix)]
            break
    else:
        last = strip_korean_particle(last)
    tokens[-1] = last
    return " ".join(token for token in tokens if token).strip()


def extract_product_search_query(message: str) -> str:
    """Extract only the product mention from a natural-language product question."""
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return ""

    cleaned = re.sub(r"^[^\w가-힣]+", "", cleaned).strip()
    cleaned = _LEADING_CONVERSATION_PATTERN.sub("", cleaned).strip()
    cleaned = re.sub(r"^(?:(?:아까|방금|이번에는|그럼|그러면)\s+)+", "", cleaned)
    cleaned = re.sub(r"^(?:이|그|현재|해당)\s*(?:제품|상품)(?:은|는|을|를)?\s*(?:말고|대신)\s*", "", cleaned)
    # Parse replacement targets independently of catalog lookup success.
    cleaned = re.sub(r"^.{1,80}?\s+(?:말고|대신)\s+", "", cleaned)
    cleaned = re.sub(r"^(?:다른|새|새로운)\s*(?:제품|상품)(?:인)?\s+", "", cleaned)
    declaration = re.fullmatch(r"(.+?)(?:으로|로)\s*(?:바꿨어|바꿨[가-힣]*|변경했[가-힣]*|선택했[가-힣]*|골랐[가-힣]*)[.!?\s]*", cleaned)
    if declaration:
        cleaned = declaration[1] + " 찾아줘"
    # In "새우 알레르기가 있는데 새우깡 먹어도 돼?", the first 새우 is
    # a user constraint and the second term is the product. Search only the
    # product clause so the catalog does not offer every shrimp-containing item.
    cleaned = _LEADING_PERSONAL_ALLERGY_PATTERN.sub("", cleaned).strip()
    cleaned = re.sub(r"^(?:다이어트|식단\s*관리|운동|저염식|저당식|비건|채식|감량)\s*(?:중|하는\s*중)(?:인데|이라|이라서|이어서|이니까|이야)?\s+", "", cleaned)
    # The named person in '상품명은 아빠가 먹어도 돼?' is a subject,
    # not part of the product's catalog identity.
    cleaned = re.sub(r"^(.+?)(?:은|는)\s+\S+(?:이|가|도)\s+(?=먹어도|먹을\s*수|마셔도|드셔도|드실\s*수)", r"\1 ", cleaned)
    cleaned = re.sub(r"^[^\w가-힣]+", "", cleaned).strip()
    boundary_indexes = []
    for pattern in _PRODUCT_QUERY_BOUNDARY_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            boundary_indexes.append(match.start())

    if boundary_indexes:
        cleaned = cleaned[: min(boundary_indexes)]

    query = _strip_product_query_suffix(cleaned)
    if compact_search_text(query) in PRODUCT_REFERENCE_ONLY_TOKENS:
        return ""

    compact_query = compact_search_text(query)
    canonical_generic_tokens = {
        compact_search_text(token): token
        for token in GENERIC_PRODUCT_TOKENS
    }
    if compact_query in canonical_generic_tokens:
        query = canonical_generic_tokens[compact_query]

    meaningful_tokens = [
        token
        for token in tokenize_search_text(query)
        if token not in CONVERSATIONAL_SEARCH_TOKENS
    ]
    if not meaningful_tokens or all(token in NON_PRODUCT_QUERY_TOKENS for token in meaningful_tokens):
        return ""

    return query


def recipe_source_query(message: str) -> str:
    """Explicit source syntax, not just a nonempty search string.

    Diet-only and generic recipes remain productless. A named source must be
    resolved even if the catalog returns zero candidates.
    """
    from app.data.dictionaries import SPECIAL_DIET_KEYWORDS
    from app.services.diet_goal_context import product_text_without_goal_edits
    generic = {
        "기본식재료", "일반식재료", "식재료", "채소", "야채", "일반", "간단한",
        "제품없이", "제품없는", "다른", "새로운", "다양한", "또다른", "더", "또", "하나", "추천",
        # An unsupported diet is a condition, not a catalog source; the recipe
        # builder answers that it is unsupported instead of a product lookup.
        "비건", "vegan",
        *(compact_search_text(term) for name, aliases in SPECIAL_DIET_KEYWORDS.items() for term in (name, *aliases)),
    }
    for sentence in re.split(r"[.!?;\n]", message):
        sentence = product_text_without_goal_edits(sentence)
        sentence = re.sub(r"(?<![\w])(?:제품\s*(?:없이|없는)|기본\s*식재료|일반\s*식재료)", "일반", sentence)
        sentence = re.sub(r"(?<![가-힣])한\s*끼(?=\s|$)", "일반", sentence)
        # Remove only goal operations following an actual diet term. Keep
        # named food tokens, so a failed named-source lookup stays unresolved.
        diet_terms = "|".join(re.escape(term) for name, aliases in SPECIAL_DIET_KEYWORDS.items() for term in (name, *aliases))
        sentence = re.sub(r"(" + diet_terms + r")(?:도)?\s*(?:(?:같이|함께)\s*)?(?:적용한|적용해서|추가한)", r"\1", sentence)
        sentence = _LEADING_CONVERSATION_PATTERN.sub("", sentence.strip())
        sentence = _LEADING_PERSONAL_ALLERGY_PATTERN.sub("", sentence).strip()
        sentence = re.sub(
            r"^(?:(?:유제품|우유|달걀|계란)(?:은|는|을|를)?\s*(?:없이|없는|빼고|제외하고)\s*)+",
            "", sentence,
        )
        match = re.match(
            r"(.+?)(?:으로|로|(?:을|를)\s*(?:이용|활용)(?:한|해서|해))\s*"
            r"(?:(?:저염|저당|고단백|저칼로리|저지방|채식|글루텐프리)(?:식)?\s*)*"
            r"(?:(?:만들|만들어|활용|이용)[가-힣\s]*?)?(?:레시피|요리|조리법|요리법)",
            sentence,
        )
        if match:
            if compact_search_text(match[1]) not in generic:
                return match[1].strip()
            continue
        # A catalog name need not have an instrumental particle. Keep a named
        # but unresolved source distinct from an intentionally generic recipe.
        # Identify the source before the recipe noun, independently of how
        # the user requests it ('있어?', '가능해?', '추천해줘', ...).
        bare = re.match(
            r"(.+?)\s+(?:레시피|요리|조리법|요리법)(?=$|\s|[를도는가은에?!])", sentence,
        )
        if bare:
            prefix = bare[1].strip()
            tokens = prefix.split()
            if tokens and not all(compact_search_text(token) in generic for token in tokens):
                # Conditions and full sentences are not product identities.
                if not re.search(r"알레르기|알러지|먹|없이|빼|제외|않|말고|만들|추천|알려", prefix):
                    diet_tokens = {compact_search_text(term) for name, aliases in SPECIAL_DIET_KEYWORDS.items() for term in (name, *aliases)}
                    while len(tokens) > 1 and compact_search_text(tokens[-1]) in diet_tokens:
                        tokens.pop()
                    return " ".join(tokens)
    return ""


def recipe_query_without_instrumental_particle(message: str, query: str) -> str:
    """A fallback only after exact lookup, preserving names ending in '로'."""
    source = recipe_source_query(message)
    if source and source != query and compact_search_text(source) not in PRODUCT_REFERENCE_ONLY_TOKENS:
        return source
    return ""


def _candidate_identity_text(product: Product) -> str:
    return " ".join(
        value
        for value in [
            product.product_name,
            product.clean_product_name,
            product.manufacturer_name,
            product.company_name,
        ]
        if value
    )


def _matches_product_form_alias(query_token: str, identity_tokens: list[str]) -> bool:
    for token_group in PRODUCT_FORM_TOKEN_GROUPS:
        if query_token not in token_group:
            continue
        aliases = token_group - {query_token}
        return any(
            identity_token == alias or identity_token.endswith(alias)
            for identity_token in identity_tokens
            for alias in aliases
        )
    return False


def _matches_identity_token_boundary(query_token: str, identity_tokens: list[str]) -> bool:
    if any(query_token in identity_token for identity_token in identity_tokens):
        return True

    for start in range(len(identity_tokens)):
        combined = ""
        for identity_token in identity_tokens[start:]:
            combined += identity_token
            if combined == query_token:
                return True
            if len(combined) >= len(query_token):
                break
    return False


def candidate_matches_product_query(query: str, product: Product) -> bool:
    """Require a candidate to match its identity fields, not only raw materials."""
    identity_text = _candidate_identity_text(product)
    if not query or not identity_text:
        return False

    compact_query = compact_search_text(query)
    query_tokens = [
        compact_search_text(token)
        for token in tokenize_search_text(query)
        if token not in CONVERSATIONAL_SEARCH_TOKENS
        and compact_search_text(token) not in PRODUCT_REFERENCE_ONLY_TOKENS
    ]
    identity_tokens = [
        compact_search_text(token)
        for token in tokenize_search_text(identity_text)
    ]
    if not query_tokens or not identity_tokens:
        return False
    if compact_query and _matches_identity_token_boundary(compact_query, identity_tokens):
        return True
    if len(query_tokens) == 1:
        # A joined brand/type query (e.g. '브랜드스낵') may omit the flavour.
        # Require both parts in the product name, never just a raw ingredient.
        name = compact_search_text(product.product_name or "")
        for form in ("스낵", "과자"):
            stem = compact_query[:-len(form)] if compact_query.endswith(form) else ""
            if len(stem) >= 2 and name.startswith(stem) and name.endswith(form):
                return True

    matched = 0
    for query_token in query_tokens:
        if _matches_identity_token_boundary(query_token, identity_tokens):
            matched += 1
            continue
        if _matches_product_form_alias(query_token, identity_tokens):
            matched += 1
            continue
        if len(query_token) >= 3 and any(
            len(query_token) == len(identity_token)
            and SequenceMatcher(None, query_token, identity_token).ratio() >= 0.84
            for identity_token in identity_tokens
        ):
            matched += 1

    required_matches = max(1, ceil(len(query_tokens) * 0.67))
    return matched >= required_matches


def filter_product_candidates_for_query(query: str, products: list[Product]) -> list[Product]:
    return [
        product
        for product in products
        if candidate_matches_product_query(query, product)
    ]


def same_product(left: Optional[Product], right: Optional[Product]) -> bool:
    if left is None or right is None:
        return False

    if (
        left.product_id is not None
        and right.product_id is not None
        and left.product_id == right.product_id
    ):
        return True
    if left.barcode and right.barcode and left.barcode == right.barcode:
        return True
    if left.report_number and right.report_number and left.report_number == right.report_number:
        return True
    if left.food_code and right.food_code and left.food_code == right.food_code:
        return True
    if left.product_name and right.product_name and left.product_name == right.product_name:
        return True
    return False


def product_match_score(product: Product, candidates: list[Product]) -> float:
    for candidate in candidates:
        if same_product(product, candidate):
            return candidate.match_confidence or 0
    return 0


def product_display_key(product: Product) -> str:
    name = product.clean_product_name or product.product_name or ""
    tokens = [
        token
        for token in tokenize_search_text(name)
        if token not in GENERIC_PRODUCT_TOKENS
        and not token.isdigit()
        and not SIZE_TOKEN_PATTERN.fullmatch(token)
    ]
    return compact_search_text(" ".join(tokens) or name)


def dedupe_products_by_display_name(products: list[Product]) -> list[Product]:
    deduped: list[Product] = []
    for product in products:
        duplicate_index = next(
            (
                index
                for index, previous in enumerate(deduped)
                if _same_authoritative_candidate(previous, product)
            ),
            None,
        )
        if duplicate_index is None:
            deduped.append(product)
            continue
        previous = deduped[duplicate_index]
        if (product.match_confidence or 0) > (previous.match_confidence or 0):
            deduped[duplicate_index] = product
    return deduped


def _same_authoritative_candidate(left: Product, right: Product) -> bool:
    shared_identifiers = []
    for field_name in ["product_id", "barcode", "report_number", "food_code"]:
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value is not None and right_value is not None:
            shared_identifiers.append(left_value == right_value)
    return bool(shared_identifiers) and all(shared_identifiers)


def same_display_product_group(products: list[Product]) -> bool:
    keys = [product_display_key(product) for product in products if product_display_key(product)]
    if len(keys) < 2:
        return False
    return len(set(keys)) == 1


def product_size_tokens(product: Product) -> list[str]:
    if not product.product_name:
        return []
    return [compact_search_text(token) for token in SIZE_TOKEN_PATTERN.findall(product.product_name)]


def product_name_tokens(product: Product) -> list[str]:
    if not product.product_name:
        return []

    tokens = []
    for token in re.split(r"[\s/()]+", normalize_search_text(product.product_name)):
        token = strip_korean_particle(token.strip())
        if len(token) < 2:
            continue
        if SIZE_TOKEN_PATTERN.fullmatch(token):
            continue
        tokens.append(token)
    return tokens


def explicitly_mentions_product(message: str, product: Product) -> bool:
    if not product.product_name:
        return False

    message_compact = compact_search_text(message)
    name_compact = compact_search_text(product.product_name)
    if name_compact and name_compact in message_compact:
        return True

    matched_name_tokens = [
        token
        for token in product_name_tokens(product)
        if compact_search_text(token) in message_compact
    ]
    distinctive_matches = [
        token
        for token in matched_name_tokens
        if compact_search_text(token) not in GENERIC_PRODUCT_TOKENS
    ]
    if len(distinctive_matches) >= 2:
        return True

    size_matched = any(size_token in message_compact for size_token in product_size_tokens(product))
    if not size_matched:
        return False

    return len(matched_name_tokens) >= 1


def mentions_distinct_product_token(message: str, product: Product) -> bool:
    message_compact = compact_search_text(message)
    for token in product_name_tokens(product):
        token_compact = compact_search_text(token)
        if token_compact in GENERIC_PRODUCT_TOKENS:
            continue
        if len(token_compact) < 2:
            continue
        if token_compact in message_compact:
            return True
    return False
