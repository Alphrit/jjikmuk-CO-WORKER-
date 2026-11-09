"""Every reply that leaves a product choice open must be read as open by the history guard.

The guard only sees client-supplied text, so producer wording and guard wording
must stay one set (app/services/candidate_prompts.py).
"""

import pytest

from app.core.models import ChatHistoryItem, ChatResponse, Product
from app.services import conversation_router as router
from app.services.team_catalog import restrict_team_catalog_response


def _products():
    return [Product(product_name="새우깡", barcode="8801043035989", report_number="19760342001184"),
            Product(product_name="쌀새우깡", barcode="8801043036054", report_number="19760342001338")]


def _open_choice_replies():
    products = _products()
    yield "multiple_matches", router.build_product_confirmation_response(products, "새우깡", "multiple_matches").answer
    yield "low_confidence", router.build_product_confirmation_response(products, "새우깡", "low_confidence").answer
    yield "pending", router._build_pending_candidate_response(products, comparison_requested=False).answer
    yield "pending_compare", router._build_pending_candidate_response(products, comparison_requested=True).answer
    yield "missing_named", router._missing_named_product_response("구름맛젤리", {}).answer
    confirmation = router.build_product_confirmation_response(products, "새우깡", "multiple_matches")
    yield "team_rewrite", restrict_team_catalog_response(confirmation).answer


def _history(*answers):
    items = []
    for answer in answers:
        items += [ChatHistoryItem(role="user", content="새우깡 먹어도 돼?"), ChatHistoryItem(role="assistant", content=answer)]
    return items


@pytest.mark.parametrize("name, answer", list(_open_choice_replies()), ids=[name for name, _ in _open_choice_replies()])
def test_open_choice_reply_blocks_history_guessing(name, answer):
    assert router._has_unresolved_product_confirmation(_history(answer)), answer


def test_confirmed_choice_reopens_history_lookup():
    open_reply = router.build_product_confirmation_response(_products(), "새우깡", "multiple_matches").answer
    confirmed = router._build_candidate_selected_response(_products()[1]).answer
    assert not router._has_unresolved_product_confirmation(_history(open_reply, confirmed))


def test_plain_product_answer_does_not_block():
    assert not router._has_unresolved_product_confirmation(_history("쌀새우깡 기준으로 확인했어요."))
