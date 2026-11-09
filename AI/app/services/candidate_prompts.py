"""Wording that marks an open product choice in client-supplied history.

The app sends history as plain text and the backend relays no conversation
state, so the only trace of a pending candidate list is the assistant's own
sentence. Every reply that leaves a choice open builds its text from these
phrases, and the history guard reads exactly these. Editing a reply without
going through this module silently disabled the guard once (team wording,
2026-11-09); tests now tie every producer to the guard.
"""

CHOOSE_FROM_CANDIDATES = "아래 후보 중 맞는 제품을 선택해 주세요"
TEAM_CHOOSE_FROM_CANDIDATES = "검색된 상품 중 질문하려는 제품을 선택해 주세요"
NONE_SELECTED_YET = "아직 어느 제품도 선택하지 않았"
NOT_CONFIRMED_YET = "아직 한 제품을 확정하지 않았어요"
NEW_PRODUCT_NOT_FOUND = "요청하신 새 제품을 확인하지 못했어요"
SELECTION_CONFIRMED = "으로 선택했어요"

_OPEN_CHOICE = (CHOOSE_FROM_CANDIDATES, TEAM_CHOOSE_FROM_CANDIDATES, NONE_SELECTED_YET,
                NOT_CONFIRMED_YET, NEW_PRODUCT_NOT_FOUND)


def leaves_choice_open(text: str) -> bool:
    return any(phrase in text for phrase in _OPEN_CHOICE)


def confirms_choice(text: str) -> bool:
    return SELECTION_CONFIRMED in text
