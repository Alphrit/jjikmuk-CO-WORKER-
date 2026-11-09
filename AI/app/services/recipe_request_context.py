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


_VEGAN_REQUEST = re.compile(r"비건|vegan|완전\s*채식", re.I)
_NAMED_DISH_REQUEST = re.compile(
    r"([가-힣A-Za-z][가-힣A-Za-z0-9 ]{0,20}?)\s*(?:을|를)?\s*"
    r"(?:만드는\s*(?:법|방법)|만들기|끓이는\s*(?:법|방법)|조리\s*방법)"
)
_GENERIC_DISH_WORDS = {"간단한", "레시피", "요리", "음식", "반찬", "한끼", "일반", "다른", "새로운", "건강한", "쉬운", "그거", "이거"}


def requested_dish_name(message):
    """The dish in '김치찌개 만드는 법', or None for a generic/diet-only request."""
    match = _NAMED_DISH_REQUEST.search(message or "")
    if not match:
        return None
    from app.services.product_search import compact_search_text
    from app.data.dictionaries import SPECIAL_DIET_KEYWORDS
    dish = match[1].strip()
    words = [compact_search_text(word) for word in dish.split()]
    generic = _GENERIC_DISH_WORDS | {
        compact_search_text(term) for name, aliases in SPECIAL_DIET_KEYWORDS.items() for term in (name, *aliases)
    }
    if not words or all(word in generic for word in words):
        return None
    return dish


def matches_dish(dish, title):
    from app.services.product_search import compact_search_text
    return compact_search_text(dish) in compact_search_text(title)


def unsupported_recipe_request(message):
    """(answer, reason) for a productless recipe request the bounded generator
    cannot honor. Returning another dish would answer a different question."""
    if _VEGAN_REQUEST.search(message or ""):
        return (
            "비건 조건을 확인하는 레시피는 아직 지원하지 않아요. 채식으로 바꿔 만들면 달걀·유제품이 들어갈 수 있어 "
            "요청과 달라져요. 지금은 채식·저염·저당·글루텐프리·저칼로리·저지방·고단백 조건의 레시피만 만들 수 있어요.",
            "지원하지 않는 식단 조건: 비건",
        )
    dish = requested_dish_name(message)
    if dish is None:
        return None
    from app.services.reviewed_recipes import reviewed_recipe_variants
    if any(matches_dish(dish, variant["title"]) for variant in reviewed_recipe_variants()):
        return None
    return (
        f"요청하신 ‘{dish[:30]}’ 조리법은 검토된 레시피 자료에 없어 안내하기 어려워요. 다른 요리로 바꿔 드리면 "
        "요청과 다른 레시피가 되므로 만들지 않았어요. 검토된 재료로 만든 간단한 레시피를 원하시면 "
        "‘간단한 레시피 추천해줘’라고 말씀해 주세요.",
        f"검토된 레시피에 없는 요리: {dish[:30]}",
    )


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
    source = recipe_source_query(original)
    # '김치찌개 레시피' may name a dish rather than a product: say both what was
    # not found and that named-dish recipes are not available, instead of only
    # asking for a barcode.
    answer = (
        (f"‘{source[:40]}’ 상품을 DB에서 확인하지 못해 " if source else "요청하신 제품을 확인하지 못해 ")
        + "해당 상품을 사용하는 레시피는 만들지 않았어요. "
        + "이 상품으로 만드는 레시피라면 정확한 제품명이나 바코드를 알려 주세요. "
        + "특정 요리의 조리법은 검토된 레시피 자료에 있는 요리만 안내할 수 있어 지금은 제공하기 어려워요. "
        + "제품 없는 간단한 레시피를 원하시면 그렇게 말씀해 주세요."
    )
    return ChatResponse(
        intent="missing_product", task_type="product_chat", answer_source="rules", pipeline_stage="rules",
        answer=answer,
        risk_level="unknown", assessment_status="incomplete", overall_decision="check_required",
        reasons=["레시피에 사용할 제품 확인 필요"], missing_information=["요청 제품의 정확한 제품명 또는 바코드"],
        conversation_state=context, internal_conversation_state=context,
    )
