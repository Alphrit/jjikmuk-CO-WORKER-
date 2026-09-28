import pytest

from app import start


def test_ready_index_does_not_trigger_rebuild(monkeypatch):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": True, "index_stale": False})
    monkeypatch.setattr(start, "rebuild_rag_index", lambda: pytest.fail("ready index must not be rebuilt"))
    start.prepare_rag(rebuild=True)


def test_stale_index_needs_explicit_rebuild_authorization(monkeypatch):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": False, "index_stale": True})
    monkeypatch.setattr(start, "rebuild_rag_index", lambda: pytest.fail("no implicit external embedding"))
    with pytest.raises(RuntimeError, match="not ready"):
        start.prepare_rag()


def test_authorized_rebuild_is_verified_before_serving(monkeypatch):
    monkeypatch.setattr(start, "get_rag_status", lambda: {"retriever_ready": False, "index_stale": True})
    monkeypatch.setattr(start, "rebuild_rag_index", lambda: {"retriever_ready": True, "index_stale": False})
    start.prepare_rag(rebuild=True)
    monkeypatch.setattr(start, "rebuild_rag_index", lambda: {"retriever_ready": True, "index_stale": True})
    with pytest.raises(RuntimeError, match="not ready"):
        start.prepare_rag(rebuild=True)
