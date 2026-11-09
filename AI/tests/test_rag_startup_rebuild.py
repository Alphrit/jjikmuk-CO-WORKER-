"""API startup rebuilds a stale RAG index only when external AI is enabled."""

import pytest

from app import start


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_ENV", "development")
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "configured-test-key")
    monkeypatch.delenv("FOOD_CHATBOT_RAG_AUTO_REBUILD", raising=False)
    calls = []
    monkeypatch.setattr(start, "rebuild_rag_index", lambda: calls.append(True) or {"retriever_ready": True})
    return calls


def test_stale_index_is_rebuilt_in_the_background(enabled, monkeypatch):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": False, "index_stale": True})
    thread = start.rebuild_stale_rag_in_background()
    thread.join(5)
    assert enabled == [True]


@pytest.mark.parametrize("env", [
    {"FOOD_CHATBOT_RAG_AUTO_REBUILD": "false"}, {"FOOD_CHATBOT_ENV": "test"},
    {"FOOD_CHATBOT_ENABLE_EXTERNAL_AI": "false"}, {"OPENAI_API_KEY": ""},
])
def test_no_rebuild_without_external_ai_or_when_turned_off(enabled, monkeypatch, env):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": False, "index_stale": True})
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert start.rebuild_stale_rag_in_background() is None
    assert enabled == []


def test_current_index_is_kept(enabled, monkeypatch):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": True, "index_stale": False})
    assert start.rebuild_stale_rag_in_background() is None
    assert enabled == []
