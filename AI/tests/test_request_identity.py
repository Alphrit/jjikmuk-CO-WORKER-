import pytest

from app.infrastructure.request_identity import (
    conversation_identity_boundary_status,
    reset_conversation_identity_nonce_registry,
    sign_conversation_identity,
    verify_conversation_identity,
)

SECRET = "identity-" + "i" * 32


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", SECRET)
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", "api-" + "a" * 32)
    monkeypatch.setenv("FOOD_CHATBOT_ADMIN_TOKEN", "admin-" + "b" * 32)
    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", "consent-" + "c" * 32)
    monkeypatch.setenv("FOOD_CHATBOT_IDENTITY_MAX_AGE_SECONDS", "300")
    reset_conversation_identity_nonce_registry()
    yield
    reset_conversation_identity_nonce_registry()


def _proof(**changes):
    values = {
        "timestamp": 2_000_000_000,
        "method": "POST",
        "path": "/conversations/abc/messages",
        "user_id": 42,
        "nonce": "nonce-0123456789ab",
        "body": b'{"message":"exact bytes"}',
        "purpose": "conversation",
    }
    values.update(changes)
    signature = sign_conversation_identity(**values)
    return values, signature


def test_exact_proof_verifies_once_then_is_replay():
    values, signature = _proof()
    first = verify_conversation_identity(**values, signature=signature, now=2_000_000_001)
    second = verify_conversation_identity(**values, signature=signature, now=2_000_000_001)
    assert first.verified and first.user_id == 42 and first.reason == "verified"
    assert not second.verified and second.reason == "replay"


@pytest.mark.parametrize("field,value", [
    ("body", b'{"message":"changed"}'),
    ("method", "GET"),
    ("path", "/conversations/other/messages"),
    ("user_id", 43),
    ("nonce", "nonce-xxxxxxxxxxxx"),
    ("purpose", "conversation.recheck"),
])
def test_any_signed_dimension_change_fails(field, value):
    values, signature = _proof()
    values[field] = value
    result = verify_conversation_identity(**values, signature=signature, now=2_000_000_001)
    assert not result.verified and result.reason == "invalid_signature"


def test_invalid_signature_does_not_consume_nonce():
    values, signature = _proof()
    invalid = verify_conversation_identity(**values, signature="0" * 64, now=2_000_000_001)
    valid = verify_conversation_identity(**values, signature=signature, now=2_000_000_001)
    assert invalid.reason == "invalid_signature"
    assert valid.verified


def test_strict_timestamp_window_and_future_skew():
    values, signature = _proof(timestamp=1_999_999_699)
    assert verify_conversation_identity(**values, signature=signature, now=2_000_000_000).reason == "timestamp_expired"
    values, signature = _proof(timestamp=2_000_000_006, nonce="nonce-future-123456")
    assert verify_conversation_identity(**values, signature=signature, now=2_000_000_000).reason == "timestamp_in_future"


def test_signature_must_be_lowercase_hex_and_nonce_is_visible_bounded():
    values, signature = _proof()
    assert verify_conversation_identity(**values, signature=signature.upper(), now=2_000_000_001).reason == "invalid_signature"
    with pytest.raises(ValueError):
        _proof(nonce="short")
    with pytest.raises(ValueError):
        _proof(nonce="a" * 129)
    with pytest.raises(ValueError):
        _proof(nonce="a" * 15 + "\n")


@pytest.mark.parametrize("user_id", ["042", 0, -1, 9223372036854775808])
def test_user_id_must_be_canonical_positive_bigint(user_id):
    with pytest.raises(ValueError):
        _proof(user_id=user_id)


def test_configuration_requires_visible_distinct_secret(monkeypatch):
    assert conversation_identity_boundary_status()["configured"] is True
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", "한" * 40)
    assert conversation_identity_boundary_status()["configured"] is False
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", "api-" + "a" * 32)
    assert conversation_identity_boundary_status()["secret_distinct"] is False
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", SECRET)
    monkeypatch.setenv("FOOD_CHATBOT_IDENTITY_MAX_AGE_SECONDS", "9999")
    assert conversation_identity_boundary_status()["max_age_valid"] is False


def test_body_requires_exact_bytes_not_text():
    with pytest.raises(ValueError):
        _proof(body='{"message":"not bytes"}')
