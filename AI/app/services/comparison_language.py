"""Small, explicit continuation grammar; does not infer new product identities."""

import re


def split_comparison_cancellation(message):
    match = re.match(
        r"\s*비교(?:는|를)?\s*(?:그만(?:하(?:고|자)|해(?:줘|요)?|하고)?|"
        r"중단(?:하고|해(?:줘|요)?)|종료(?:하고|해(?:줘|요)?))\s*[,.;!]?\s*",
        message,
    )
    return (message[match.end():].strip(), True) if match else (message, False)


def clear_comparison_context(state):
    state = dict(state or {})
    for key in ("comparison_pending_index", "pending_product_candidates", "pending_user_message", "pending_intent", "comparison_axes"):
        state.pop(key, None)
    state.update(comparison_status="cleared", comparison_products=[], comparison_queries=[])
    return state


_AXIS = r"(?:나트륨|단백질|당류|당분|칼로리|열량|포화지방|지방|탄수화물|콜레스테롤|알레르기\s*성분|알레르기)"
_AXIS_LIST = rf"{_AXIS}(?:\s*(?:과|와|및|하고|이랑|랑|,|/|·)\s*{_AXIS}){{0,7}}"
_AXIS_END = (
    r"\s*(?:(?:수치|함량)\s*)?(?:으로|로|은|는|을|를|도|만|까지|기준으로(?:도)?|기준|이요|요)?\s*(?:포함해서)?"
    r"\s*(?:(?:둘\s*다|모두|각각|함께|같이|다시)\s*)*"
    r"(?:비교해\s*줘|비교해\s*주세요|비교\s*부탁해(?:요)?|비교할래|비교|볼래|봐줘|해줘|알려줘|보여줘|부탁해(?:요)?|부탁드립니다)?[?.!\s]*"
)
_AXIS_REPLY = re.compile(
    rf"\s*(?:(?:그럼|그러면|이제)\s*)?(?:(?:(?:아까|이전|방금)\s*)?비교한\s*(?:두\s*(?:개|제품)|제품들)(?:의)?\s*|(?:그\s*)?둘(?:의|을|은|도)?\s*)?({_AXIS_LIST})" + _AXIS_END
)


def comparison_axis_suffix(message):
    return re.search(rf"(?<![\w])({_AXIS_LIST})" + _AXIS_END + r"$", message)


def comparison_axis_names(message):
    match = comparison_axis_suffix(message)
    return re.findall(_AXIS, match[1]) if match else []


def comparison_axis_reply(message):
    from app.intent.request_semantics import command_text
    text = command_text(message)
    match = _AXIS_REPLY.fullmatch(text)
    if match:
        return match[1]
    # Relative predicates name an axis and reuse the established pair. Require
    # only reference words around it, so an actual new product is never lost.
    relative = re.fullmatch(
        rf"(?:(?:그럼|그러면|이번엔|이번에는|이제)\s*)?"
        rf"(?:(?:둘|두\s*(?:개|제품))\s*중(?:에)?\s*)?"
        rf"(?:짠맛\s*말고\s*)?({_AXIS})(?:이|가)?\s*(?:수치가\s*)?(?:더\s*)?"
        r"(?:작은|적은|많은|높은|낮은)\s*(?:건|것|쪽)(?:이|은)?\s*(?:뭐야|궁금해|알려줘)[?？.!\s]*", text,
    )
    return relative[1] if relative else None


def comparison_relative_reply(message):
    return bool(re.fullmatch(
        rf"\s*(?:(?:그럼|그러면)\s*)?(?:(?:둘|두\s*(?:개|제품))\s*중\s*)?(?:뭐|어느\s*(?:것|제품|쪽))(?:가|이)?\s*(?:{_AXIS}(?:이|가)?\s*)?(?:더\s*)?(?:낮아|높아|적어|많아|낮을까|높을까|적을까|많을까)(?:요)?[?？.!\s]*",
        message,
    ))


def replacement_targets(message):
    """Return an explicit old/new name, never infer an omitted target."""
    match = re.fullmatch(
        r"\s*(.+?)(?:은|는|을|를)?\s*(?:빼고|대신|말고)\s*"
        r"(.+?)(?:와|과|랑|하고|으로|로)\s*비교(?:해\s*줘|해\s*주세요)?[.!?\s]*",
        message,
    )
    return (match[1].strip(), match[2].strip()) if match else None


def comparison_pick_parts(message, state):
    """Split '첫 번째, 나트륨으로 비교해줘', not '첫 번째와 두 번째 비교'."""
    if (state or {}).get("pending_intent") != "food_compare":
        return message, None
    match = re.fullmatch(
        r"\s*((?:첫|두|세)\s*번째|[123]\s*번(?:째)?)\s*[,，]?\s*(.+)", message,
    )
    axis = comparison_axis_reply(match[2]) if match else None
    return (match[1], axis) if axis else (message, None)
