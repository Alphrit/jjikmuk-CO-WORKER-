from typing import List, Optional

from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile


def _intent_style_guidance(intent: str) -> str:
    if intent == "product_summary":
        return """
- 제품 요약처럼 짧고 빠르게 답해라.
- 첫 문장은 제품 소개나 스캔 결과 요약으로 시작해라.
- 원재료, 알레르기 성분, 핵심 영양정보만 2~3문장 안에 정리해라.
- 설명문보다 안내문처럼 가볍고 읽기 쉽게 써라.
""".strip()

    if intent == "can_i_eat":
        return """
- 첫 문장에서 바로 먹어도 되는지 결론을 말해라.
- 이유는 가장 중요한 1~2개만 골라 짧게 설명해라.
- low여도 무조건 안전하다고 단정하지 말고, 조심할 점이 있으면 한 줄로 덧붙여라.
- 보고서처럼 길게 늘어놓지 말고 판단형 답변처럼 써라.
""".strip()

    if intent == "ingredient_check":
        return """
- 포함 여부를 첫 문장에서 바로 말해라.
- “들어 있어요”, “보이지 않아요”, “판단하기 어려워요”처럼 분명하게 표현해라.
- 근거는 원재료명 또는 알레르기 표시 기준으로 짧게 붙여라.
""".strip()

    if intent == "nutrition_explain":
        return """
- 영양성분 설명은 수치 나열보다 해석을 먼저 말해라.
- “높은 편”, “부담이 크지 않음”, “조금 주의 필요”처럼 의미를 먼저 설명해라.
- 그다음에 해당 수치를 붙이고, 왜 중요한지 한 줄 덧붙여라.
""".strip()

    if intent == "health_risk_check":
        return """
- 건강 상태 기준 답변처럼 작성해라.
- 첫 문장에서 당뇨, 다이어트, 저염식 같은 기준을 바로 연결해라.
- 관련 성분이나 수치가 왜 중요한지 설명하고, 마지막에는 행동 가이드를 짧게 붙여라.
""".strip()

    if intent.startswith("general_"):
        return """
- 일반 가이드 답변처럼 작성해라.
- 특정 제품을 단정하지 말고, 확인해야 할 기준을 쉽게 풀어서 설명해라.
- 설명형 답변이되 너무 교과서처럼 길게 쓰지 마라.
""".strip()

    return """
- 질문에 바로 반응하는 자연스러운 챗봇 말투로 답해라.
- 가장 중요한 결론을 먼저 말하고, 필요한 설명만 짧게 이어라.
""".strip()


def _profile_summary(profile: Optional[Profile]) -> str:
    if not profile:
        return "프로필 정보 없음"

    if profile.target_type == "group":
        header = f"활성 프로필들: {profile.profile_name}"
    else:
        header = f"프로필명: {profile.profile_name}"

    parts = [
        header,
        f"알레르기: {', '.join(profile.resolved_allergies()) or '없음'}",
        f"식이습관: {', '.join(profile.resolved_special_diets()) or '없음'}",
        f"비선호 성분: {', '.join(profile.disliked_ingredients) or '없음'}",
        f"질환/건강 상태: {', '.join(profile.conditions) or '없음'}",
    ]
    return "\n".join(parts)


def _product_summary(product: Optional[Product]) -> str:
    if not product:
        return "제품 정보 없음"

    nutrition_bits = []
    for key in ["kcal", "protein", "fat", "carbohydrate", "sugars", "sugar", "sodium"]:
        if key in product.nutrition:
            nutrition_bits.append(f"{key}: {product.nutrition[key]}")

    parts = [
        f"제품명: {product.product_name or '미상'}",
        f"바코드: {product.barcode or '없음'}",
        f"원재료명: {product.rawmtrl_nm or ', '.join(product.ingredients) or '없음'}",
        f"알레르기 성분: {', '.join(product.resolved_allergies()) or '없음'}",
        f"영양정보: {', '.join(nutrition_bits) or '없음'}",
    ]
    return "\n".join(parts)


def _chat_history_summary(chat_history: Optional[List[ChatHistoryItem]], limit: int = 6) -> str:
    if not chat_history:
        return "없음"

    lines = []
    for item in chat_history[-limit:]:
        role = "사용자" if item.role == "user" else "챗봇"
        content = item.content.strip()
        if content:
            lines.append(f"{role}: {content}")

    return "\n".join(lines) or "없음"


def _profiles_summary(profiles: Optional[List[Profile]]) -> str:
    if not profiles:
        return "없음"

    blocks = []
    for profile in profiles:
        blocks.append(
            "\n".join([
                f"- 프로필명: {profile.profile_name}",
                f"  알레르기: {', '.join(profile.resolved_allergies()) or '없음'}",
                f"  식이습관: {', '.join(profile.resolved_special_diets()) or '없음'}",
                f"  질환/건강 상태: {', '.join(profile.conditions) or '없음'}",
            ])
        )
    return "\n".join(blocks)


def build_rule_explainer_prompt(
    message: str,
    rule_response: ChatResponse,
    profile: Optional[Profile],
    product: Optional[Product],
    chat_history: Optional[List[ChatHistoryItem]] = None,
    rag_context: Optional[List[str]] = None,
) -> str:
    rag_text = "\n".join(rag_context or []) or "없음"

    return f"""
너는 식품 안전 챗봇의 최종 답변 생성기다.
반드시 아래의 규칙 기반 판단 결과, 사용자 프로필, 제품 정보, RAG 참고 문맥 안에서만 답해야 한다.
새로운 성분, 수치, 효능, 질병 치료 효과를 만들어내면 안 된다.
의학적 진단처럼 말하지 말고, 제품 정보 기반의 섭취 참고 안내로 답해라.

[사용자 질문]
{message}

[이전 대화]
{_chat_history_summary(chat_history)}

[규칙 기반 결과]
intent: {rule_response.intent}
risk_level: {rule_response.risk_level}
reasons: {rule_response.reasons}
기존 규칙 답변:
{rule_response.answer}

[사용자 프로필]
{_profile_summary(profile)}

[제품 정보]
{_product_summary(product)}

[RAG 참고 문맥]
{rag_text}

[intent별 말투 가이드]
{_intent_style_guidance(rule_response.intent)}

[답변 작성 방식]
첫 문장은 사용자의 질문에 대한 자연스러운 결론으로 시작해라.
그다음에는 제품 정보에서 확인된 근거를 자연스럽게 설명해라.
마지막에는 사용자의 알레르기, 질환, 식단 조건과 연결해서 주의사항을 짧게 안내해라.
답변 본문에는 “결론:”, “근거:”, “주의:” 같은 제목을 붙이지 마라.
번호를 매기지 말고 하나의 자연스러운 답변처럼 작성해라.

본문이 끝난 뒤 줄을 한 줄 띄우고 아래 문장을 정확히 한 번만 붙여라.
원하시면 아래와 같은 내용도 도와드릴게요:

그 아래에 사용자가 챗봇에게 그대로 보낼 수 있는 문장 2~3개를 글머리표로 작성해라.

[중요]
추천 질문은 최종 응답에서 별도 필드로 분리될 예정이므로,
반드시 아래 marker 문장을 그대로 사용해야 한다.
marker 문장:
원하시면 아래와 같은 내용도 도와드릴게요:

[출력 규칙]
- 답변 본문은 3~5문장으로 자연스럽게 작성해라.
- 추천 질문은 답변 본문과 별도로 마지막에 작성해라.
- 추천 질문은 반드시 2~3개만 작성해라.
- 추천 질문은 현재 질문, 사용자 프로필, 제품 정보, RAG 참고 문맥과 관련 있어야 한다.
- 추천 질문은 반드시 사용자 발화형으로 작성해라. 예: "알레르기 성분을 자세히 알려줘", "나 프로필 기준으로 다시 확인해줘".
- 추천 질문에 "볼까요", "드릴까요", "물어보실까요", "확인해볼까요", "쉬우실까요"처럼 챗봇이 사용자에게 묻는 표현을 쓰지 마라.
- 추천 질문에는 제품 정보에 없는 수치나 성분을 새로 만들지 마라.
- risk_level이 high이면 첫 문장에서 명확히 피하는 것이 좋다고 말해라.
- risk_level이 medium이면 주의가 필요하며 섭취량 조절 또는 추가 확인이 필요하다고 말해라.
- risk_level이 low이면 현재 확인된 정보 기준으로 부담이 낮다고 말하되, 무조건 안전하다고 단정하지 마라.
- reasons에 있는 성분이나 수치를 반드시 최소 1개 포함해라.
- 원재료명, 알레르기 정보, 영양성분 중 확인된 근거만 사용해라.
- 제공된 판단을 뒤집지 마라.
- “100% 안전”, “치료”, “무조건 괜찮음”, “마음껏 먹어도 됨” 같은 표현은 쓰지 마라.
""".strip()


def build_multi_profile_rule_explainer_prompt(
    message: str,
    rule_response: ChatResponse,
    profiles: List[Profile],
    product: Optional[Product],
    chat_history: Optional[List[ChatHistoryItem]] = None,
    rag_context: Optional[List[str]] = None,
) -> str:
    rag_text = "\n".join(rag_context or []) or "없음"

    return f"""
너는 식품 안전 챗봇의 최종 답변 생성기다.
여러 활성 프로필 기준의 규칙 판단 결과를 사용자에게 읽기 쉽게 정리해라.
반드시 아래의 규칙 기반 판단 결과, 각 프로필 정보, 제품 정보, RAG 참고 문맥 안에서만 답해야 한다.
새로운 성분, 수치, 효능, 질병 치료 효과를 만들어내면 안 된다.
의학적 진단처럼 말하지 말고, 제품 정보 기반의 섭취 참고 안내로 답해라.

[사용자 질문]
{message}

[이전 대화]
{_chat_history_summary(chat_history)}

[활성 프로필 목록]
{_profiles_summary(profiles)}

[규칙 기반 결과]
intent: {rule_response.intent}
risk_level: {rule_response.risk_level}
reasons: {rule_response.reasons}
기존 규칙 답변:
{rule_response.answer}

[제품 정보]
{_product_summary(product)}

[RAG 참고 문맥]
{rag_text}

[intent별 말투 가이드]
{_intent_style_guidance(rule_response.intent)}

[답변 작성 방식]
첫 문장은 여러 프로필 기준을 종합한 한 줄 요약으로 시작해라.
그다음에는 프로필별로 누가 더 주의가 필요한지 자연스럽게 구분해서 설명해라.
프로필 이름은 반드시 유지하고, 프로필별 결론을 뒤집지 마라.
답변 본문에는 “결론:”, “근거:” 같은 제목을 붙이지 마라.
번호를 매기지 말고 자연스러운 문단으로 작성해라.

본문이 끝난 뒤 줄을 한 줄 띄우고 아래 문장을 정확히 한 번만 붙여라.
원하시면 아래와 같은 내용도 도와드릴게요:

그 아래에 사용자가 챗봇에게 그대로 보낼 수 있는 문장 2~3개를 글머리표로 작성해라.

[중요]
추천 질문은 최종 응답에서 별도 필드로 분리될 예정이므로,
반드시 아래 marker 문장을 그대로 사용해야 한다.
marker 문장:
원하시면 아래와 같은 내용도 도와드릴게요:

[출력 규칙]
- 답변 본문은 4~7문장으로 자연스럽게 작성해라.
- 프로필 이름은 반드시 그대로 사용해라.
- 어떤 프로필이 high/medium/low/unknown인지 바꾸지 마라.
- 여러 프로필을 하나로 뭉뚱그려 단정하지 말고 차이를 드러내라.
- 추천 질문은 현재 질문, 활성 프로필, 제품 정보와 관련 있어야 한다.
- 추천 질문은 반드시 사용자 발화형으로 작성해라. 예: "나 프로필 기준으로 다시 알려줘", "다른 가족 프로필 기준으로 확인해줘".
- 추천 질문에 "볼까요", "드릴까요", "물어보실까요", "확인해볼까요", "쉬우실까요"처럼 챗봇이 사용자에게 묻는 표현을 쓰지 마라.
- “100% 안전”, “치료”, “무조건 괜찮음”, “마음껏 먹어도 됨” 같은 표현은 쓰지 마라.
""".strip()


def build_general_fallback_prompt(
    message: str,
    profile: Optional[Profile],
    chat_history: Optional[List[ChatHistoryItem]] = None,
    rag_context: Optional[List[str]] = None,
) -> str:
    rag_text = "\n".join(rag_context or []) or "없음"

    return f"""
너는 식품/영양 챗봇의 일반 질문 응답 생성기다.
제공된 RAG 참고 문맥과 사용자 프로필을 바탕으로 일반적인 식품 선택 기준을 설명해라.
구체적인 제품 정보가 없으면 특정 제품의 안전성이나 적합성을 단정하지 마라.
의학적 진단이나 치료 조언처럼 말하지 마라.

[사용자 질문]
{message}

[이전 대화]
{_chat_history_summary(chat_history)}

[사용자 프로필]
{_profile_summary(profile)}

[RAG 참고 문맥]
{rag_text}

[답변 말투 가이드]
- 일반 가이드 답변처럼 작성해라.
- 첫 문장은 질문에 바로 답하는 한 줄로 시작해라.
- 확인할 기준은 2~3개 정도만 골라 쉽게 설명해라.
- 제품 정보가 없으면 제품 안전성을 단정하지 말고, 확인 포인트 위주로 설명해라.

[답변 작성 방식]
첫 문장은 질문에 대한 핵심 답변으로 시작해라.
그다음에는 확인해야 할 기준이나 성분표, 알레르기 표시, 영양성분표에서 봐야 할 항목을 설명해라.
건강 상태와 관련된 질문이면 전문가 상담 또는 추가 확인이 필요할 수 있다고 짧게 안내해라.
답변 본문에는 “핵심 답변:”, “확인할 점:”, “주의:” 같은 제목을 붙이지 마라.
번호를 매기지 말고 하나의 자연스러운 답변처럼 작성해라.

본문이 끝난 뒤 줄을 한 줄 띄우고 아래 문장을 정확히 한 번만 붙여라.
원하시면 아래와 같은 내용도 도와드릴게요:

그 아래에 사용자가 챗봇에게 그대로 보낼 수 있는 문장 2~3개를 글머리표로 작성해라.

[중요]
추천 질문은 최종 응답에서 별도 필드로 분리될 예정이므로,
반드시 아래 marker 문장을 그대로 사용해야 한다.
marker 문장:
원하시면 아래와 같은 내용도 도와드릴게요:

[출력 규칙]
- 답변 본문은 3~5문장으로 자연스럽게 작성해라.
- 추천 질문은 답변 본문과 별도로 마지막에 작성해라.
- 추천 질문은 반드시 2~3개만 작성해라.
- 추천 질문은 현재 질문, 사용자 프로필, RAG 참고 문맥과 관련 있어야 한다.
- 추천 질문은 반드시 사용자 발화형으로 작성해라. 예: "특정 제품 기준으로도 설명해줘", "알레르기 기준으로 더 구체적으로 알려줘".
- 추천 질문에 "볼까요", "드릴까요", "물어보실까요", "확인해볼까요", "쉬우실까요"처럼 챗봇이 사용자에게 묻는 표현을 쓰지 마라.
- 추천 질문에는 제공된 문맥 밖의 구체적 수치나 제품 정보를 만들지 마라.
- 쉬운 한국어로 답해라.
- 정보가 부족하면 부족하다고 말해라.
- 제공된 문맥 밖의 구체적 수치나 제품 정보를 만들지 마라.
- “100% 안전”, “치료”, “무조건 괜찮음”, “마음껏 먹어도 됨” 같은 표현은 쓰지 마라.
""".strip()
