"""Local request-lifecycle predicates; these never change profiles."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestLifecycle:
    cancel: bool = False
    new_topic: bool = False
    continue_request: bool = False
    remaining_message: str = ""
    cancel_search: bool = False


_CANCEL_ACTION = r"(?:취소|그만)(?:\s*해\s*(?:줘|주세요|요)?|할게(?:요)?|하(?:고|자))?"
_FOOD_TASK = r"레시피|요리|먹어도|마셔도|섭취해도|원재료|영양|나트륨|당류|단백질|(?:식|채식).*뭐|비교"


def request_lifecycle(message: str) -> RequestLifecycle:
    # Resolve negation before interpreting the action, for every caller.
    negated = r"(?:취소하|그만(?:두|하))지\s*(?:말[고아아요]*|마[요]?|않[가-힣]*)"
    negated_cancel = bool(re.search(negated, message))
    text = re.sub(negated + r"[.!?]*", "", message)
    from app.services.diet_goal_context import product_text_without_goal_edits, is_goal_only_reply
    # Withdrawing '저염 조건' does not cancel the food request or its allergies.
    text = product_text_without_goal_edits(text)
    partial_search = False
    stopped_questions = False
    search_suffix = ""
    clarification_suffix = ""

    def remove_partial_action(match):
        nonlocal partial_search, stopped_questions, search_suffix, clarification_suffix
        target = match[1].replace(" ", "")
        if target == "질문":
            before = text[:match.start()]
            # '그 질문 취소' names the previous task, not a clarification UI.
            if re.search(r"(?:그|이전|아까|원래|지난|레시피|요리)\s*$", before):
                return match[0]
            if not re.search(r"(?:레시피|요리).*(?:계속|만들|추천)|계속해", text) and not re.match(r"질문\s*만", match[0]):
                return match[0]
        if "검색" in target:
            partial_search = True
            search_suffix = text[match.end():].strip()
        else:
            stopped_questions = True
            clarification_suffix = text[match.end():].strip()
        return ""

    # The action's target, not just its verb, determines whether the unfinished
    # food request ends. Stopping questions never resolves an allergy.
    text = re.sub(
        rf"(?<![가-힣])((?:확인\s*)?질문|확인|(?:제품\s*)?검색)(?:만(?:은)?|은|는|을|를|도)?\s*(?:좀\s*)?{_CANCEL_ACTION}[.!;,\s]*",
        remove_partial_action, text,
    )
    cancel = bool(re.search(r"취소|그만", text))
    new_topic = bool(re.search(r"다른\s*(?:질문|얘기|이야기)|새\s*질문", text))
    continuation = negated_cancel or stopped_questions or partial_search or is_goal_only_reply(message) or bool(re.search(r"계속|이어서", message))
    remaining = ""
    if cancel:
        actions = list(re.finditer(_CANCEL_ACTION + r"[.!;,\s]*", text))
        suffix = text[actions[-1].end():].strip() if actions else ""
        # Only an explicit new food question replaces the cancelled task.
        # A dietary clarification or '계속해' is not a new recipe instruction.
        from app.intent.common import OUT_OF_DOMAIN_KEYWORDS
        if re.search(_FOOD_TASK, suffix) or any(word in suffix for word in OUT_OF_DOMAIN_KEYWORDS):
            remaining = suffix
    elif partial_search and re.search(_FOOD_TASK, search_suffix):
        remaining = search_suffix
    elif stopped_questions and re.search(_FOOD_TASK, clarification_suffix):
        remaining = clarification_suffix
    return RequestLifecycle(cancel, new_topic, continuation and not (cancel or new_topic), remaining,
                            partial_search and not (cancel or new_topic))


def request_is_cancelled(message: str) -> bool:
    lifecycle = request_lifecycle(message)
    return lifecycle.cancel or lifecycle.new_topic


def requests_continuation(message: str) -> bool:
    return request_lifecycle(message).continue_request


def cleared_request_state(state):
    from app.core.conversation_context_fields import json_state, carry_task_context
    context = json_state(state or {})
    for key in ("pending_allergy", "pending_user_message", "pending_intent", "pending_product_candidates", "recipe_product", "last_confirmed_product", "recipe_requested_diets", "recipe_receipt", "pending_target_product"):
        context.pop(key, None)
    context["recipe_goals_initialized"] = True
    context = carry_task_context(context, {
        "version": 1, "last_intent": "unknown", "last_task_type": "general_chat",
    })
    return context


def cleared_product_search_state(state):
    """Discard search targets without withdrawing the unfinished conditions."""
    from app.core.conversation_context_fields import json_state
    context = json_state(state or {})
    original = context.get("pending_user_message") or context.get("pending_allergy", {}).get("message", "")
    recipe_pending = context.get("pending_intent") == "recipe_recommend" or bool(re.search(r"레시피|요리", original))
    for key in ("pending_product_candidates", "last_confirmed_product", "recipe_product", "pending_user_message", "pending_intent"):
        context.pop(key, None)
    context.update(last_intent="unknown", last_task_type="general_chat")
    if recipe_pending and original:
        from app.services.recipe import _requested_diets
        context.update(last_intent="missing_product", last_task_type="product_chat",
                       pending_intent="recipe_recommend", pending_user_message=original,
                       recipe_goals_initialized=True,
                       recipe_requested_diets=_requested_diets([], original, [], context))
    return context


def request_state_after_action(lifecycle, state):
    if lifecycle.cancel:
        return cleared_request_state(state)
    if lifecycle.cancel_search:
        return cleared_product_search_state(state)
    return state


def standalone_cancellation_response(message, state=None, *, profiles=None, known_profiles=None):
    """A real cancellation stops generation, including in compound replies."""
    lifecycle = request_lifecycle(message)
    if not (lifecycle.cancel or lifecycle.cancel_search) or lifecycle.remaining_message:
        return None
    # The comparison workflow has its own explicit end acknowledgement.
    from app.services.comparison_language import split_comparison_cancellation
    if split_comparison_cancellation(message)[1]:
        return None
    from app.core.models import ChatResponse
    context = cleared_product_search_state(state) if lifecycle.cancel_search else cleared_request_state(state)
    clarification = ""
    if profiles is not None:
        # Stopping lookup is not permission to skip local condition collection.
        # No catalog or generation call is made by this preparation step.
        from app.services.food_conditions import prepare_food_conditions
        prepared = prepare_food_conditions(
            message, profiles, context, known_profiles,
            resumed_product_message=context.get("pending_user_message") if lifecycle.cancel_search else None,
        )
        context = prepared.state
        if lifecycle.cancel_search and prepared.pending:
            context["pending_allergy"] = prepared.pending
            if prepared.allergy_guard is not None:
                clarification = " " + prepared.allergy_guard.answer
        elif lifecycle.cancel:
            # A whole-request cancellation still ends turn-local allergies.
            # Explicit conversational exclusions are independent of that task.
            context.pop("pending_allergy", None)
        if prepared.ambiguous_diet:
            clarification += " 제외 조건의 적용 대상이나 허용 여부는 아직 확인이 필요해요."
    return ChatResponse(
        intent="unknown", task_type="general_chat", answer_source="rules", pipeline_stage="rules",
        answer=("제품 검색을 중단했어요. 진행 중인 요청의 조건과 새로 말씀하신 조건을 이어서 확인할게요. 다른 제품명이나 바코드를 알려 주세요."
                if lifecycle.cancel_search else "진행 중인 요청을 취소했어요. 새로 궁금한 제품이나 레시피를 알려 주세요.") + clarification,
        risk_level="unknown", conversation_state=context, internal_conversation_state=context,
    )


def refers_only_to_current_product(message: str) -> bool:
    """Bind an explicit demonstrative, with no other target in its request.

    Parse the target separately from request-word order. Unrecognized nouns,
    comparisons and replacement targets fail closed instead of selecting an
    old product during an allergy clarification.
    """
    match = re.search(
        r"(?:^|[.!?]\s*|(?:있는데|없는데|인데)\s*)"
        r"(?:이\s*제품|그\s*제품|이거|그거)(?:으로|로|은|는|을|를)?\s*(.+)$",
        message,
    )
    if not match:
        return False
    words = re.findall(r"[^\s?.!,]+", match[1])
    request_word = re.compile(
        r"(?:먹(?:어도|으면|을까)[가-힣]*|마셔도|섭취해도|"
        r"(?:돼|되|될|괜찮|어때|좋|추천|알려|만들|활용|이용)[가-힣]*|"
        r"(?:레시피|요리|조리법)(?:를|도|는)?|"
        r"뭘|무엇을|어떤|수|있는|먹을|좀|줘(?:요)?|주세요|주시겠어요|부탁해(?:요)?)"
    )
    return bool(words) and all(request_word.fullmatch(word) for word in words)
