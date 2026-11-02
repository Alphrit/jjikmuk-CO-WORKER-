"""Explicit help/reset controls, without editing profiles or saved messages."""

import re

from app.core.models import ChatResponse


def is_context_reset(message):
    from app.intent.request_semantics import command_text
    message = command_text(message)
    if re.search(r"하지|말아|말고|않|방법|어떻게", message):
        return False
    if re.fullmatch(r"(?:이\s*)?(?:대화|채팅)(?:\s*내용)?(?:를|을|는)?\s*초기화해줘", message):
        return True
    if re.fullmatch(r"앞서\s*(?:나눈|한)\s*(?:얘기|이야기|대화)(?:는|를)?\s*잊어줘", message):
        return True
    if re.fullmatch(r"(?:주제|대화)(?:를|는)?\s*새로\s*시작하고\s*싶어(?:요)?", message):
        return True
    if re.fullmatch(r"(?:(?:앞|이전|지금까지|여태)(?:의|\s*한)?\s*)?대화(?:를|는)?\s*(?:모두|전부|다)?\s*초기화해줘", message):
        return True
    if re.fullmatch(r"(?:앞|이전|지금까지)(?:의|에)?\s*(?:얘기|이야기|대화)(?:는|를)?\s*(?:다|모두|전부)?\s*잊어줘", message):
        return True
    if re.fullmatch(r"새\s*대화(?:로|를)?\s*시작(?:하자|해줘)", message):
        return True
    if re.fullmatch(r"\s*(?:대화(?:를)?\s*초기화해\s*줘|처음부터\s*다시\s*(?:할게|시작해\s*줘|시작하자))[.!?\s]*", message):
        return True
    return bool(re.fullmatch(
        r"\s*(?:이전|앞|지금까지)(?:의)?\s*(?:얘기|이야기|대화)(?:는|를)?\s*"
        r"(?:잊고|지우고)\s*(?:처음부터|새로)\s*(?:시작하자|시작해\s*줘)[.!?\s]*", message,
    ))


def history_after_reset(history):
    history = list(history or [])
    for index in range(len(history) - 1, -1, -1):
        if history[index].role == "user" and is_context_reset(history[index].content):
            return history[index + 1:]
    return history


def conversation_control_response(message, state=None):
    if is_context_reset(message):
        context = {"version": 1, "last_intent": "unknown", "last_task_type": "general_chat"}
        return ChatResponse(
            intent="unknown", risk_level="unknown", answer="이전 제품·비교·레시피 맥락을 비웠어요. 현재 ON인 프로필 조건은 유지해요. 새 제품명이나 질문을 알려 주세요.",
            conversation_state=context, internal_conversation_state=context,
        )
    from app.intent.request_semantics import command_text
    if re.fullmatch(r"(?:(?:이|이전|앞|지금까지)(?:의)?\s*)?(?:대화|채팅)(?:\s*기록)?(?:를|을|는)?\s*(?:모두|전부|다)?\s*(?:(?:지워|삭제해)줘|삭제하고\s*싶어(?:요)?)", command_text(message)):
        return ChatResponse(
            intent="unknown", risk_level="unknown",
            answer="저장된 대화 삭제는 채팅 목록의 삭제 기능에서 진행해 주세요. 이어지는 질문의 맥락만 비우려면 ‘대화 초기화해줘’라고 말씀해 주세요.",
            conversation_state=state or {}, internal_conversation_state=state or {},
        )
    if re.fullmatch(r"\s*(?:어떤\s*질문을\s*할\s*수\s*있어|뭘\s*물어볼\s*수\s*있어|사용법(?:을)?\s*(?:짧게\s*)?알려\s*줘|도움말)[요?？.!\s]*", message):
        return ChatResponse(
            intent="unknown", risk_level="unknown", answer="제품명이나 바코드와 함께 질문해 주세요. ON인 프로필의 알레르기·식이조건 확인, 원재료·영양성분 조회, 두 제품 비교, 대체 상품·레시피 추천을 도와드려요. 식품 표시의 뜻도 설명해요. 후보가 나오면 ‘두 번째’처럼 선택할 수 있어요.",
            recommended_questions=["무설탕과 무가당의 차이를 알려줘", "저염 레시피 추천해줘"],
            conversation_state=state or {}, internal_conversation_state=state or {},
        )
    return None
