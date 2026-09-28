from pathlib import Path

import pytest

from app.infrastructure import rag
from app.infrastructure.external_ai_consent import (
    ExternalAIConsentDecision,
    reset_external_ai_request_context,
    set_external_ai_request_context,
)


@pytest.fixture(autouse=True)
def _reset_rag_endpoint_state(monkeypatch):
    rag.reset_rag_cache()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        raising=False,
    )
    token = set_external_ai_request_context(
        ExternalAIConsentDecision(
            verified=True,
            consent_granted=True,
            reason="verified",
            user_id="rag-endpoint-test",
        )
    )
    try:
        yield
    finally:
        reset_external_ai_request_context(token)
        rag.reset_rag_cache()


class _FakeHTTPClient:
    pass


class _FakeAsyncHTTPClient:
    pass


def _capture_embedding_constructor(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured["http_client_kwargs"] = kwargs
        return _FakeHTTPClient()

    def fake_async_client(**kwargs):
        captured["http_async_client_kwargs"] = kwargs
        return _FakeAsyncHTTPClient()

    def fake_embeddings(**kwargs):
        captured["embedding_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(rag.httpx, "Client", fake_client)
    monkeypatch.setattr(rag.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(rag, "OpenAIEmbeddings", fake_embeddings)
    return captured


def test_default_openai_embedding_host_is_allowed_and_redirects_are_disabled(
    monkeypatch,
):
    captured = _capture_embedding_constructor(monkeypatch)

    assert rag._get_embeddings() is not None

    kwargs = captured["embedding_kwargs"]
    assert kwargs["base_url"] == "https://api.openai.com/v1"
    assert isinstance(kwargs["http_client"], _FakeHTTPClient)
    assert isinstance(kwargs["http_async_client"], _FakeAsyncHTTPClient)
    assert kwargs["openai_proxy"] is None
    assert captured["http_client_kwargs"] == {
        "follow_redirects": False,
        "trust_env": False,
    }
    assert captured["http_async_client_kwargs"] == {
        "follow_redirects": False,
        "trust_env": False,
    }


def test_embedding_override_requires_exact_allowlist_before_constructor(
    monkeypatch,
):
    captured = _capture_embedding_constructor(monkeypatch)
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://embeddings.example.invalid/v1",
    )

    with pytest.raises(RuntimeError, match="override_host_not_allowed"):
        rag._get_embeddings()
    assert "embedding_kwargs" not in captured

    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "embeddings.example.invalid",
    )
    assert rag._get_embeddings() is not None
    assert captured["embedding_kwargs"]["base_url"] == (
        "https://embeddings.example.invalid/v1"
    )


@pytest.mark.parametrize(
    "unsafe_base_url",
    [
        "http://embeddings.example.invalid/v1",
        "https://user:pass@embeddings.example.invalid/v1",
        "https://embeddings.example.invalid/v1?api_key=secret",
        "https://embeddings.example.invalid/v1#fragment",
        "https://embeddings.example.invalid:8443/v1",
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://metadata.local/v1",
    ],
)
def test_unsafe_embedding_endpoint_never_constructs_provider(
    monkeypatch,
    unsafe_base_url,
):
    captured = _capture_embedding_constructor(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", unsafe_base_url)
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "embeddings.example.invalid,127.0.0.1,localhost,metadata.local",
    )

    with pytest.raises(RuntimeError, match="endpoint rejected by policy"):
        rag._get_embeddings()

    assert "embedding_kwargs" not in captured


def test_user_query_cannot_bypass_invalid_embedding_endpoint(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://127.0.0.1/v1",
    )
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "127.0.0.1",
    )
    build_calls = []
    monkeypatch.setattr(
        rag,
        "build_vectorstore",
        lambda: build_calls.append(True),
    )

    assert rag.get_rag_context_entries("private health query") == []
    assert build_calls == []
    assert "ip_literal_forbidden" in str(rag.get_rag_status()["rag_last_error"])


def test_local_index_inspection_still_works_when_external_endpoint_is_blocked(
    monkeypatch,
    tmp_path: Path,
):
    knowledge_dir = tmp_path / "knowledge"
    chroma_dir = tmp_path / "chroma"
    knowledge_dir.mkdir()
    (knowledge_dir / "rules.md").write_text("local rules", encoding="utf-8")
    monkeypatch.setenv("RAG_DOCS_DIR", str(knowledge_dir))
    monkeypatch.setenv("CHROMA_DB_DIR", str(chroma_dir))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://blocked.invalid/v1")

    manifest = rag._build_document_manifest()
    status = rag.get_rag_status()

    assert manifest[0]["path"] == "rules.md"
    assert status["document_count"] == 1
    assert status["loadable"] is True
    assert status["retriever_ready"] is False
    assert status["embedding_endpoint_policy"]["endpoint_allowed"] is False
    assert "http://blocked.invalid" not in repr(status)


def test_rag_endpoint_status_never_exposes_configured_host(monkeypatch):
    private_host = "embeddings-private.invalid"
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        f"https://{private_host}/v1",
    )
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        private_host,
    )

    status = rag.get_rag_embedding_endpoint_policy_status()

    assert status["endpoint_allowed"] is True
    assert private_host not in repr(status)
