import json
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.infrastructure import llm, rag
from app.infrastructure.external_ai_consent import (
    CONSENT_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    USER_ID_HEADER,
    ExternalAIConsentDecision,
    current_external_ai_processing_summary,
    external_ai_request_allowed,
    external_ai_request_context,
    reset_external_ai_consent_nonce_registry,
    sign_external_ai_consent,
    verify_external_ai_consent,
)
from app.infrastructure.repository import InMemoryActivityRepository
from app.services.activity import get_activity_repository, set_activity_repository
from test_released_knowledge import packaged_sources


CONSENT_SECRET = "consent-" + "s" * 40
USER_ID = "user-42"
NONCE = "nonce-0123456789abcdef"


@pytest.mark.parametrize("signed", [False, True])
def test_general_followup_rag_generation_keeps_request_consent_boundary(packaged_sources, monkeypatch, signed):
    from rag_test_helpers import grounding_review

    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    document = next(d for d in rag._load_documents()
                    if d.metadata["source"] == "sugar-sweeteners-calories.source.json")
    answer = "무가당 표시는 열량이 없다는 뜻은 아니에요."
    searches, outputs = [], []

    class Store:
        def similarity_search(self, query, k, **kwargs):
            searches.append((query, kwargs))
            return [document]

    monkeypatch.setattr(rag, "build_vectorstore", lambda: Store())
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    replies = iter([answer, grounding_review(answer, [document.page_content])])

    def provider(*_args):
        outputs.append(True)
        return {"output_text": next(replies)}

    monkeypatch.setattr(llm, "_post_json", provider)
    body = _json_body({
        "message": "그럼 칼로리는?",
        "chat_history": [
            {"role": "user", "content": "무설탕과 무가당의 차이가 뭐야?"},
            {"role": "assistant", "content": "무당과 무가당은 다른 표시예요."},
        ],
    })
    response = TestClient(api_main.app).post(
        "/chat", content=body,
        headers=_signed_headers(body) if signed else {"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("current_product") is None
    if signed:
        assert len(searches) == 1 and len(outputs) == 2
        from app.intent.explanation_topics import requested_topics
        assert "무가당" in searches[0][0] and requested_topics(searches[0][0]) == {"sugar_energy"}
        assert payload["answer"] == answer and payload["answer_source"] == "rag"
        assert payload["external_ai_processing"]["services"] == ["rag", "llm"]
    else:
        assert searches == outputs == []
        assert payload["answer_source"] == "rules" and not payload["sources"]


@pytest.fixture(autouse=True)
def _external_ai_environment(monkeypatch):
    reset_external_ai_consent_nonce_registry()
    llm.set_llm_client(None)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", CONSENT_SECRET)
    monkeypatch.setenv(
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_MAX_AGE_SECONDS",
        "60",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "configured-test-key")
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", "")
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", "")
    yield
    llm.set_llm_client(None)
    reset_external_ai_consent_nonce_registry()


def _json_body(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_headers(
    body: bytes,
    *,
    timestamp: int | None = None,
    consent: str = "granted",
    nonce: str = NONCE,
    user_id: str = USER_ID,
    signature_body: bytes | None = None,
) -> dict[str, str]:
    issued_at = int(time.time()) if timestamp is None else timestamp
    signature = sign_external_ai_consent(
        secret=CONSENT_SECRET,
        timestamp=issued_at,
        method="POST",
        path="/chat",
        user_id=user_id,
        consent=consent,
        nonce=nonce,
        body=body if signature_body is None else signature_body,
    )
    return {
        "Content-Type": "application/json",
        USER_ID_HEADER: user_id,
        CONSENT_HEADER: consent,
        TIMESTAMP_HEADER: str(issued_at),
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature,
    }


def _post_unknown_chat(body: bytes, headers: dict[str, str] | None = None):
    # An unknown intent reaches the LLM intent fallback once and then remains a
    # deterministic rule response when the fake provider returns unknown.
    with patch.object(
        llm,
        "_generate_openai",
        return_value='{"intent":"unknown"}',
    ) as provider:
        response = TestClient(api_main.app).post(
            "/chat",
            content=body,
            headers=headers or {"Content-Type": "application/json"},
        )
    return response, provider


def test_valid_request_bound_signature_allows_provider_and_reports_usage():
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 zyxq"})

    response, provider = _post_unknown_chat(body, _signed_headers(body))

    assert response.status_code == 200
    provider.assert_called_once()
    processing = response.json()["external_ai_processing"]
    assert processing == {
        "allowed_for_request": True,
        "used": True,
        "status": "used",
        "services": ["llm"],
    }
    assert USER_ID not in str(processing)
    assert CONSENT_SECRET not in response.text
    assert external_ai_request_allowed() is False


def test_failed_provider_attempt_is_audited_without_prompt_or_identity():
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 zyxq"})
    previous = get_activity_repository()
    activities = InMemoryActivityRepository()
    set_activity_repository(activities)
    try:
        with patch.object(
            llm,
            "_generate_openai",
            return_value=None,
        ):
            response = TestClient(api_main.app).post(
                "/chat",
                content=body,
                headers=_signed_headers(body),
            )
    finally:
        set_activity_repository(previous)

    assert response.status_code == 200
    assert response.json()["answer_source"] == "rules"
    assert len(activities.records) == 1
    record = activities.records[0]
    assert record.external_ai_used is True
    assert record.external_ai_services == ["llm"]
    assert record.response_id == response.json()["response_id"]
    assert record.message is None
    serialized = record.model_dump_json()
    assert USER_ID not in serialized
    assert CONSENT_SECRET not in serialized
    assert "provider prompt must not enter logs" not in serialized


def test_missing_consent_is_successful_rule_only_and_provider_is_not_called():
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 zyxq"})

    response, provider = _post_unknown_chat(body)

    assert response.status_code == 200
    provider.assert_not_called()
    processing = response.json()["external_ai_processing"]
    assert processing["allowed_for_request"] is False
    assert processing["used"] is False
    assert processing["status"] == "consent_not_verified"


def test_body_boolean_is_ignored_and_never_grants_external_processing():
    body = _json_body({
        "message": "의도를 알 수 없는 모호한 문장 zyxq",
        "external_ai_consent": True,
    })

    response, provider = _post_unknown_chat(body)

    assert response.status_code == 200
    provider.assert_not_called()
    assert response.json()["external_ai_processing"]["used"] is False


@pytest.mark.parametrize("failure", ["expired", "tampered_body", "tampered_signature"])
def test_expired_or_tampered_assertion_fails_closed_without_failing_chat(failure):
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 zyxq"})
    if failure == "expired":
        headers = _signed_headers(body, timestamp=int(time.time()) - 61)
    elif failure == "tampered_body":
        headers = _signed_headers(
            body,
            signature_body=_json_body({"message": "서명했던 다른 본문"}),
        )
    else:
        headers = _signed_headers(body)
        headers[SIGNATURE_HEADER] = "0" * 64

    response, provider = _post_unknown_chat(body, headers)

    assert response.status_code == 200
    provider.assert_not_called()
    processing = response.json()["external_ai_processing"]
    assert processing["status"] == "consent_not_verified"
    assert processing["used"] is False


def test_valid_signed_denial_remains_rule_only_and_is_distinguishable_to_ui():
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 zyxq"})
    headers = _signed_headers(body, consent="denied")

    response, provider = _post_unknown_chat(body, headers)

    assert response.status_code == 200
    provider.assert_not_called()
    assert response.json()["external_ai_processing"] == {
        "allowed_for_request": False,
        "used": False,
        "status": "consent_denied",
        "services": [],
    }


def test_signature_is_bound_to_method_path_user_consent_nonce_and_exact_body():
    now = int(time.time())
    body = _json_body({"message": "테스트"})
    headers = _signed_headers(body, timestamp=now)

    valid = verify_external_ai_consent(
        headers={key.lower(): value for key, value in headers.items()},
        method="POST",
        path="/chat",
        body=body,
        operator_enabled=True,
        now=now,
    )
    wrong_path = verify_external_ai_consent(
        headers={key.lower(): value for key, value in headers.items()},
        method="POST",
        path="/another-chat",
        body=body,
        operator_enabled=True,
        now=now,
    )

    assert valid.allowed is True
    assert wrong_path.allowed is False
    assert wrong_path.reason == "invalid_assertion"


@pytest.mark.parametrize("consent", ["granted", "denied"])
def test_valid_consent_assertion_is_one_time_use_in_this_process(consent):
    now = int(time.time())
    body = _json_body({"message": "재사용 차단 테스트"})
    headers = _signed_headers(body, timestamp=now, consent=consent)
    normalized_headers = {key.lower(): value for key, value in headers.items()}

    first = verify_external_ai_consent(
        headers=normalized_headers,
        method="POST",
        path="/chat",
        body=body,
        operator_enabled=True,
        now=now,
    )
    replay = verify_external_ai_consent(
        headers=normalized_headers,
        method="POST",
        path="/chat",
        body=body,
        operator_enabled=True,
        now=now,
    )

    assert first.verified is True
    assert first.consent_granted is (consent == "granted")
    assert replay.allowed is False
    assert replay.verified is False
    assert replay.reason == "replayed_assertion"


def test_replayed_http_consent_proof_cannot_trigger_a_second_provider_call():
    body = _json_body({"message": "의도를 알 수 없는 모호한 문장 replay"})
    headers = _signed_headers(body)

    first_response, first_provider = _post_unknown_chat(body, headers)
    replay_response, replay_provider = _post_unknown_chat(body, headers)

    assert first_response.status_code == 200
    first_provider.assert_called_once()
    assert first_response.json()["external_ai_processing"]["status"] == "used"
    assert replay_response.status_code == 200
    replay_provider.assert_not_called()
    assert replay_response.json()["external_ai_processing"]["status"] == (
        "consent_not_verified"
    )


def test_context_is_isolated_between_concurrent_requests_and_resets_afterward():
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    lock = threading.Lock()

    allowed = ExternalAIConsentDecision(
        verified=True,
        consent_granted=True,
        reason="verified",
        user_id="allowed-user",
    )
    denied = ExternalAIConsentDecision(
        verified=True,
        consent_granted=False,
        reason="consent_denied",
        user_id="denied-user",
    )

    def provider(prompt: str) -> str:
        return f"provider:{prompt}"

    def worker(name: str, decision: ExternalAIConsentDecision) -> None:
        with external_ai_request_context(decision):
            barrier.wait(timeout=2)
            generated = llm.generate_text(name)
            summary = current_external_ai_processing_summary()
        with lock:
            results[name] = (generated, summary, external_ai_request_allowed())

    with patch.object(llm, "_generate_openai", side_effect=provider) as provider_mock:
        threads = [
            threading.Thread(target=worker, args=("allowed", allowed)),
            threading.Thread(target=worker, args=("denied", denied)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert results["allowed"][0] == "provider:allowed"
    assert results["allowed"][1]["used"] is True
    assert results["denied"][0] is None
    assert results["denied"][1]["used"] is False
    assert results["allowed"][2] is False
    assert results["denied"][2] is False
    provider_mock.assert_called_once_with("allowed")
    assert external_ai_request_allowed() is False


def test_user_query_rag_cannot_search_without_request_consent(monkeypatch):
    class Document:
        metadata = {
            "source": "allergy_rules.md",
            "source_sha256": "test-source-digest",
        }
        page_content = "검증된 테스트 문맥"

    class TrackingVectorstore:
        def __init__(self):
            self.search_calls = 0

        def similarity_search(self, _query, *, k):
            self.search_calls += 1
            return [Document()]

    vectorstore = TrackingVectorstore()
    monkeypatch.setattr(rag, "build_vectorstore", lambda: vectorstore)
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    monkeypatch.setattr(
        rag,
        "_verified_rag_chunk_keys",
        lambda: {
            (
                "allergy_rules.md",
                "test-source-digest",
                rag.hashlib.sha256("검증된 테스트 문맥".encode("utf-8")).hexdigest(),
            )
        },
    )
    monkeypatch.setattr(
        rag,
        "_internal_policy_source_identifier",
        lambda value: "internal-policy:allergy_rules.md"
        if value == "allergy_rules.md"
        else None,
    )

    assert rag.get_rag_context_entries("민감한 건강 질문") == []
    assert vectorstore.search_calls == 0

    allowed = ExternalAIConsentDecision(
        verified=True,
        consent_granted=True,
        reason="verified",
        user_id="rag-user",
    )
    with external_ai_request_context(allowed):
        entries = rag.get_rag_context_entries("민감한 건강 질문")
        summary = current_external_ai_processing_summary()

    assert vectorstore.search_calls == 1
    assert entries[0]["source"] == "internal-policy:allergy_rules.md"
    assert summary["used"] is True
    assert summary["services"] == ["rag"]
    assert external_ai_request_allowed() is False
