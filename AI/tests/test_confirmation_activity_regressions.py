from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.models import ChatResponse


def test_product_confirmation_early_response_is_recorded():
    confirmation = ChatResponse(
        intent="product_confirmation",
        answer="제품 후보 중 하나를 선택해 주세요.",
        risk_level="unknown",
        assessment_status="incomplete",
        conversation_state={
            "version": 1,
            "last_intent": "product_confirmation",
            "pending_product_candidates": [
                {
                    "barcode": "8800000000017",
                    "product_name": "후보 제품",
                },
            ],
        },
    )

    with patch.object(
        api_main,
        "resolve_product_context",
        return_value=(None, None, None, confirmation),
    ), patch.object(api_main, "log_chat_activity") as log_activity:
        response = TestClient(api_main.app).post(
            "/chat",
            json={"message": "후보 제품 찾아줘"},
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "product_confirmation"
    log_activity.assert_called_once()
    assert log_activity.call_args.kwargs["intent"] == "product_confirmation"
    assert log_activity.call_args.kwargs["message"] == "후보 제품 찾아줘"
    assert log_activity.call_args.kwargs["risk_level"] == "unknown"
