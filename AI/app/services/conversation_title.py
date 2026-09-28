"""Privacy-friendly, deterministic conversation title generation.

Titles are display metadata, not a substitute for persisted chat messages.  This
module deliberately does not call an LLM and only accepts an authoritative product
name through the ``verified_product_name`` argument.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Optional


DEFAULT_CONVERSATION_TITLE: Final = "새 채팅"
MAX_CONVERSATION_TITLE_LENGTH: Final = 32

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[\s.-]?)?"
    r"(?:0?1[016789]|0?2|0?[3-6][1-5]|0?70)"
    r"[\s.)-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
# Includes common EAN/UPC barcodes and longer account-like numeric identifiers.
_LONG_IDENTIFIER_RE = re.compile(r"(?<!\d)(?:\d[\s-]?){7,18}\d(?!\d)")
_SENSITIVE_LABEL_RE = re.compile(
    r"(?:바코드|전화번호|휴대폰|연락처|이메일|e-?mail)\s*[:=#-]?",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_EDGE_DECORATION_RE = re.compile(
    r"^[\s\-_|:;,.!?'“”‘’\[\](){}<>/\\]+|"
    r"[\s\-_|:;,.!?'“”‘’\[\](){}<>/\\]+$"
)

_PROMPT_INJECTION_PATTERNS = (
    re.compile(
        r"(?:이전|위|앞선|기존|모든)\s*(?:지시|명령|규칙|프롬프트)"
        r".{0,20}(?:무시|취소|따르지)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:시스템|개발자|developer|system)\s*(?:프롬프트|메시지|지시)", re.IGNORECASE),
    re.compile(
        r"(?:프롬프트|내부\s*규칙|시스템\s*메시지).{0,20}"
        r"(?:공개|출력|보여|알려)",
        re.IGNORECASE,
    ),
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?", re.IGNORECASE),
    re.compile(r"(?:reveal|print|show)\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message)", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:system|developer|assistant)\s*:\s*", re.IGNORECASE),
)

_SCAN_ONLY_RE = re.compile(
    r"^(?:제품\s*)?(?:스캔|스캔했어요?|스캔\s*완료|사진|이미지|ocr|"
    r"이거|이건|그거|그건|[이것그것은는이가을를\s]*)$",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^(?:안녕(?:하세요|해|하십니까)?|반가워(?:요)?|저기요|죄송한데)\s*[,!.?~-]*\s*",
    re.IGNORECASE,
)
_TRAILING_REQUEST_RE = re.compile(
    r"\s*(?:좀\s*)?(?:알려\s*줘|알려줘|설명해\s*줘|설명해줘|"
    r"확인해\s*줘|확인해줘|찾아\s*줘|찾아줘|추천해\s*줘|추천해줘|"
    r"궁금해(?:요)?|물어보고\s*싶어(?:요)?)\s*$",
    re.IGNORECASE,
)

_INTENT_TITLES: Final[dict[str, str]] = {
    "can_i_eat": "먹어도 되는지 확인",
    "ingredient_check": "원재료·알레르기 확인",
    "nutrition_explain": "영양성분 확인",
    "product_summary": "제품 정보 확인",
    "alternative_food_recommend": "대체 식품 추천",
    "food_compare": "제품 비교",
    "recommendation": "식품 추천",
    "missing_product": "제품 찾기",
    "out_of_domain": "지원 범위 안내",
    "unknown": "식품 질문",
}
_PRODUCT_INTENT_SUFFIXES: Final[dict[str, str]] = {
    "can_i_eat": "섭취 확인",
    "ingredient_check": "성분 확인",
    "nutrition_explain": "영양성분 확인",
    "product_summary": "제품 정보",
}
_GENERAL_INTENT_KEYWORDS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("알레르기", "알러지"), "알레르기 식품 상담"),
    (("저당",), "저당 식품 상담"),
    (("나트륨", "저염"), "나트륨·저염 식품 상담"),
    (("영양성분", "영양 성분", "칼로리", "열량"), "영양성분 식품 상담"),
)
_GENERIC_PRODUCT_REFERENCES: Final = {
    "제품",
    "이 제품",
    "그 제품",
    "이거",
    "그거",
    "unknown",
}


def _normalize_unicode_and_space(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    characters: list[str] = []
    for character in value:
        if character in "\r\n\t":
            characters.append(" ")
        elif not unicodedata.category(character).startswith("C"):
            characters.append(character)
    return _SPACE_RE.sub(" ", "".join(characters)).strip()


def _contains_prompt_injection(value: str) -> bool:
    return any(pattern.search(value) for pattern in _PROMPT_INJECTION_PATTERNS)


def _contains_sensitive_identifier(value: str) -> bool:
    normalized = _normalize_unicode_and_space(value)
    return bool(
        _EMAIL_RE.search(normalized)
        or _PHONE_RE.search(normalized)
        or _LONG_IDENTIFIER_RE.search(normalized)
    )


def _remove_sensitive_identifiers(value: str) -> str:
    value = _URL_RE.sub(" ", value)
    value = _EMAIL_RE.sub(" ", value)
    value = _PHONE_RE.sub(" ", value)
    value = _LONG_IDENTIFIER_RE.sub(" ", value)
    value = _SENSITIVE_LABEL_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _truncate_title(value: str) -> str:
    if len(value) <= MAX_CONVERSATION_TITLE_LENGTH:
        return value

    prefix = value[: MAX_CONVERSATION_TITLE_LENGTH + 1]
    boundary = prefix.rfind(" ", 0, MAX_CONVERSATION_TITLE_LENGTH + 1)
    if boundary >= MAX_CONVERSATION_TITLE_LENGTH // 2:
        prefix = prefix[:boundary]
    else:
        prefix = value[:MAX_CONVERSATION_TITLE_LENGTH]
    return prefix.rstrip(" -_|:;,.!?")


def normalize_conversation_title(
    value: object,
    *,
    fallback: str = DEFAULT_CONVERSATION_TITLE,
) -> str:
    """Return a safe title suitable for persistence and display.

    Sensitive identifiers and URLs are removed. Prompt-injection-like text is not
    reused as a title. Blank or unusable input resolves to ``fallback``.
    """
    if fallback == "":
        safe_fallback = ""
    else:
        fallback_text = (
            _normalize_unicode_and_space(fallback)
            if isinstance(fallback, str)
            else DEFAULT_CONVERSATION_TITLE
        )
        if _contains_prompt_injection(fallback_text):
            fallback_text = DEFAULT_CONVERSATION_TITLE
        fallback_text = _remove_sensitive_identifiers(fallback_text)
        fallback_text = _EDGE_DECORATION_RE.sub("", fallback_text).strip()
        safe_fallback = _truncate_title(fallback_text) or DEFAULT_CONVERSATION_TITLE
    if not isinstance(value, str):
        return safe_fallback

    normalized = _normalize_unicode_and_space(value)
    if not normalized or _contains_prompt_injection(normalized):
        return safe_fallback

    normalized = _remove_sensitive_identifiers(normalized)
    normalized = _EDGE_DECORATION_RE.sub("", normalized).strip()
    if not normalized:
        return safe_fallback
    return _truncate_title(normalized) or safe_fallback


def is_valid_conversation_title(value: object) -> bool:
    """Return whether a user-supplied title is safe before normalization."""
    if not isinstance(value, str):
        return False
    normalized = _normalize_unicode_and_space(value)
    return bool(
        normalized
        and len(normalized) <= MAX_CONVERSATION_TITLE_LENGTH
        and not _contains_prompt_injection(normalized)
        and not _contains_sensitive_identifier(normalized)
        and not _URL_RE.search(normalized)
    )


def validate_conversation_title(value: object) -> str:
    """Validate a manually edited title and return its normalized representation.

    ``ValueError`` is raised for blank, oversized, identifier-bearing, URL-bearing,
    or prompt-injection-like input. Whitespace and harmless edge punctuation are
    normalized in the returned value.
    """
    if not is_valid_conversation_title(value):
        raise ValueError("conversation title is blank, unsafe, or too long")
    normalized = normalize_conversation_title(value, fallback="")
    if not normalized:
        raise ValueError("conversation title is blank after normalization")
    return normalized


def _intent_title(intent: Optional[str], message: str) -> Optional[str]:
    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent.startswith("general_"):
        for keywords, title in _GENERAL_INTENT_KEYWORDS:
            if any(keyword in message.lower() or keyword in normalized_intent for keyword in keywords):
                return title
        return "식품·영양 상담"
    return _INTENT_TITLES.get(normalized_intent)


def _product_title(product_name: str, intent: Optional[str]) -> Optional[str]:
    if not product_name or product_name.lower() in _GENERIC_PRODUCT_REFERENCES:
        return None
    normalized_intent = str(intent or "").strip().lower()
    suffix = _PRODUCT_INTENT_SUFFIXES.get(normalized_intent)
    if not suffix:
        suffix = "제품 비교" if normalized_intent == "food_compare" else "제품 상담"

    available = MAX_CONVERSATION_TITLE_LENGTH - len(suffix) - 1
    if available <= 0:
        return suffix
    shortened_name = _truncate_title(product_name[:available].rstrip())
    return f"{shortened_name} {suffix}" if shortened_name else suffix


def _message_title(message: str) -> Optional[str]:
    if not message or _contains_prompt_injection(message):
        return None

    cleaned = _remove_sensitive_identifiers(message)
    cleaned = _GREETING_RE.sub("", cleaned)
    cleaned = _EDGE_DECORATION_RE.sub("", cleaned).strip()
    if not cleaned or _SCAN_ONLY_RE.fullmatch(cleaned):
        return None

    cleaned = _TRAILING_REQUEST_RE.sub("", cleaned)
    cleaned = _EDGE_DECORATION_RE.sub("", cleaned).strip()
    if not cleaned or _SCAN_ONLY_RE.fullmatch(cleaned):
        return None
    return _truncate_title(cleaned)


def generate_conversation_title(
    first_user_message: object = None,
    *,
    verified_product_name: object = None,
    intent: Optional[str] = None,
) -> str:
    """Generate a short Korean title without an external model or API call.

    Callers must pass ``verified_product_name`` only after resolving a product from
    an authoritative repository. Raw client product payloads must not be used.
    """
    message = (
        _normalize_unicode_and_space(first_user_message)
        if isinstance(first_user_message, str)
        else ""
    )
    product_name = (
        normalize_conversation_title(verified_product_name, fallback="")
        if isinstance(verified_product_name, str)
        and not _contains_prompt_injection(_normalize_unicode_and_space(verified_product_name))
        else ""
    )
    if product_name == DEFAULT_CONVERSATION_TITLE:
        product_name = ""

    product_based = _product_title(product_name, intent)
    if product_based:
        return normalize_conversation_title(product_based)

    message_based = _message_title(message)
    intent_based = _intent_title(intent, message)

    # General intents are clearer as stable topic labels than as full questions.
    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent == "out_of_domain" and intent_based:
        return intent_based
    if normalized_intent.startswith("general_") and intent_based:
        return intent_based
    if message_based:
        return normalize_conversation_title(message_based)
    if intent_based:
        return intent_based
    return DEFAULT_CONVERSATION_TITLE


__all__ = [
    "DEFAULT_CONVERSATION_TITLE",
    "MAX_CONVERSATION_TITLE_LENGTH",
    "generate_conversation_title",
    "is_valid_conversation_title",
    "normalize_conversation_title",
    "validate_conversation_title",
]
