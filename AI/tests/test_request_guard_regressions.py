import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import main as api_main
from app.api.request_guard import (
    DEFAULT_MAX_CHAT_BODY_BYTES,
    DEFAULT_RATE_LIMIT_REQUESTS,
    DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    FixedWindowRateLimiter,
    _client_key,
    chat_rate_limit_enabled,
    chat_rate_limit_requests,
    chat_rate_limit_window_seconds,
    chat_rate_limiter,
    max_chat_body_bytes,
    secure_text_matches,
)


def _chat_env(**overrides):
    values = {
        "FOOD_CHATBOT_API_TOKEN": "",
        "FOOD_CHATBOT_ENABLE_RATE_LIMIT": "false",
        "FOOD_CHATBOT_RATE_LIMIT_REQUESTS": "60",
        "FOOD_CHATBOT_RATE_LIMIT_WINDOW_SECONDS": "60",
        "FOOD_CHATBOT_MAX_REQUEST_BYTES": str(DEFAULT_MAX_CHAT_BODY_BYTES),
        "FOOD_CHATBOT_TRUST_PROXY_HEADERS": "false",
    }
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


def test_chat_api_token_is_optional_but_enforced_when_configured():
    client = TestClient(api_main.app)

    with _chat_env(FOOD_CHATBOT_API_TOKEN="service-secret"):
        missing = client.post("/chat", json={"message": "저염식 기준 알려줘"})
        wrong = client.post(
            "/chat",
            headers={"Authorization": "Bearer wrong-secret"},
            json={"message": "저염식 기준 알려줘"},
        )
        bearer = client.post(
            "/chat",
            headers={"Authorization": "Bearer service-secret"},
            json={"message": "저염식 기준 알려줘"},
        )
        explicit_header = client.post(
            "/chat",
            headers={"X-Food-Chatbot-Token": "service-secret"},
            json={"message": "저염식 기준 알려줘"},
        )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert bearer.status_code == 200
    assert explicit_header.status_code == 200


def test_secret_comparison_handles_utf8_text_without_type_error():
    assert secure_text_matches("보안-토큰", "보안-토큰") is True
    assert secure_text_matches("보안-토큰", "다른-토큰") is False


def test_misconfigured_utf8_chat_secret_rejects_request_without_server_error():
    client = TestClient(api_main.app)
    utf8_token = "보안-토큰-한글-특수값"

    with _chat_env(FOOD_CHATBOT_API_TOKEN=utf8_token):
        rejected = client.post(
            "/chat",
            headers={"X-Food-Chatbot-Token": "ascii-token-does-not-match"},
            json={"message": "저염식 기준 알려줘"},
        )

    assert rejected.status_code == 401


def test_chat_request_body_is_rejected_before_pydantic_parsing_when_too_large():
    client = TestClient(api_main.app)
    with _chat_env(FOOD_CHATBOT_MAX_REQUEST_BYTES="1024"):
        response = client.post("/chat", json={"message": "가" * 600})

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}


def test_chat_rate_limit_returns_retry_after_and_does_not_affect_health():
    client = TestClient(api_main.app)
    chat_rate_limiter.reset()
    try:
        with _chat_env(
            FOOD_CHATBOT_ENABLE_RATE_LIMIT="true",
            FOOD_CHATBOT_RATE_LIMIT_REQUESTS="2",
            FOOD_CHATBOT_RATE_LIMIT_WINDOW_SECONDS="60",
        ):
            first = client.post("/chat", json={"message": "저염식 기준 알려줘"})
            second = client.post("/chat", json={"message": "당류 기준 알려줘"})
            blocked = client.post("/chat", json={"message": "단백질 기준 알려줘"})
            health = client.get("/health")
    finally:
        chat_rate_limiter.reset()

    assert first.status_code == 200
    assert second.status_code == 200
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert health.status_code == 200


def test_invalid_guard_configuration_falls_back_without_disabling_body_limit():
    with _chat_env(
        FOOD_CHATBOT_ENABLE_RATE_LIMIT="sometimes",
        FOOD_CHATBOT_RATE_LIMIT_REQUESTS="0",
        FOOD_CHATBOT_RATE_LIMIT_WINDOW_SECONDS="nan",
        FOOD_CHATBOT_MAX_REQUEST_BYTES="999999999999",
    ):
        assert chat_rate_limit_enabled() is False
        assert chat_rate_limit_requests() == DEFAULT_RATE_LIMIT_REQUESTS
        assert chat_rate_limit_window_seconds() == DEFAULT_RATE_LIMIT_WINDOW_SECONDS
        assert max_chat_body_bytes() == DEFAULT_MAX_CHAT_BODY_BYTES


def test_forwarded_client_address_is_ignored_unless_explicitly_trusted():
    scope = {
        "client": ("10.0.0.5", 54321),
    }
    headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}

    with _chat_env(FOOD_CHATBOT_TRUST_PROXY_HEADERS="false"):
        assert _client_key(scope, headers) == "10.0.0.5"
    with _chat_env(FOOD_CHATBOT_TRUST_PROXY_HEADERS="true"):
        assert _client_key(scope, headers) == "203.0.113.7"


def test_fixed_window_limiter_resets_and_bounds_tracked_clients():
    limiter = FixedWindowRateLimiter(max_keys=2)

    assert limiter.allow("a", limit=1, window_seconds=10, now=0)[0] is True
    assert limiter.allow("a", limit=1, window_seconds=10, now=1)[0] is False
    assert limiter.allow("b", limit=1, window_seconds=10, now=1)[0] is True
    assert limiter.allow("c", limit=1, window_seconds=10, now=2)[0] is True
    assert len(limiter._windows) == 2
    assert limiter.allow("a", limit=1, window_seconds=10, now=11)[0] is True
