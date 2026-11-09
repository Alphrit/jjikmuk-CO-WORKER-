"""Final pass over follow-up chips and question wording.

A chip is sent verbatim when tapped, so it must be a question this service can
answer as written: no fill-in templates, no '이 제품' without a product, no
feature the team filter blocks, and not the question just answered.
"""

import re

from app.core.models import ChatResponse

# Instructions shaped like questions ('제품명 기준으로 나트륨 함량 알려줘') ask the
# user to supply something; sent as-is they name no product or ingredient.
_TEMPLATE = re.compile(r"제품명\s*(?:기준|으로)|바코드\s*기준|식별자|특정\s*성분|원재료명\s*기준|같은\s*성분\s*기준")
_DEICTIC = re.compile(r"(?:^|\s)(?:이|그|해당|현재)\s*(?:제품|상품)|^(?:이거|이건|그거|그건)")
_REQUEST_ENDING = re.compile(r"\s*(?:을|를)?\s*(?:알려|설명해|말해|정리해|보여)\s*(?:줘|주세요|줄래)?[.?!]*$")
_SUGAR_FREE_BRIDGE = "자료에서는 당류를 없앴다는 표시를 ‘무설탕’ 대신 ‘무당(제로슈거)’으로 설명해요.\n"


def _request_key(text: str) -> str:
    return re.sub(r"\s+", "", _REQUEST_ENDING.sub("", text.strip()))


def tidy_followups(response: ChatResponse, message: str, *, team_mode: bool) -> ChatResponse:
    from app.services.team_catalog import STAGE_C_QUESTION
    has_product = response.current_product is not None
    asked = _request_key(message)
    questions = [
        question for question in response.recommended_questions
        if not _TEMPLATE.search(question)
        and (has_product or not _DEICTIC.search(question))
        and not (team_mode and STAGE_C_QUESTION.search(question))
        and _request_key(question) != asked
    ]
    answer = response.answer
    # The reviewed source names the claim '무당'; tie it to the user's word.
    if "무설탕" in message and "무설탕" not in answer and "무당" in answer and response.answer_source in {"rag", "rules"}:
        answer = _SUGAR_FREE_BRIDGE + answer
    if questions == response.recommended_questions and answer == response.answer:
        return response
    return response.model_copy(update={"recommended_questions": questions, "answer": answer})
