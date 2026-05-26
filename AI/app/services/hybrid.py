import re
from typing import List, Optional, Tuple

from app.infrastructure.llm import generate_text, is_llm_enabled
from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile
from app.prompts.builder import (
    build_general_fallback_prompt,
    build_multi_profile_rule_explainer_prompt,
    build_rule_explainer_prompt,
)
from app.infrastructure.rag import get_rag_context


def _split_answer_and_questions(text: str) -> Tuple[str, List[str]]:
    marker = "원하시면 아래와 같은 내용도 도와드릴게요:"

    if marker not in text:
        return text.strip(), []

    answer_part, questions_part = text.split(marker, 1)

    questions = []
    for line in questions_part.splitlines():
        cleaned = line.strip()
        cleaned = re.sub(r"^[\-\*\•]\s*", "", cleaned)
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)

        if cleaned:
            questions.append(cleaned)

    return answer_part.strip(), [_to_user_utterance(question) for question in questions[:3]]


def _to_user_utterance(question: str) -> str:
    text = question.strip()
    replacements = [
        ("더 자세히 설명해드릴까요?", "더 자세히 설명해줘"),
        ("설명해드릴까요?", "설명해줘"),
        ("정리해드릴까요?", "정리해줘"),
        ("알려드릴까요?", "알려줘"),
        ("확인해볼까요?", "확인해줘"),
        ("살펴볼까요?", "살펴봐줘"),
        ("비교해볼까요?", "비교해줘"),
        ("물어보실까요?", "물어봐줘"),
        ("볼까요?", "알려줘"),
        ("볼까요", "알려줘"),
        ("드릴까요?", "줘"),
        ("드릴까요", "줘"),
        ("쉬우실까요?", "쉬워지게 설명해줘"),
        ("편하실까요?", "편하게 설명해줘"),
        ("도움이 될까요?", "도움 되게 설명해줘"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)

    polite_suffixes = [
        ("해주세요", "해줘"),
        ("알려주세요", "알려줘"),
        ("설명해주세요", "설명해줘"),
        ("정리해주세요", "정리해줘"),
        ("확인해주세요", "확인해줘"),
    ]
    for source, target in polite_suffixes:
        if text.endswith(source):
            text = text[: -len(source)] + target

    return text.rstrip("?").strip()


def enhance_rule_response(
    rule_response: ChatResponse,
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    profiles: Optional[List[Profile]] = None,
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> ChatResponse:
    if not is_llm_enabled():
        return rule_response

    if rule_response.task_type != "product_chat":
        return rule_response

    if rule_response.intent not in {
        "product_summary",
        "ingredient_check",
        "nutrition_explain",
        "can_i_eat",
        "health_risk_check",
    }:
        return rule_response

    rag_context = get_rag_context(message=message, profile=profile, product=product)

    if profiles and len(profiles) > 1:
        prompt = build_multi_profile_rule_explainer_prompt(
            message=message,
            rule_response=rule_response,
            profiles=profiles,
            product=product,
            chat_history=chat_history,
            rag_context=rag_context,
        )
    else:
        prompt = build_rule_explainer_prompt(
            message=message,
            rule_response=rule_response,
            profile=profile,
            product=product,
            chat_history=chat_history,
            rag_context=rag_context,
        )

    llm_answer = generate_text(prompt)

    if not llm_answer:
        return rule_response

    answer, recommended_questions = _split_answer_and_questions(llm_answer)

    return rule_response.model_copy(update={
        "answer": answer,
        "recommended_questions": recommended_questions,
        "answer_source": "hybrid" if not rag_context else "rag",
    })


def try_general_llm_fallback(
    base_response: ChatResponse,
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    chat_history: Optional[List[ChatHistoryItem]] = None,
) -> ChatResponse:
    if not is_llm_enabled():
        return base_response

    if product is not None:
        return base_response

    if base_response.intent not in {
        "unknown",
        "general_food_health",
        "general_allergy_guide",
        "general_diet_guide",
        "general_nutrition_guide",
    }:
        return base_response

    rag_context = get_rag_context(message=message, profile=profile, product=product)

    prompt = build_general_fallback_prompt(
        message=message,
        profile=profile,
        chat_history=chat_history,
        rag_context=rag_context,
    )

    llm_answer = generate_text(prompt)

    if not llm_answer:
        return base_response

    answer, recommended_questions = _split_answer_and_questions(llm_answer)

    return base_response.model_copy(update={
        "answer": answer,
        "recommended_questions": recommended_questions,
        "answer_source": "llm" if not rag_context else "rag",
        "task_type": "general_chat",
    })
