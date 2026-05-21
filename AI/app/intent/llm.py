import json
import re
from typing import List, Optional

from app.intent.common import VALID_INTENTS
from app.intent.text import (
    product_context_summary,
    profile_context_summary,
    recent_history_summary,
)
from app.infrastructure.llm import generate_text, is_llm_enabled
from app.core.models import ChatHistoryItem, Product, Profile


def build_llm_intent_prompt(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> str:
    return f"""
너는 식품 안전 챗봇의 의도 분류기다.
사용자 질문을 보고 아래 intent 중 하나만 골라라.
절대 답변을 생성하지 말고 JSON만 출력해라.
규칙이 애매하면 문맥을 보고 가장 그럴듯한 intent 하나를 고르되, 확신이 없으면 unknown을 선택해라.
사용자는 프로필 이름이나 별칭을 자유롭게 부를 수 있고, 이전 대화를 이어서 짧게 후속질문할 수 있다.

[허용 intent]
- can_i_eat: 특정 제품을 먹어도 되는지, 안전한지, 괜찮은지 묻는 경우
- ingredient_check: 특정 성분, 원재료, 알레르기 성분 포함 여부를 묻는 경우
- nutrition_explain: 칼로리, 당류, 단백질, 지방, 탄수화물, 나트륨 등 영양성분 설명을 원하는 경우
- health_risk_check: 당뇨, 혈당, 고혈압, 다이어트, 질환, 건강 상태 기준으로 제품 위험을 묻는 경우
- product_summary: 제품 정보를 전반적으로 요약해달라는 경우
- missing_product: 제품 정보가 없거나 DB 미등록 제품에 대한 경우
- general_allergy_guide: 특정 제품 없이 알레르기 일반 정보를 묻는 경우
- general_diet_guide: 특정 제품 없이 다이어트, 비건, 식단 일반 정보를 묻는 경우
- general_nutrition_guide: 특정 제품 없이 영양성분 일반 정보를 묻는 경우
- general_food_health: 특정 제품 없이 식품과 건강 관련 일반 질문을 하는 경우
- profile_based_recheck: 이미 본 제품을 다른 사람이나 다른 프로필 기준으로 다시 봐달라는 경우
- unknown: 위에 해당하지 않거나 판단이 어려운 경우

[제품 상태]
{product_context_summary(product)}

[현재 프로필]
{profile_context_summary(profile)}

[이전 대화]
{recent_history_summary(chat_history)}

[사용자 질문]
{message}

[출력 형식]
{{"intent": "위 intent 중 하나"}}
""".strip()


def parse_llm_intent(raw: str) -> str:
    if not raw:
        return "unknown"

    cleaned = raw.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        data = json.loads(cleaned)
        intent = data.get("intent")
        if intent in VALID_INTENTS:
            return intent
    except json.JSONDecodeError:
        pass

    match = re.search(r'"intent"\s*:\s*"([^"]+)"', cleaned)
    if match:
        intent = match.group(1)
        if intent in VALID_INTENTS:
            return intent

    return "unknown"


def llm_fallback_classify_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> str:
    if not is_llm_enabled():
        return "unknown"

    prompt = build_llm_intent_prompt(
        message=message,
        product=product,
        profile=profile,
        chat_history=chat_history,
    )
    raw = generate_text(prompt)

    return parse_llm_intent(raw or "")
