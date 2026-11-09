import re
from typing import List, Optional

from app.intent.common import IntentClassificationResult, LLM_FALLBACK_INTENTS
from app.intent.llm import llm_fallback_classify_intent
from app.intent.rules import rule_based_classify_intent
from app.intent.text import (
    detect_ingredient_keyword,
    detect_nutrition_keyword,
    is_context_dependent_followup,
    normalize_message,
    recent_assistant_message,
    recent_user_message,
)
from app.core.models import ChatHistoryItem, Product, Profile


def _infer_followup_intent_from_assistant_answer(chat_history: List[ChatHistoryItem]) -> str:
    assistant_message = recent_assistant_message(chat_history)
    if not assistant_message:
        return "unknown"

    text = normalize_message(assistant_message)

    if detect_nutrition_keyword(text) is not None or any(
        token in text for token in ["영양성분", "당류", "나트륨", "열량", "칼로리", "탄수화물", "지방", "단백질"]
    ):
        return "nutrition_explain"

    if detect_ingredient_keyword(text) is not None or any(
        token in text for token in ["원재료", "알레르기 유발 성분", "관련 성분", "표기", "포함되어 있어요", "보이지 않아요"]
    ):
        return "ingredient_check"

    if any(
        token in text for token in ["제품 정보를 먼저", "스캔이 완료됐어요", "핵심 정보", "주요 원재료"]
    ):
        return "product_summary"

    if any(
        token in text for token in ["피하는 편", "먹어도", "걱정할 만한", "안전해 보여요", "크게 걱정할 만한"]
    ):
        return "can_i_eat"

    return "unknown"


def _infer_followup_intent_from_history(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> str:
    if not is_context_dependent_followup(message, chat_history):
        return "unknown"

    previous_user_message = recent_user_message(chat_history)
    if previous_user_message:
        prior_history = chat_history[:-1]
        previous_intent = rule_based_classify_intent(
            message=previous_user_message,
            product=product,
            profile=profile,
            chat_history=prior_history,
        )
        if previous_intent not in LLM_FALLBACK_INTENTS:
            return previous_intent

    assistant_intent = _infer_followup_intent_from_assistant_answer(chat_history)
    if assistant_intent not in LLM_FALLBACK_INTENTS:
        return assistant_intent

    return "unknown"


def _general_food_question(message: str, product: Optional[Product], profile: Optional[Profile]) -> bool:
    if product is not None:
        return False
    from app.services.conversation_router import _explicit_named_product_query
    from app.services.hybrid import _FOOD_DOMAIN_TERMS
    if _explicit_named_product_query(message, profile) is not None:
        # A named product still goes to the candidate search.
        return False
    text = message.lower()
    # Only questions that ask for an explanation; '성분 좀 볼래' wants a product.
    asks = re.search(r"뭐|뭘|무엇|뜻|의미|차이|어떻게|왜|봐야|고를|고르|기준|설명", text)
    return bool(asks) and any(term in text for term in _FOOD_DOMAIN_TERMS)


def classify_intent_result(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile] = None,
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> IntentClassificationResult:
    history = chat_history or []
    rule_intent = rule_based_classify_intent(
        message=message,
        product=product,
        profile=profile,
        chat_history=history,
    )

    if rule_intent not in LLM_FALLBACK_INTENTS:
        return IntentClassificationResult(
            intent=rule_intent,
            source="rules",
            rule_intent=rule_intent,
        )

    followup_intent = _infer_followup_intent_from_history(
        message=message,
        product=product,
        profile=profile,
        chat_history=history,
    )
    if followup_intent != "unknown":
        return IntentClassificationResult(
            intent=followup_intent,
            source="followup_context",
            rule_intent=rule_intent,
        )

    llm_intent = llm_fallback_classify_intent(
        message=message,
        product=product,
        profile=profile,
        chat_history=history,
    )

    if llm_intent != "unknown":
        return IntentClassificationResult(
            intent=llm_intent,
            source="llm_fallback",
            rule_intent=rule_intent,
            llm_intent=llm_intent,
        )

    if _general_food_question(message, product, profile):
        # '아이 간식 고를 때 뭘 봐야 해?': a food question with no product name
        # gets the general-explanation path (evidence or an honest
        # 'not in the reviewed sources') instead of '질문 뜻을 잡지 못했어요'.
        return IntentClassificationResult(
            intent="general_nutrition_guide",
            source="food_domain_fallback",
            rule_intent=rule_intent,
            llm_intent=llm_intent,
        )

    return IntentClassificationResult(
        intent="unknown",
        source="unknown",
        rule_intent=rule_intent,
        llm_intent=llm_intent,
    )


def classify_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile] = None,
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> str:
    return classify_intent_result(
        message=message,
        product=product,
        profile=profile,
        chat_history=chat_history,
    ).intent
