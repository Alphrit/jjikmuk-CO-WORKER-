from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.data.dictionaries import EATABILITY_KEYWORDS, INCLUSION_KEYWORDS
from app.intent.rules import rule_based_classify_intent
from app.intent.text import (
    contains_any,
    detect_ingredient_keyword,
    detect_nutrition_keyword,
    is_context_dependent_followup,
    normalize_message,
)
from app.services.product import (
    find_product_by_barcode,
    find_product_by_report_number,
    find_product_candidates_by_text,
    find_product_from_history,
)
from app.services.product_search import (
    PRODUCT_CONFIRMATION_THRESHOLD,
    explicitly_mentions_product,
    product_match_score,
    same_product,
)

GENERAL_TOPIC_SHIFT_KEYWORDS = [
    "고를 때",
    "골라",
    "고르는",
    "기준",
    "뭘 봐",
    "뭐 봐",
    "어떻게",
    "추천",
    "일반적으로",
    "간식",
    "식단",
    "가이드",
]

PRODUCT_REFERENCE_KEYWORDS = [
    "이 제품",
    "그 제품",
    "현재 제품",
    "방금 제품",
    "스캔",
    "바코드",
    "품목보고",
    "이거",
    "이건",
    "그거",
    "그건",
]

PRODUCT_DETAIL_FOLLOWUP_KEYWORDS = [
    "어떤 성분",
    "어떤 수치",
    "무슨 성분",
    "무슨 수치",
    "왜 조심",
    "왜 주의",
    "때문에 조심",
    "때문에 주의",
    "자세히 알려",
    "자세히 설명",
    "더 자세히",
    "이유",
]

GENERAL_PERSON_CONDITION_PATTERNS = [
    "알레르기가 있는 사람",
    "알러지가 있는 사람",
    "알레르기 있는 사람",
    "알러지 있는 사람",
    "있는 사람",
    "환자",
]


def build_product_confirmation_response(products: list[Product]) -> ChatResponse:
    primary = products[0]
    name = primary.product_name or "이 제품"
    return ChatResponse(
        task_type="product_chat",
        answer_source="rules",
        pipeline_stage="rules",
        intent="unknown",
        answer=f"질문하신 제품이 '{name}'인지 확실하지 않아요. 아래 후보 중 맞는 제품을 선택해 주세요.",
        risk_level="unknown",
        reasons=["제품명 확인 필요"],
        recommended_products=[
            product.model_dump(exclude_none=True)
            | {
                "match_confidence": product.match_confidence,
                "reason": "입력한 문장과 제품명이 일부 비슷하지만 확정하기에는 유사도가 낮아요.",
                "reasons": ["제품명 일부 일치", "확인 필요"],
                "selection_type": "product_candidate",
            }
            for product in products[:3]
        ],
    )


def has_ambiguous_product_candidates(candidates: list[Product]) -> bool:
    if len(candidates) < 2:
        return False

    top_score = candidates[0].match_confidence or 0
    second_score = candidates[1].match_confidence or 0
    if top_score < PRODUCT_CONFIRMATION_THRESHOLD:
        return True
    return second_score >= 0.75 and (top_score - second_score) <= 0.15


def build_confirmation_if_needed(candidates: list[Product]) -> Optional[ChatResponse]:
    if not candidates:
        return None

    top_score = candidates[0].match_confidence or 0
    if top_score < PRODUCT_CONFIRMATION_THRESHOLD or has_ambiguous_product_candidates(candidates):
        return build_product_confirmation_response(candidates)

    return None


def find_product_from_request_text(
    message: str,
    chat_history,
    use_history: bool = True,
) -> tuple[Optional[Product], Optional[ChatResponse]]:
    if not should_search_products_from_message(message):
        if use_history:
            return find_product_from_history(chat_history), None
        return None, None

    candidates = find_product_candidates_by_text(message, limit=3)
    confirmation_response = build_confirmation_if_needed(candidates)
    if confirmation_response is not None:
        return None, confirmation_response
    if candidates:
        return candidates[0], None

    if not use_history:
        return None, None

    return find_product_from_history(chat_history), None


def should_search_products_from_message(message: str) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if contains_any(text, GENERAL_PERSON_CONDITION_PATTERNS):
        return False

    if (
        contains_any(text, ["알레르기", "알러지", "질환", "당뇨", "고혈압"])
        and contains_any(text, ["사람", "환자", "경우", "있으면"])
        and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS)
    ):
        return False

    return True


def find_explicit_product_override(message: str, current_product: Optional[Product]) -> tuple[Optional[Product], Optional[ChatResponse]]:
    if current_product is None:
        return None, None

    if not should_search_products_from_message(message):
        return None, None

    candidates = find_product_candidates_by_text(message, limit=3)
    if not candidates:
        return None, None

    current_product_is_candidate = any(same_product(current_product, candidate) for candidate in candidates)
    top_candidate = candidates[0]
    top_score = top_candidate.match_confidence or 0

    if current_product_is_candidate:
        current_score = product_match_score(current_product, candidates)
        if (
            not same_product(current_product, top_candidate)
            and explicitly_mentions_product(message, top_candidate)
            and top_score >= PRODUCT_CONFIRMATION_THRESHOLD
            and top_score - current_score >= 0.05
        ):
            return top_candidate, None
        return None, None

    if top_score >= PRODUCT_CONFIRMATION_THRESHOLD:
        return top_candidate, None

    confirmation_response = build_confirmation_if_needed(candidates)
    if confirmation_response is not None:
        return None, confirmation_response

    return None, None


def find_recent_history_product_for_followup(
    message: str,
    current_product: Optional[Product],
    chat_history,
) -> Optional[Product]:
    if current_product is None or not chat_history:
        return None

    if not has_product_reference(message, current_product, chat_history):
        return None

    # If the user explicitly names a product in this turn, explicit override/confirmation owns the decision.
    candidates = find_product_candidates_by_text(message, limit=1)
    if candidates and (candidates[0].match_confidence or 0) >= 0.75:
        return None

    history_product = find_product_from_history(chat_history)
    if history_product is None or same_product(history_product, current_product):
        return None

    return history_product


def has_general_topic_shift_cue(text: str) -> bool:
    return contains_any(text, GENERAL_TOPIC_SHIFT_KEYWORDS)


def has_general_condition_frame(text: str) -> bool:
    return contains_any(text, ["있으면", "있는 사람", "환자", "경우", "일반적으로", "제품 고를"])


def has_product_reference(message: str, product: Optional[Product], chat_history) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if contains_any(text, PRODUCT_REFERENCE_KEYWORDS):
        return True

    if product and product.product_name and product.product_name.strip().lower() in text:
        return True

    if product and should_search_products_from_message(text):
        candidates = find_product_candidates_by_text(text, limit=3)
        if any(same_product(product, candidate) for candidate in candidates):
            return True

    if product and chat_history and contains_any(text, PRODUCT_DETAIL_FOLLOWUP_KEYWORDS):
        return not has_general_condition_frame(text)

    if has_general_condition_frame(text) and not contains_any(text, PRODUCT_REFERENCE_KEYWORDS):
        return False

    if is_context_dependent_followup(text, chat_history):
        return True

    if contains_any(text, EATABILITY_KEYWORDS):
        return True

    if detect_ingredient_keyword(text) is not None and contains_any(text, INCLUSION_KEYWORDS + ["없어", "있어"]):
        return True

    if detect_nutrition_keyword(text) is not None and (
        len(text) <= 32 or contains_any(text, ["설명", "알려", "기준", "같은", "수치", "함량"])
    ):
        return True

    if (
        product
        and chat_history
        and contains_any(text, ["영양", "당류", "나트륨", "탄수화물", "단백질", "지방", "칼로리", "열량", "건강 기준"])
        and contains_any(text, ["설명", "알려", "봐줘", "정리", "기준"])
    ):
        return True

    if (
        contains_any(text, ["알레르기", "알러지", "원재료", "성분"])
        and len(text) <= 24
        and chat_history
        and not has_general_topic_shift_cue(text)
    ):
        return True

    return False


def looks_like_general_topic_shift(message: str, profile: Optional[Profile], product: Optional[Product], chat_history) -> bool:
    text = normalize_message(message)
    if not text:
        return False

    if has_product_reference(text, product, chat_history):
        return False

    general_intent = rule_based_classify_intent(
        message=text,
        product=None,
        profile=profile,
        chat_history=[],
    )
    if general_intent.startswith("general_"):
        return True

    if has_general_topic_shift_cue(text) and len(text) >= 8:
        return True

    return False


def resolve_product_context(
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    barcode: Optional[str],
    report_number: Optional[str],
    chat_history,
    conversation_state: Optional[dict] = None,
) -> tuple[Optional[Product], Optional[str], Optional[str], Optional[ChatResponse]]:
    if product is None:
        if report_number:
            product = find_product_by_report_number(report_number)
        elif barcode:
            product = find_product_by_barcode(barcode)
        elif conversation_state:
            state_product = conversation_state.get("last_confirmed_product")
            if isinstance(state_product, dict):
                product = Product.model_validate(state_product)
                barcode = product.barcode
                report_number = product.report_number

    ignore_product_context = looks_like_general_topic_shift(message, profile, product, chat_history)

    if ignore_product_context:
        product = None
        barcode = None
        report_number = None
    else:
        explicit_product, confirmation_response = find_explicit_product_override(message, product)
        if confirmation_response is not None:
            return product, barcode, report_number, confirmation_response
        if explicit_product is not None:
            product = explicit_product
            barcode = product.barcode
            report_number = product.report_number
        else:
            recent_history_product = find_recent_history_product_for_followup(message, product, chat_history)
            if recent_history_product is not None:
                product = recent_history_product
                barcode = product.barcode
                report_number = product.report_number

    if product is None:
        if report_number:
            product = find_product_by_report_number(report_number)
        elif barcode:
            product = find_product_by_barcode(barcode)

        if product is None and not ignore_product_context:
            product, confirmation_response = find_product_from_request_text(
                message,
                chat_history,
                use_history=not looks_like_general_topic_shift(message, profile, None, chat_history),
            )
            if confirmation_response is not None:
                return product, barcode, report_number, confirmation_response

    if product is None and (report_number or barcode):
        product = Product(
            barcode=barcode,
            report_number=report_number,
            product_found=False,
        )

    return product, barcode, report_number, None
