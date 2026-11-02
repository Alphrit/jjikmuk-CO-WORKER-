"""The Kotlin bridge and Python verifier share one exact UTF-8 signature."""

import json
from pathlib import Path

from app.infrastructure import external_ai_consent as consent


def test_backend_golden_request_is_accepted_by_python(monkeypatch):
    path = Path(__file__).resolve().parent / "fixtures/chat-external-ai-consent-v1.json"
    fixture = json.loads(path.read_text())
    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", fixture["secret"])
    consent.reset_external_ai_consent_nonce_registry()
    headers = {
        consent.USER_ID_HEADER: fixture["user_id"], consent.CONSENT_HEADER: fixture["consent"],
        consent.TIMESTAMP_HEADER: str(fixture["timestamp"]), consent.NONCE_HEADER: fixture["nonce"],
        consent.SIGNATURE_HEADER: fixture["signature"],
    }
    decision = consent.verify_external_ai_consent(headers=headers, method=fixture["method"], path=fixture["path"],
        body=fixture["body"].encode(), operator_enabled=True, now=fixture["timestamp"] + 1)
    assert decision.allowed and decision.user_id == "42"
    consent.reset_external_ai_consent_nonce_registry()
