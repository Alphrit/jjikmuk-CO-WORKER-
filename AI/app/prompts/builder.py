from typing import List, Optional

from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile
from app.core.nutrition_basis import nutrition_basis_label
from app.core.product_provenance import (
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    label_provenance_limitation_reason,
    label_provenance_supports,
)


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
- 영양성분 설명은 반드시 수치의 기준량을 함께 밝혀라.
- DB에 확인된 수치는 그대로 설명하되 높음·낮음·부담 여부를 새로 해석하지 마라.
- 저염·저당·저칼로리·저지방·고단백은 제품의 명시 표시나 같은 기준량의 제품 비교 없이는 단정하지 마라.
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
        f"알레르기: {_profile_list_summary(profile.resolved_allergies(), profile.allergy_context_provided)}",
        f"식이습관: {_profile_list_summary(profile.resolved_special_diets(), profile.diet_context_provided)}",
        f"비선호 성분: {', '.join(profile.disliked_ingredients) or '미입력/미확인'}",
    ]
    return "\n".join(parts)


def _profile_list_summary(values: List[str], context_provided: bool) -> str:
    if values:
        return ", ".join(values)
    if context_provided:
        return "없음(사용자가 명시적으로 입력)"
    return "미입력/미확인"


def _product_summary(product: Optional[Product]) -> str:
    if not product:
        return "제품 정보 없음"
    if not product.facts_verified:
        return (
            "제품 정보: DB 미검증\n"
            "요청에 포함된 제품명, 원재료, 알레르기, 영양 수치는 "
            "근거로 사용하지 말 것"
        )

    nutrition_bits = []
    for key in ["kcal", "protein", "fat", "carbohydrate", "sugars", "sugar", "sodium"]:
        if key in product.nutrition:
            nutrition_bits.append(f"{key}: {product.nutrition[key]}")

    ingredient_summary = product.rawmtrl_nm or ", ".join(product.ingredients)
    allergy_summary = ", ".join(product.resolved_allergies())
    parts = [
        f"제품명: {product.product_name or '미상'}",
        f"바코드: {product.barcode or '미제공'}",
        f"원재료명: {ingredient_summary or '미확인(정보 미제공)'}",
        (
            f"알레르기 성분: {allergy_summary}"
            if allergy_summary
            else "알레르기 성분: 미확인(빈 값만으로 알레르기 성분이 없다고 단정할 수 없음)"
        ),
        f"영양정보: {', '.join(nutrition_bits) or '미확인(정보 미제공)'}",
        f"영양 수치 기준: {nutrition_basis_label(product)}",
    ]
    return "\n".join(parts)


def _nutrition_interpretation_guard(product: Optional[Product]) -> str:
    if product is None or not product.nutrition:
        return "해석할 제품 영양 수치가 없음. 수치나 해석을 추측하지 말 것."
    return (
        f"{nutrition_basis_label(product)}. 영양 원문 수치는 표시해도 되지만, "
        "자체 임계치로 높음·낮음·적합·부담·주의 구간을 판정하거나 "
        "추론하지 말 것. 규칙 답변의 해석 보류를 반드시 유지할 것."
    )


def _label_lineage_guard(product: Optional[Product]) -> str:
    if product is None:
        return "제품 표시 계보 해당 없음."
    if not product.facts_verified:
        return (
            "저장소 제품 identity 불일치. 요청 제품의 표시 facts를 "
            "근거로 사용하지 말 것."
        )

    provenance = product.label_provenance
    observed_at = (
        provenance.label_observed_at.isoformat()
        if provenance.label_observed_at is not None
        else "미확인"
    )
    composition_traceable = label_provenance_supports(
        product,
        ("ingredients", "allergens"),
    )
    nutrition_traceable = label_provenance_supports(
        product,
        ("nutrition", "nutrition_basis"),
    )
    rules = [
        f"표시 계보 상태: {effective_label_lineage_status(product)}",
        f"표시 freshness 운영 상태: {label_freshness_status(product)}",
        f"수집 snapshot 결속 상태: {content_revision_binding_status(product)}",
        f"표시 관찰일: {observed_at}",
        (
            "content_revision_sha256는 동일한 정규화 snapshot 식별자일 뿐이며 "
            "출처 권위·최신성의 증명이 아님."
        ),
        "'최신', '현재 판매 라벨', '가장 최근 정보'라고 주장하지 말 것.",
    ]
    if not composition_traceable:
        rules.append(
            label_provenance_limitation_reason(
                product,
                ("ingredients", "allergens"),
            )
            + " 원재료·알레르기 판단에 관해 '없다', '포함되지 않았다', "
            "'충돌하지 않는다'는 부재 주장을 하지 말 것. 규칙 결과에 있는 "
            "positive 함유/교차오염 경고는 유지하되 근거 제한을 밝힐 것."
        )
    if not nutrition_traceable:
        rules.append(
            label_provenance_limitation_reason(
                product,
                ("nutrition", "nutrition_basis"),
            )
            + " 영양 원문 수치 외의 임계치, 고/저, 저당/저염/양호/가벼움, "
            "우승 제품, 수치 차이 해석을 하지 말 것."
        )
    return "\n".join(rules)


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
                f"  알레르기: {_profile_list_summary(profile.resolved_allergies(), profile.allergy_context_provided)}",
                f"  식이습관: {_profile_list_summary(profile.resolved_special_diets(), profile.diet_context_provided)}",
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
규칙 기반 판단 결과는 이미 확정된 판단이다. 너의 역할은 판단을 새로 하는 것이 아니라 사용자에게 자연스럽게 읽히도록 문장만 다듬는 것이다.
반드시 아래의 규칙 기반 판단 결과, 사용자 프로필, 제품 정보, RAG 참고 문맥 안에서만 답해야 한다.
규칙 기반 결과, 제품 정보, RAG 참고 문맥에 없는 내용은 추측하지 말고, 확인된 정보가 부족하다고 말해라.
새로운 성분, 수치, 효능, 질병 치료 효과를 만들어내면 안 된다.
의학적 진단처럼 말하지 말고, 제품 정보 기반의 섭취 참고 안내로 답해라.

[사용자 질문]
{message}

[이전 대화]
{_chat_history_summary(chat_history)}

[규칙 기반 결과]
intent: {rule_response.intent}
risk_level: {rule_response.risk_level}
assessment_status: {rule_response.assessment_status}
reasons: {rule_response.reasons}
기존 규칙 답변:
{rule_response.answer}

[사용자 프로필]
{_profile_summary(profile)}

[제품 정보]
{_product_summary(product)}

[영양 기준 안전 규칙]
{_nutrition_interpretation_guard(product)}

[제품 표시 계보 안전 규칙]
{_label_lineage_guard(product)}

[RAG 참고 문맥]
{rag_text}

[intent별 말투 가이드]
{_intent_style_guidance(rule_response.intent)}

[답변 작성 방식]
첫 문장은 사용자의 질문에 대한 자연스러운 결론으로 시작해라.
그다음에는 제품 정보에서 확인된 근거를 자연스럽게 설명해라.
마지막에는 사용자의 알레르기와 식단 조건에 연결해 주의사항을 짧게 안내해라.
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
- assessment_status가 incomplete 또는 unsupported이면 안전하거나 문제가 없다고 바꾸지 말고, 확인하지 못한 기준을 분명히 유지해라.
- 영양 수치 기준이 미확인이거나 1회 제공량이 아니면 수치의 높음·낮음·적합성을 새로 해석하지 마라.
- 프로필의 “미입력/미확인”과 제품의 “미확인”은 “없음”이 아니다. 해당 정보가 없다고 추론하지 마라.
- content revision hash를 최신성이나 출처 권위의 증명으로 표현하지 마라.
- reasons에 있는 성분이나 수치를 반드시 최소 1개 포함해라.
- 원재료명, 알레르기 정보, 영양성분 중 확인된 근거만 사용해라.
- 규칙 기반 결과의 intent, risk_level, reasons를 바꾸거나 약하게 만들지 마라.
- 기존 규칙 답변에 없는 새로운 위험 사유를 추가하지 마라.
- RAG 참고 문맥에 없는 일반 의학/영양 지식을 새로 추가하지 마라.
- 근거가 부족한 항목은 “현재 제공된 정보만으로는 확인하기 어려워요”라고 말해라.
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
규칙 기반 판단 결과는 이미 확정된 판단이다. 너의 역할은 판단을 새로 하는 것이 아니라 사용자에게 자연스럽게 읽히도록 문장만 다듬는 것이다.
반드시 아래의 규칙 기반 판단 결과, 각 프로필 정보, 제품 정보, RAG 참고 문맥 안에서만 답해야 한다.
규칙 기반 결과, 제품 정보, RAG 참고 문맥에 없는 내용은 추측하지 말고, 확인된 정보가 부족하다고 말해라.
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
assessment_status: {rule_response.assessment_status}
reasons: {rule_response.reasons}
기존 규칙 답변:
{rule_response.answer}

[제품 정보]
{_product_summary(product)}

[영양 기준 안전 규칙]
{_nutrition_interpretation_guard(product)}

[제품 표시 계보 안전 규칙]
{_label_lineage_guard(product)}

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
- 프로필의 “미입력/미확인”과 제품의 “미확인”은 “없음”이 아니다. 해당 정보가 없다고 추론하지 마라.
- content revision hash를 최신성이나 출처 권위의 증명으로 표현하지 마라.
- 여러 프로필을 하나로 뭉뚱그려 단정하지 말고 차이를 드러내라.
- 추천 질문은 현재 질문, 활성 프로필, 제품 정보와 관련 있어야 한다.
- 추천 질문은 반드시 사용자 발화형으로 작성해라. 예: "나 프로필 기준으로 다시 알려줘", "다른 가족 프로필 기준으로 확인해줘".
- 추천 질문에 "볼까요", "드릴까요", "물어보실까요", "확인해볼까요", "쉬우실까요"처럼 챗봇이 사용자에게 묻는 표현을 쓰지 마라.
- 규칙 기반 결과의 intent, risk_level, reasons를 바꾸거나 약하게 만들지 마라.
- 영양 수치 기준이 미확인이거나 1회 제공량이 아니면 수치의 높음·낮음·적합성을 새로 해석하지 마라.
- 기존 규칙 답변에 없는 새로운 위험 사유를 추가하지 마라.
- RAG 참고 문맥에 없는 일반 의학/영양 지식을 새로 추가하지 마라.
- 근거가 부족한 항목은 “현재 제공된 정보만으로는 확인하기 어려워요”라고 말해라.
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
RAG 참고 문맥에 없는 기준, 수치, 효능, 질병 관련 설명은 새로 만들지 말고 정보가 부족하다고 말해라.
영양 수치가 나오더라도 1회 제공량·100g/100ml·총 내용량 중 기준이 없으면 높음·낮음·적합성을 해석하지 마라.

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
알레르기나 식단 조건이 불분명하면 필요한 프로필·표시 정보를 추가로 확인해라.
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
- RAG 참고 문맥에 없는 일반 의학/영양 지식을 새로 추가하지 마라.
- 제공된 문맥 밖의 구체적 수치나 제품 정보를 만들지 마라.
- “100% 안전”, “치료”, “무조건 괜찮음”, “마음껏 먹어도 됨” 같은 표현은 쓰지 마라.
""".strip()
