"""product storage / common repository implementation."""

from app.core.models import Product
from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional


RECOMMENDATION_QUERY_OVERSAMPLE_FACTOR = 5


RECOMMENDATION_QUERY_MIN_ROWS = 50


class ProductRepository:
    def readiness_check(self) -> Dict[str, Any]:
        return {"available": False, "reason": "readiness_check_not_implemented"}

    def get_by_product_id(self, product_id: int) -> Optional[Product]:
        raise NotImplementedError

    def get_by_barcode(self, barcode: str) -> Optional[Product]:
        raise NotImplementedError

    def get_by_report_number(self, report_number: str) -> Optional[Product]:
        raise NotImplementedError

    def search_by_text(self, text: str) -> Optional[Product]:
        raise NotImplementedError

    def search_candidates(self, text: str, limit: int = 3) -> List[Product]:
        raise NotImplementedError

    def recommend_alternatives(self, product: Product, limit: int = 3) -> List[Product]:
        raise NotImplementedError


def _normalize_search_text(text: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in text).strip()


def _strip_korean_particle(token: str) -> str:
    for suffix in ["에서는", "에게는", "에는", "으로는", "로는", "이랑", "하고", "까지", "부터", "은", "는", "이", "가", "을", "를", "에", "와", "과", "랑", "도"]:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)]
    return token


SHORT_FOOD_SEARCH_TOKENS = {"칩"}


def _tokenize_search_text(text: str) -> List[str]:
    stopwords = {
        "이",
        "이거",
        "이건",
        "제품",
        "그럼",
        "그러면",
        "그건",
        "그거",
        "그럼이거",
        "그럼이건",
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
        "나트륨",
        "당류",
        "칼로리",
        "열량",
        "지방",
        "탄수화물",
        "영양",
        "비교해줘",
        "비교",
        "맞아",
        "맞아요",
        "맞습니다",
        "선택",
        "선택할게",
        "이걸로",
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
    tokens = []
    for token in _normalize_search_text(text).split():
        stripped = _strip_korean_particle(token)
        candidates = [stripped] if stripped != token else [token]
        for candidate in candidates:
            if (
                (len(candidate) >= 2 or candidate in SHORT_FOOD_SEARCH_TOKENS)
                and candidate not in stopwords
                and candidate not in tokens
            ):
                tokens.append(candidate)
    return tokens


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
    "cj",
    "씨제이",
    "대상",
    "샘표",
    "팔도",
    "삼양",
    "하림",
    "오뚜기",
    "라이트",
    "스탠다드",
    "light",
    "standard",
    "90g",
    "50g",
    "60g",
    "70g",
    "200ml",
    "1l",
}


SEARCH_KEYWORD_COLUMNS = ["search_keywords", "searchKeywords", "검색키워드"]


PRODUCT_ID_COLUMNS = ["product_id", "id"]


BARCODE_COLUMNS = ["barcode", "바코드", "BAR_CD"]


REPORT_NUMBER_COLUMNS = [
    "report_number",
    "report_no",
    "품목제조보고번호",
    "품목보고번호",
    "PRDLST_REPORT_NO",
]


PRODUCT_NAME_COLUMNS = ["product_name", "productName", "식품명", "PRDLST_NM", "food_name"]


CLEAN_PRODUCT_NAME_COLUMNS = ["clean_product_name", "cleanProductName"]


RAW_MATERIAL_COLUMNS = ["rawmtrl_nm", "raw_materials", "rawMaterials", "RAWMTRL_NM", "원재료명"]


ALLERGY_COLUMNS = [
    "allergy",
    "allergy_info",
    "allergy_warning",
    "allergyWarning",
    "알레르기",
    "알레르기경고",
]


NUTRITION_COLUMNS = [
    "kcal",
    "energy",
    "energy_kcal",
    "energyKcal",
    "protein",
    "protein_g",
    "fat",
    "fat_g",
    "carbohydrate",
    "carbs_g",
    "sugars",
    "sugar_g",
    "sodium",
    "sodium_mg",
    "cholesterol",
    "cholesterol_mg",
]


# `food_type` is the backend products column (JJIKMUK-Backend V5); it holds the
# first of the raw food type, sub/middle category, representative food or major category.
PRODUCT_TYPE_COLUMNS = ["prdlst_dcnm", "PRDLST_DCNM", "유형명", "food_type"]


MANUFACTURER_COLUMNS = ["manufacturer", "manufacturer_name", "manufacturerName", "BSSH_NM", "제조사명"]


PRODUCT_LABEL_PROVENANCE_COLUMNS = {
    "label_source_type",
    "label_source_id",
    "label_source_record_id",
    "label_observed_at",
    "upstream_updated_at",
    "upstream_revision",
    "ingested_content_revision_sha256",
    "label_covered_fact_groups",
    "label_lineage_status",
}


def _select_preferred_fulltext_index(
    columns_by_index: Dict[str, List[str]],
    search_columns: List[str],
) -> tuple[Optional[str], List[str]]:
    valid_indexes = [
        (index_name, columns)
        for index_name, columns in columns_by_index.items()
        if columns and all(column in search_columns for column in columns)
    ]
    if not valid_indexes:
        return None, []

    keyword_columns = set(SEARCH_KEYWORD_COLUMNS)
    keyword_indexes = [
        (index_name, columns)
        for index_name, columns in valid_indexes
        if any(column in keyword_columns for column in columns)
    ]
    if keyword_indexes:
        return min(keyword_indexes, key=lambda item: (len(item[1]), item[0]))

    return max(valid_indexes, key=lambda item: (len(item[1]), item[0]))


def _should_merge_like_fallback(fulltext_columns: List[str], fulltext_row_count: int) -> bool:
    if fulltext_row_count == 0:
        return True
    return not any(column in SEARCH_KEYWORD_COLUMNS for column in fulltext_columns)


FOOD_SIMILARITY_TOKENS = {
    "감자칩",
    "감자",
    "과자",
    "김밥",
    "단백질",
    "두부",
    "두유",
    "라면",
    "마요",
    "만두",
    "빵",
    "샐러드",
    "새우",
    "스낵",
    "오트",
    "우유",
    "음료",
    "죽",
    "참치",
    "칩",
    "치즈",
    "초콜릿",
    "쿠키",
}


def _distinctive_product_tokens(product_name: str) -> List[str]:
    return [
        token
        for token in _tokenize_search_text(product_name)
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    ]


def _brand_removed_text(text: str) -> str:
    tokens = _tokenize_search_text(text)
    return " ".join(
        token
        for token in tokens
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    )


def _expanded_product_name_tokens(text: str) -> List[str]:
    tokens = []
    for token in _distinctive_product_tokens(text):
        tokens.append(token)
        tokens.extend(food_token for food_token in FOOD_SIMILARITY_TOKENS if food_token in token)

    seen = set()
    unique_tokens = []
    for token in tokens:
        if token in seen or token in GENERIC_PRODUCT_TOKENS or token.isdigit():
            continue
        seen.add(token)
        unique_tokens.append(token)
    return unique_tokens


def build_product_search_keywords(product: Product) -> str:
    parts = [
        product.clean_product_name,
        product.product_name,
        _brand_removed_text(product.clean_product_name or product.product_name or ""),
        product.manufacturer_name,
        product.company_name,
        product.rawmtrl_nm,
        product.allergy_warning,
        product.prdlst_dcnm,
    ]
    parts.extend(product.ingredients)
    parts.extend(product.allergy)

    tokens: List[str] = []
    for part in parts:
        if not part:
            continue
        tokens.extend(_expanded_product_name_tokens(str(part)))
        tokens.extend(_tokenize_search_text(str(part)))

    seen = set()
    unique_tokens = []
    for token in tokens:
        if token in seen or len(token) < 2:
            continue
        seen.add(token)
        unique_tokens.append(token)
    return " ".join(unique_tokens)


def _product_search_text(product: Product) -> str:
    return " ".join(
        value
        for value in [
            product.clean_product_name,
            product.product_name,
            _brand_removed_text(product.clean_product_name or product.product_name or ""),
            build_product_search_keywords(product),
        ]
        if value
    )


def _explicit_package_sizes(text: str) -> set[tuple[str, float]]:
    scales = {"ml": ("volume", 1), "l": ("volume", 1000),
              "g": ("mass", 1), "kg": ("mass", 1000)}
    # Start only at the first digit of a run; restarting inside a long digit
    # run is quadratic and runs once per scored product.
    return {(scales[unit][0], round(float(value) * scales[unit][1], 6))
            for value, unit in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(kg|ml|g|l)(?![a-z])", text.lower())}


def _product_text_score(query: str, product: Product) -> float:
    score = _product_name_score(query, product)
    tokens = _tokenize_search_text(query)
    identity = _normalize_search_text(" ".join(filter(None, [
        product.product_name, product.clean_product_name,
        product.manufacturer_name, product.company_name,
    ]))).replace(" ", "")
    # Brand names are generic for fuzzy name matching, but an explicitly
    # requested brand must not disappear as a constraint. Ingredient fields
    # are not evidence that the product belongs to that brand.
    brands = GENERIC_PRODUCT_TOKENS - {"라이트", "스탠다드", "light", "standard"}
    brands = {brand for brand in brands if not re.search(r"\d", brand)}
    compact_query = _normalize_search_text(query).replace(" ", "")
    requested_brands = {brand for brand in brands if any(token.startswith(brand) for token in tokens)
                        or compact_query.startswith(brand)}
    if requested_brands and any(brand not in identity for brand in requested_brands):
        return min(score, 0.34)
    requested_sizes = _explicit_package_sizes(query)
    # Only packaging/name fields establish package size. Nutrient quantities
    # and ingredient amounts are not evidence of the requested package.
    product_sizes = _explicit_package_sizes(" ".join(str(value) for value in (
        product.product_name, product.weight, product.total_weight,
    ) if value))
    if requested_sizes and product_sizes and requested_sizes.isdisjoint(product_sizes):
        return min(score, 0.34)
    lexical = [token for token in tokens if not re.fullmatch(r"\d+(?:\.\d+)?(?:g|kg|ml|l)?", token)]
    if len(tokens) > 1 and lexical and not any(token in identity for token in lexical):
        # A shared size (500ml) alone must not retrieve an unrelated beverage.
        return min(score, 0.34)
    # Preserve every explicit identity qualifier, including '제로' and a
    # separated one-character name fragment. Ingredient/size overlap must not
    # turn regular cola or raw shrimp into a match for a different product.
    raw_words = _normalize_search_text(query).split()
    fragments = [word for word in raw_words if len(word) == 1 and word.isalpha()
                 and word not in {"은", "는", "이", "가", "을", "를", "도", "와", "과", "랑", "로", "에", "줘", "돼"}]
    name_identity = _normalize_search_text(" ".join(filter(None, [product.product_name, product.clean_product_name])))
    identity_words = name_identity.split()
    if len(lexical) + len(fragments) > 1:
        for token in [*lexical, *fragments]:
            token_identity = identity if any(token.startswith(brand) or brand.startswith(token) for brand in requested_brands) else name_identity.replace(" ", "")
            if token in token_identity:
                continue
            if token in _normalize_search_text(" ".join(filter(None, [
                product.prdlst_dcnm, product.category_large, product.category_medium,
                product.category_small, product.category_detail,
            ]))):
                # A category request can match the catalog category even when
                # the product's name uses another food-form word (과자/스낵) or
                # none at all ('아몬드브리즈 언스위트' is in category '음료').
                continue
            # Keep bounded spelling variations, but never drop a qualifier.
            if len(token) >= 3 and any(SequenceMatcher(None, token, word).ratio() >= 0.75 for word in identity_words):
                continue
            return min(score, 0.34)
    return score


def _product_name_score(query: str, product: Product) -> float:
    if not product.product_name:
        return 0.0

    normalized_query = _normalize_search_text(query)
    normalized_name = _normalize_search_text(product.product_name)
    normalized_search_text = _normalize_search_text(_product_search_text(product))
    if not normalized_query or not normalized_name:
        return 0.0

    compact_query = normalized_query.replace(" ", "")
    compact_name = normalized_name.replace(" ", "")
    compact_brand_removed_name = _brand_removed_text(product.product_name).replace(" ", "")
    if compact_query and compact_query in {compact_name, compact_brand_removed_name}:
        return 1.0
    if compact_query and compact_query in compact_name:
        extra_length_ratio = max(0.0, (len(compact_name) - len(compact_query)) / max(len(compact_name), 1))
        return max(0.82, 0.96 - min(extra_length_ratio, 0.2))

    if normalized_name in normalized_query:
        exact_query_tokens = set(_expanded_product_name_tokens(normalized_query) or _tokenize_search_text(normalized_query))
        extra_query_tokens = [
            token
            for token in exact_query_tokens
            if token not in GENERIC_PRODUCT_TOKENS
            and not token.isdigit()
            and token not in normalized_name
        ]
        if extra_query_tokens:
            return 0.78
        return 1.0

    query_tokens = set(_expanded_product_name_tokens(normalized_query) or _tokenize_search_text(normalized_query))
    name_tokens = set(_tokenize_search_text(normalized_name))
    search_tokens = set(_tokenize_search_text(normalized_search_text))
    distinctive_tokens = _distinctive_product_tokens(normalized_search_text)
    core_query_tokens = [
        token
        for token in query_tokens
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    ]

    if core_query_tokens:
        name_hits = [token for token in core_query_tokens if token in normalized_name]
        search_hits = [token for token in core_query_tokens if token in normalized_search_text]
        if name_hits:
            name_coverage = len(name_hits) / len(core_query_tokens)
            search_coverage = len(search_hits) / len(core_query_tokens)
            if name_coverage >= 1:
                phrase_bonus = 0.04 if normalized_query in normalized_name else 0.0
                return min(0.99, 0.45 + (0.42 * name_coverage) + (0.08 * search_coverage) + phrase_bonus)
            return min(0.82, 0.28 + (0.32 * name_coverage) + (0.04 * search_coverage))
        if search_hits:
            search_coverage = len(search_hits) / len(core_query_tokens)
            return min(0.58, 0.28 + (0.25 * search_coverage))

    exact_distinctive_matches = [
        token for token in distinctive_tokens if token in normalized_query or token in query_tokens
    ]
    if exact_distinctive_matches:
        extra_query_tokens = [
            token
            for token in query_tokens
            if token not in distinctive_tokens and token not in GENERIC_PRODUCT_TOKENS
        ]
        match_ratio = len(exact_distinctive_matches) / len(distinctive_tokens)
        if match_ratio >= 1:
            if extra_query_tokens:
                return 0.78
            return min(0.99, 0.94 + (0.02 * len(exact_distinctive_matches)))
        return max(0.45, 0.55 + (0.25 * match_ratio))

    fuzzy_token_score = 0.0
    for query_token in query_tokens:
        for name_token in distinctive_tokens:
            fuzzy_token_score = max(
                fuzzy_token_score,
                SequenceMatcher(None, query_token, name_token).ratio(),
            )

    token_score = len(query_tokens & name_tokens) / len(name_tokens) if name_tokens else 0.0
    search_token_score = len(query_tokens & search_tokens) / len(query_tokens) if query_tokens else 0.0
    sequence_score = SequenceMatcher(None, normalized_query, normalized_name).ratio()
    if fuzzy_token_score >= 0.65:
        return max(token_score, search_token_score, sequence_score, 0.45)

    return max(token_score, search_token_score, sequence_score)


def _product_candidate_key(product: Product) -> str:
    name = product.clean_product_name or product.product_name or ""
    brand_removed = _brand_removed_text(name)
    normalized_name = _normalize_search_text(brand_removed or name).replace(" ", "")
    if normalized_name:
        return normalized_name
    if product.report_number:
        return f"report:{product.report_number}"
    if product.barcode:
        return f"barcode:{product.barcode}"
    return str(product.product_id or id(product))


def _product_information_score(product: Product) -> int:
    score = 0
    for value in [
        product.barcode,
        product.report_number,
        product.food_code,
        product.clean_product_name,
        product.manufacturer_name,
        product.company_name,
        product.rawmtrl_nm,
        product.allergy_warning,
        product.prdlst_dcnm,
        product.image_url,
    ]:
        if value:
            score += 1
    score += min(len(product.ingredients), 5)
    score += min(len(product.allergy), 5)
    score += min(len(product.nutrition), 10)
    return score


def _dedupe_scored_products(scored: List[tuple[float, Product]]) -> List[tuple[float, Product]]:
    deduped: Dict[str, tuple[float, Product]] = {}
    for score, product in scored:
        key = _product_candidate_key(product)
        previous = deduped.get(key)
        if previous is None:
            deduped[key] = (score, product)
            continue
        previous_score, previous_product = previous
        if (
            score > previous_score
            or (
                score == previous_score
                and _product_information_score(product) > _product_information_score(previous_product)
            )
        ):
            deduped[key] = (score, product)
    return sorted(deduped.values(), key=lambda item: item[0], reverse=True)


def _alternative_search_text(product: Product) -> str:
    return " ".join(
        value
        for value in [
            product.clean_product_name,
            product.product_name,
        ]
        if value
    ).strip()


def _alternative_search_tokens(product: Product) -> List[str]:
    source_text = _alternative_search_text(product)
    tokens = _expanded_product_name_tokens(source_text) or _tokenize_search_text(source_text)
    return [
        token
        for token in tokens
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    ][:12]


def _alternative_similarity(reference: Product, candidate: Product) -> float:
    query = _alternative_search_text(reference)
    if not query:
        return 0.0
    text_score = _product_text_score(query, candidate)
    reference_tokens = set(_alternative_search_tokens(reference))
    candidate_tokens = set(_expanded_product_name_tokens(_alternative_search_text(candidate)))
    overlap = reference_tokens & candidate_tokens
    token_score = 0.0
    if overlap:
        token_score = min(0.95, 0.3 + (0.08 * len(overlap)))
    return max(text_score, token_score)


def _same_product_identity(left: Product, right: Product) -> bool:
    if left.barcode and right.barcode and left.barcode == right.barcode:
        return True
    if left.report_number and right.report_number and left.report_number == right.report_number:
        return True
    if left.product_name and right.product_name and left.product_name == right.product_name:
        return True
    return False
