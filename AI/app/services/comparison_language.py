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
    for key in ("comparison_pending_index", "pending_product_candidates", "pending_user_message", "pending_intent"):
        state.pop(key, None)
    state.update(comparison_status="cleared", comparison_products=[], comparison_queries=[])
    return state


_AXIS = r"(?:나트륨|단백질|당류|당분|칼로리|열량|포화지방|지방|탄수화물)"
_AXIS_REPLY = re.compile(
    rf"\s*(?:(?:그럼|그러면)\s*)?({_AXIS})\s*(?:으로|로|은|는|기준으로|기준|이요|요)?"
    r"\s*(?:비교해\s*줘|비교해\s*주세요|비교|볼래|해줘|부탁해(?:요)?|부탁드립니다)?[?.!\s]*"
)


def comparison_axis_reply(message):
    match = _AXIS_REPLY.fullmatch(message)
    return match[1] if match else None


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
