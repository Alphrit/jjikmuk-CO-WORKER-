"""Bounded source-product confirmation for an unfinished recipe request."""

import re

from app.core.models import ChatResponse
from app.services.request_language import request_is_cancelled, requests_continuation, refers_only_to_current_product


def pending_recipe_source(state):
    state = state or {}
    if (state.get("last_intent") == "missing_product"
            and state.get("pending_intent") == "recipe_recommend"
            and not state.get("pending_product_candidates")):
        return state.get("pending_user_message")
    return None


def recipe_source_reply(message, state, *, has_identifier=False):
    """Return a task restatement only for a source reply, never a new question."""
    original = pending_recipe_source(state)
    if not original or request_is_cancelled(message):
        return None
    from app.services.product_search import recipe_source_query, PRODUCT_REFERENCE_ONLY_TOKENS, compact_search_text
    from app.services.response_helpers import resolve_profile_diets
    from app.services.diet_goal_context import read_diet_goal_update, is_goal_only_reply
    update = read_diet_goal_update(message)
    previous_goals = (state or {}).get("recipe_requested_diets", resolve_profile_diets(None, original))
    goals = update.apply(previous_goals)
    source = recipe_source_query(message)
    if is_goal_only_reply(message):
        return update.restate(original) if update.touched else original
    if source:
        if compact_search_text(source) in PRODUCT_REFERENCE_ONLY_TOKENS and not has_identifier:
            return update.restate(original) if update.touched else original
        source = "이 제품" if has_identifier else source
        return f"{source}으로 {' '.join(goals)} 레시피 추천해줘"
    if requests_continuation(message) or refers_only_to_current_product(message):
        return update.restate(original) if update.touched else original
    # A bare catalog name/barcode reply, not an independent food question.
    if not re.fullmatch(r"[가-힣A-Za-z0-9%·() /_-]{1,100}[.!]?", message.strip()):
        return None
    if re.search(r"알레르기|알러지|레시피|요리|먹|추천|비교|알려|나트륨|원재료|없이|빼|제외|그만|질문", message):
        return None
    goals = " ".join(goals)
    source = "이 제품" if has_identifier else message.strip(" .!")
    return f"{source}으로 {goals} 레시피 추천해줘"


def missing_recipe_source_response(message, state=None):
    from app.services.recipe import _requested_diets
    from app.services.product_search import recipe_source_query
    previous = state or {}
    # A reconstructed source reply already includes the retained goals and
    # current additions. Persist that latest request, not the first failed one.
    original = message if recipe_source_query(message) else pending_recipe_source(previous) or message
    context = {
        "version": 1, "last_intent": "missing_product", "last_task_type": "product_chat",
        "pending_intent": "recipe_recommend", "pending_user_message": original[:4000],
        "recipe_goals_initialized": True,
        "recipe_requested_diets": _requested_diets([], original, [], previous),
    }
    return ChatResponse(
        intent="missing_product", task_type="product_chat", answer_source="rules", pipeline_stage="rules",
        answer="요청하신 제품을 확인하지 못해 해당 제품을 사용하는 레시피는 아직 만들지 않았어요. 정확한 제품명이나 바코드를 알려 주세요. 제품 없는 일반 레시피를 원하시면 그렇게 말씀해 주세요.",
        risk_level="unknown", assessment_status="incomplete", overall_decision="check_required",
        reasons=["레시피에 사용할 제품 확인 필요"], missing_information=["요청 제품의 정확한 제품명 또는 바코드"],
        conversation_state=context, internal_conversation_state=context,
    )
