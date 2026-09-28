from unittest.mock import patch

import pytest

from app.api import main as api_main


API_TOKEN = "api-" + "a" * 32
IDENTITY_SECRET = "identity-" + "i" * 32
CONSENT_SECRET = "consent-" + "c" * 32


def _production_env(monkeypatch, **overrides):
    values = {
        "FOOD_CHATBOT_ENV": "production",
        "FOOD_CHATBOT_DB_PROVIDER": "mysql",
        "FOOD_CHATBOT_DB_PATH": "",
        "FOOD_CHATBOT_MYSQL_SSL_CA": "/etc/ssl/mysql-ca.pem",
        "FOOD_CHATBOT_MYSQL_SSL_CERT": "",
        "FOOD_CHATBOT_MYSQL_SSL_KEY": "",
        "FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS": "180",
        "FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES": (
            "manufacturer_label:manufacturer-a"
        ),
        "FOOD_CHATBOT_API_TOKEN": API_TOKEN,
        "FOOD_CHATBOT_ENABLE_RATE_LIMIT": "true",
        "FOOD_CHATBOT_BEHIND_TRUSTED_GATEWAY": "false",
        "FOOD_CHATBOT_TRUST_PROXY_HEADERS": "false",
        "FOOD_CHATBOT_SHARED_REPLAY_PROTECTION": "true",
        "FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR": "false",
        "FOOD_CHATBOT_STORE_RAW_MESSAGES": "false",
        "FOOD_CHATBOT_ENABLE_CONVERSATIONS": "false",
        "FOOD_CHATBOT_USER_IDENTITY_SECRET": IDENTITY_SECRET,
        "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": "false",
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET": "",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _codes():
    return api_main._deployment_boundary_status()["error_codes"]


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_environment_does_not_require_production_boundaries(
    monkeypatch, environment
):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_ENV=environment,
        FOOD_CHATBOT_DB_PROVIDER="memory",
        FOOD_CHATBOT_API_TOKEN="",
        FOOD_CHATBOT_ENABLE_RATE_LIMIT="false",
        FOOD_CHATBOT_SHARED_REPLAY_PROTECTION="false",
    )

    assert api_main._deployment_boundary_status() == {
        "environment": environment,
        "valid": True,
        "error_codes": [],
    }


@pytest.mark.parametrize("environment", ["", "staging", "prod"])
def test_unknown_deployment_environment_fails_closed(monkeypatch, environment):
    _production_env(monkeypatch, FOOD_CHATBOT_ENV=environment)

    assert api_main._deployment_boundary_status() == {
        "environment": "invalid",
        "valid": False,
        "error_codes": ["invalid_environment"],
    }


def test_minimal_production_boundary_can_be_valid(monkeypatch):
    _production_env(monkeypatch)

    assert api_main._deployment_boundary_status() == {
        "environment": "production",
        "valid": True,
        "error_codes": [],
    }


@pytest.mark.parametrize("provider", ["memory", "sqlite", "unconfigured"])
def test_production_requires_persistent_mysql(monkeypatch, provider):
    _production_env(monkeypatch, FOOD_CHATBOT_DB_PROVIDER=provider)

    assert "production_persistent_mysql_required" in _codes()


def test_production_mysql_requires_tls_ca_and_valid_client_pair(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_MYSQL_SSL_CA="",
        FOOD_CHATBOT_MYSQL_SSL_CERT="client.pem",
        FOOD_CHATBOT_MYSQL_SSL_KEY="",
    )

    codes = _codes()
    assert "production_mysql_tls_ca_missing" in codes
    assert "production_mysql_tls_client_certificate_pair_invalid" in codes


def test_production_rejects_raw_chat_storage_and_memory_fallback(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_STORE_RAW_MESSAGES="true",
        FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR="true",
    )

    codes = _codes()
    assert "production_raw_message_storage_forbidden" in codes
    assert "production_memory_fallback_forbidden" in codes


def test_production_requires_header_safe_service_token(monkeypatch):
    _production_env(monkeypatch, FOOD_CHATBOT_API_TOKEN="short")
    assert "production_api_token_too_short" in _codes()

    _production_env(monkeypatch, FOOD_CHATBOT_API_TOKEN="가" * 32)
    assert "production_api_token_not_header_safe" in _codes()


def test_conversations_require_identity_and_shared_replay_boundary(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_ENABLE_CONVERSATIONS="true",
        FOOD_CHATBOT_USER_IDENTITY_SECRET="",
        FOOD_CHATBOT_SHARED_REPLAY_PROTECTION="false",
    )

    codes = _codes()
    assert "production_conversation_identity_not_configured" in codes
    assert "production_shared_replay_protection_missing" in codes


def test_rate_limit_or_trusted_gateway_is_required(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_ENABLE_RATE_LIMIT="false",
        FOOD_CHATBOT_BEHIND_TRUSTED_GATEWAY="false",
    )

    assert "production_request_abuse_boundary_missing" in _codes()


def test_proxy_headers_require_an_asserted_trusted_gateway(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_TRUST_PROXY_HEADERS="true",
        FOOD_CHATBOT_BEHIND_TRUSTED_GATEWAY="false",
    )

    assert "production_proxy_header_trust_requires_gateway" in _codes()


def test_external_ai_requires_consent_openai_and_allowed_endpoints(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_ENABLE_EXTERNAL_AI="true",
        FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET="",
        OPENAI_API_KEY="configured",
    )
    with patch.object(
        api_main,
        "get_llm_endpoint_policy_status",
        return_value={"endpoint_allowed": False},
    ), patch.object(
        api_main,
        "get_rag_embedding_endpoint_policy_status",
        return_value={"endpoint_allowed": False},
    ):
        codes = _codes()

    assert "production_external_ai_consent_not_configured" in codes
    assert "production_external_ai_generation_endpoint_invalid" in codes
    assert "production_external_ai_embedding_endpoint_invalid" in codes


def test_external_ai_rejects_non_openai_provider(monkeypatch):
    _production_env(
        monkeypatch,
        FOOD_CHATBOT_ENABLE_EXTERNAL_AI="true",
        FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET=CONSENT_SECRET,
        LLM_PROVIDER="custom",
    )

    assert "production_external_ai_provider_invalid" in _codes()
