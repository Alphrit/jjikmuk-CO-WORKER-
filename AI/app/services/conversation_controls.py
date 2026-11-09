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


# Marks replies that carry no product fact or suitability claim, so bounded
# deployment modes can keep them instead of substituting a product prompt.
CONVERSATION_CONTROL_REASON = "conversation_control"

_HELP_ANSWER = "제품명이나 바코드와 함께 질문해 주세요. ON인 프로필의 알레르기·식이조건 확인, 원재료·영양성분 조회, 두 제품 비교, 대체 상품·레시피 추천을 도와드려요. 식품 표시의 뜻도 설명해요. 후보가 나오면 ‘두 번째’처럼 선택할 수 있어요."
_HELP_QUESTIONS = ["무설탕과 무가당의 차이를 알려줘", "저염 레시피 추천해줘"]

# Whole-message small talk only. A greeting followed by a question ('안녕, 새우깡
# 먹어도 돼?') or a product that starts with a greeting word ('안녕달 손두부')
# never matches, so it still reaches the normal product path.
_SMALL_TALK_TAIL = r"[\s~!.?？,…^ㅎㅋㅠㅜ]*"
_GREETING = (
    r"(?:안녕(?:하세요|하십니까|하신가요)?|하이|헬로|hi|hello|hey|반가워(?:요)?|반갑습니다"
    r"|처음\s*뵙겠습니다|좋은\s*(?:아침|하루)(?:이에요|입니다)?)"
)
_SMALL_TALK = {
    "greeting": re.compile(rf"(?:{_GREETING}[\s,!.~]*){{1,3}}{_SMALL_TALK_TAIL}", re.I),
    "thanks": re.compile(
        rf"(?:고마워(?:요)?|고맙습니다|감사(?:합니다|해요|해)?|땡큐|thanks?|thank\s*you|ㄱㅅ|ㄳ){_SMALL_TALK_TAIL}", re.I,
    ),
    "farewell": re.compile(
        rf"(?:잘\s*(?:가|있어)(?:요)?|안녕히\s*(?:계세요|가세요)|바이(?:바이)?|bye|수고(?:했어(?:요)?|하셨어요|하세요)){_SMALL_TALK_TAIL}",
        re.I,
    ),
    "capability": re.compile(
        r"(?:(?:넌|너는|너|당신은)\s*)?(?:"
        r"(?:뭘|무엇을|뭐)\s*(?:할\s*수\s*있어|해\s*줄\s*수\s*있어|도와\s*줄\s*수\s*있어|하는\s*(?:챗봇|봇|앱)이야)(?:요)?"
        r"|(?:누구|뭐)(?:야|예요|에요|니)|무슨\s*(?:일|기능)(?:을)?\s*(?:해|할\s*수\s*있어)(?:요)?"
        rf"){_SMALL_TALK_TAIL}",
        re.I,
    ),
    "filler": re.compile(r"(?:[ㅋㅎ]{2,}|[ㅠㅜ]{2,})[\sㅋㅎㅠㅜ~!.?]*"),
}
_SMALL_TALK_ANSWERS = {
    "greeting": "안녕하세요! 찍먹 챗봇이에요. 제품명이나 바코드로 원재료·알레르기·영양성분을 확인하고, ON인 프로필의 알레르기·식이조건에 맞는지 안내해 드려요. 식품 표시의 뜻도 설명해요. 예를 들어 ‘새우깡 성분 알려줘’처럼 물어봐 주세요.",
    "thanks": "천만에요. 더 궁금한 제품이나 성분이 있으면 언제든 물어봐 주세요.",
    "farewell": "다음에 또 찾아 주세요. 드시기 전에는 포장지의 원재료·알레르기 표시도 한 번 더 확인해 주세요.",
    "capability": _HELP_ANSWER,
    "filler": "궁금한 제품명이나 성분을 알려 주시면 확인해 드릴게요. 예를 들어 ‘새우깡 성분 알려줘’처럼 물어봐 주세요.",
}


# A greeting/thanks before a real question. The separator is required, so a
# product such as '안녕달 손두부' or '하이츄' keeps its full name.
_LEADING_SMALL_TALK = re.compile(
    rf"\s*(?:(?:{_GREETING}|고마워(?:요)?|고맙습니다|감사(?:합니다|해요)?)[\s,!.~?ㅎㅋ]+)+(?:(?:근데|그런데|혹시|저기)\s+)?",
    re.I,
)


def strip_leading_small_talk(message):
    if not message or small_talk_kind(message) is not None:
        return message
    match = _LEADING_SMALL_TALK.match(message)
    rest = message[match.end():] if match else ""
    return rest if rest.strip() else message


def small_talk_kind(message):
    from app.intent.request_semantics import command_text
    text = command_text(message or "")
    if not text:
        return None
    return next((kind for kind, pattern in _SMALL_TALK.items() if pattern.fullmatch(text)), None)


def _control_reply(answer, state, *, recommended_questions=None):
    return ChatResponse(
        intent="unknown", risk_level="unknown", answer=answer,
        recommended_questions=recommended_questions or [],
        fallback_reason=CONVERSATION_CONTROL_REASON,
        conversation_state=state or {}, internal_conversation_state=state or {},
    )


def conversation_control_response(message, state=None):
    if is_context_reset(message):
        context = {"version": 1, "last_intent": "unknown", "last_task_type": "general_chat"}
        return _control_reply("이전 제품·비교·레시피 맥락을 비웠어요. 현재 ON인 프로필 조건은 유지해요. 새 제품명이나 질문을 알려 주세요.", context)
    from app.intent.request_semantics import command_text
    if re.fullmatch(r"(?:(?:이|이전|앞|지금까지)(?:의)?\s*)?(?:대화|채팅)(?:\s*기록)?(?:를|을|는)?\s*(?:모두|전부|다)?\s*(?:(?:지워|삭제해)줘|삭제하고\s*싶어(?:요)?)", command_text(message)):
        return _control_reply("저장된 대화 삭제는 채팅 목록의 삭제 기능에서 진행해 주세요. 이어지는 질문의 맥락만 비우려면 ‘대화 초기화해줘’라고 말씀해 주세요.", state)
    if re.fullmatch(r"\s*(?:어떤\s*질문을\s*할\s*수\s*있어|뭘\s*물어볼\s*수\s*있어|사용법(?:을)?\s*(?:짧게\s*)?알려\s*줘|도움말)[요?？.!\s]*", message):
        return _control_reply(_HELP_ANSWER, state, recommended_questions=_HELP_QUESTIONS)
    kind = small_talk_kind(message)
    if kind is not None:
        # Keep the bounded state: a greeting mid-conversation must not drop the
        # product the person already selected.
        return _control_reply(
            _SMALL_TALK_ANSWERS[kind], state,
            recommended_questions=_HELP_QUESTIONS if kind in {"greeting", "capability", "filler"} else None,
        )
    return None
