"""Resolve every comparison target, including explicit candidate selection."""

from typing import Optional
import re

from app.core.models import ChatResponse, Product

from app.services.product import find_product_candidates_from_message
from app.services.comparison_language import comparison_axis_reply, replacement_targets
from app.services.conversation_router import (
    _resolve_authoritative_product_reference,
    _product_state_reference,
    build_confirmation_if_needed,
)


def _incomplete(message, context, explanation):
    context = {"version": 1, "last_intent": "food_compare", **context}
    response = ChatResponse(
        intent="food_compare", answer=explanation, risk_level="unknown",
        assessment_status="incomplete", missing_information=[explanation],
        conversation_state=context, internal_conversation_state=context,
    )
    return [], response, context, message


def _replacement_index(old, queries, refs):
    normalized = re.sub(r"\s+", "", old)
    ordinals = {"첫번째": 0, "두번째": 1, "세번째": 2, "1번": 0, "2번": 1, "3번": 2}
    if normalized in ordinals and ordinals[normalized] < len(queries):
        return ordinals[normalized]
    matches = [i for i, query in enumerate(queries) if normalized in {
        re.sub(r"\s+", "", query),
        re.sub(r"\s+", "", (refs[i] or {}).get("product_name", "")),
    }]
    return matches[0] if len(matches) == 1 else None


def resolve_comparison(
    message: str, selected_product: Optional[Product], state: Optional[dict],
) -> tuple[list[Product], Optional[ChatResponse], dict, str]:
    from app.services.general_handlers import _message_product_segments

    state = state or {}
    queries = _message_product_segments(message)
    pending = state.get("comparison_pending_index")
    original_message = state.get("pending_user_message") or ""
    resuming = pending is not None and bool(original_message) and message.startswith(original_message)
    replacement = replacement_targets(message)
    axis_only = comparison_axis_reply(message) is not None
    if replacement and not resuming:
        queries = list(state.get("comparison_queries", []))
        refs = list(state.get("comparison_products", []))
        refs = (refs + [None] * len(queries))[:len(queries)]
        index = _replacement_index(replacement[0], queries, refs)
        if index is None:
            return _incomplete(message, {
                "comparison_status": "cleared", "comparison_products": [], "comparison_queries": [],
            }, "어느 비교 제품을 바꿀지 확인하지 못했어요. 새로 비교할 제품 두 개를 알려 주세요.")
        queries[index], refs[index] = replacement[1], None
    elif resuming or (axis_only and state.get("comparison_status") != "cleared"):
        queries = state.get("comparison_queries", [])
        refs = list(state.get("comparison_products", []))
    else:
        refs = [None] * min(len(queries), 3)
        queries = queries[:3]
    if len(queries) < 2:
        return _incomplete(message, {
            "comparison_status": "cleared", "comparison_products": [], "comparison_queries": [],
        }, "비교할 제품 두 개를 알려 주세요. 확인되지 않은 대상을 이전 제품으로 대신하지 않을게요.")
    refs = (refs + [None] * len(queries))[:len(queries)]
    if resuming and selected_product is not None and 0 <= pending < len(refs):
        selected = _product_state_reference(selected_product)
        if any(selected.get("barcode") == item.get("barcode") and selected.get("report_number") == item.get("report_number")
               for item in state.get("pending_product_candidates", []) if isinstance(item, dict)):
            refs[pending] = selected
    # Keep any newly supplied axis appended when resuming a candidate pick.
    original = message
    products = []
    for index, query in enumerate(queries):
        product = _resolve_authoritative_product_reference(refs[index])[0] if refs[index] else None
        if product is None:
            candidates = find_product_candidates_from_message(f"{query} 찾아줘", limit=3)
            confirmation = build_confirmation_if_needed(
                candidates, search_query=query, original_message=original, pending_intent="food_compare",
            )
            if confirmation is not None:
                context = {
                    **confirmation.conversation_state.model_dump(exclude_none=True),
                    "comparison_queries": queries, "comparison_products": refs,
                    "comparison_pending_index": index,
                    "comparison_status": "pending",
                }
                return [], confirmation.model_copy(update={
                    "answer": f"비교 대상 '{query}'을 먼저 확인해 주세요.\n{confirmation.answer}",
                    "conversation_state": context, "internal_conversation_state": context,
                }), context, original
            product = candidates[0] if candidates else None
        if product is None:
            refs[index] = None
            return _incomplete(message, {
                "comparison_status": "unresolved", "comparison_products": refs, "comparison_queries": queries,
            }, f"비교 대상 '{query}'을 DB에서 찾지 못했어요. 정확한 제품명을 다시 알려 주세요.")
        refs[index] = _product_state_reference(product)
        products.append(product)
    context = {"version": 1, "comparison_products": refs, "comparison_queries": queries, "comparison_status": "active"}
    return products, None, context, original
