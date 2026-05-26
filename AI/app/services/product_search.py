import re
from difflib import SequenceMatcher
from typing import List, Optional

from app.core.models import Product


PRODUCT_CONFIRMATION_THRESHOLD = 0.9
SIZE_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?\s?(?:g|kg|ml|l)")
GENERIC_PRODUCT_TOKENS = {"농심", "오리온", "매일", "서울우유", "남양", "90g", "50g", "60g", "70g", "200ml", "1l"}


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
        "먹어도",
        "돼",
        "되나",
        "설명해줘",
        "추천해줘",
        "먹어도돼",
        "있는",
        "사람",
        "사람은",
        "알레르기",
        "알러지",
        "있으면",
    }
    return [
        token
        for token in normalize_search_text(text).split()
        if len(token) >= 2 and token not in stopwords
    ]


def distinctive_product_tokens(product_name: str) -> List[str]:
    return [
        token
        for token in tokenize_search_text(product_name)
        if token not in GENERIC_PRODUCT_TOKENS and not token.isdigit()
    ]


def product_text_score(query: str, product: Product) -> float:
    if not product.product_name:
        return 0.0

    normalized_query = normalize_search_text(query)
    normalized_name = normalize_search_text(product.product_name)
    if not normalized_query or not normalized_name:
        return 0.0

    if normalized_name in normalized_query:
        return 1.0

    query_tokens = set(tokenize_search_text(normalized_query))
    name_tokens = set(tokenize_search_text(normalized_name))
    distinctive_tokens = distinctive_product_tokens(normalized_name)

    exact_distinctive_matches = [
        token for token in distinctive_tokens if token in normalized_query
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
        return max(0.85, 0.55 + (0.3 * match_ratio))

    fuzzy_token_score = 0.0
    for query_token in query_tokens:
        for name_token in distinctive_tokens:
            fuzzy_token_score = max(
                fuzzy_token_score,
                SequenceMatcher(None, query_token, name_token).ratio(),
            )

    token_score = len(query_tokens & name_tokens) / len(name_tokens) if name_tokens else 0.0
    sequence_score = SequenceMatcher(None, normalized_query, normalized_name).ratio()
    if fuzzy_token_score >= 0.65:
        return max(token_score, sequence_score, 0.45)

    return max(token_score, sequence_score)


def same_product(left: Optional[Product], right: Optional[Product]) -> bool:
    if left is None or right is None:
        return False

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


def product_size_tokens(product: Product) -> list[str]:
    if not product.product_name:
        return []
    return [compact_search_text(token) for token in SIZE_TOKEN_PATTERN.findall(product.product_name)]


def product_name_tokens(product: Product) -> list[str]:
    if not product.product_name:
        return []

    tokens = []
    for token in re.split(r"[\s/()]+", normalize_search_text(product.product_name)):
        token = token.strip()
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

    size_matched = any(size_token in message_compact for size_token in product_size_tokens(product))
    if not size_matched:
        return False

    matched_name_tokens = [
        token
        for token in product_name_tokens(product)
        if compact_search_text(token) in message_compact
    ]
    return len(matched_name_tokens) >= 1
