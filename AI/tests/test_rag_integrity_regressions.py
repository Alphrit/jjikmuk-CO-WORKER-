import hashlib
import os
import logging
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from app.infrastructure import rag
from app.infrastructure.external_ai_consent import (
    ExternalAIConsentDecision,
    reset_external_ai_request_context,
    set_external_ai_request_context,
)


@pytest.fixture(autouse=True)
def _reset_rag_globals(monkeypatch):
    rag.reset_rag_cache()
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("RAG_CHUNK_SIZE", "500")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "80")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    consent_token = set_external_ai_request_context(
        ExternalAIConsentDecision(
            verified=True,
            consent_granted=True,
            reason="verified",
            user_id="rag-integrity-test",
        )
    )
    try:
        yield
    finally:
        reset_external_ai_request_context(consent_token)
        rag.reset_rag_cache()


def _configure_paths(monkeypatch, tmp_path: Path):
    knowledge_dir = tmp_path / "knowledge"
    chroma_dir = tmp_path / "chroma"
    knowledge_dir.mkdir()
    monkeypatch.setenv("RAG_DOCS_DIR", str(knowledge_dir))
    monkeypatch.setenv("CHROMA_DB_DIR", str(chroma_dir))
    return knowledge_dir, chroma_dir


def _write_current_index(knowledge_dir: Path, chroma_dir: Path, chunk_count: int = 1):
    chroma_dir.mkdir(parents=True, exist_ok=True)
    (chroma_dir / "chroma.sqlite3").write_bytes(b"sqlite-placeholder")
    manifest = rag._build_document_manifest(knowledge_dir)
    metadata = rag._build_index_metadata(
        document_manifest=manifest,
        indexed_chunk_count=chunk_count,
        settings=rag._get_index_settings(),
    )
    rag._write_index_metadata(metadata, chroma_dir)
    return metadata


def test_rag_provider_failure_does_not_log_or_retain_sensitive_query(
    monkeypatch,
    caplog,
):
    private_query = "당뇨와 우유 알레르기가 있는 사용자의 비공개 질문"

    class BrokenVectorStore:
        def similarity_search(self, query, k):
            raise RuntimeError(f"provider echoed query={query}; k={k}")

    monkeypatch.setattr(rag, "build_vectorstore", lambda: BrokenVectorStore())
    caplog.set_level(logging.WARNING, logger="app.infrastructure.rag")

    assert rag.get_rag_context_entries(private_query) == []

    status = rag.get_rag_status()
    assert status["rag_last_error"] == (
        "RAG context unavailable: error_type=RuntimeError"
    )
    assert private_query not in caplog.text
    assert private_query not in str(status["rag_last_error"])


class _RetrievalDocument:
    def __init__(self, source, source_sha256, page_content):
        self.metadata = {
            "source": source,
            "source_sha256": source_sha256,
        }
        self.page_content = page_content


class _RetrievalVectorstore:
    def __init__(self, documents):
        self.documents = documents

    def similarity_search(self, _query, k):
        return self.documents[:k]


def _retrieval_key(source, source_sha256, page_content):
    return (
        source,
        source_sha256,
        hashlib.sha256(page_content.encode("utf-8")).hexdigest(),
    )


def _configure_retrieval_fixture(monkeypatch, documents, verified_keys):
    monkeypatch.setattr(
        rag,
        "build_vectorstore",
        lambda: _RetrievalVectorstore(documents),
    )
    monkeypatch.setattr(rag, "_verified_rag_chunk_keys", lambda: verified_keys)


def test_rag_retrieval_returns_only_current_verified_chunk(monkeypatch):
    content = "현재 검증된 정책 문맥"
    source = "allergy/rules.md"
    digest = "a" * 64
    document = _RetrievalDocument(source, digest, content)
    _configure_retrieval_fixture(
        monkeypatch,
        [document],
        {_retrieval_key(source, digest, content)},
    )

    entries = rag.get_rag_context_entries("질문")

    assert entries == [{
        "source": "internal-policy:allergy/rules.md",
        "content": content,
        "text": f"[internal-policy:allergy/rules.md] {content}",
    }]


@pytest.mark.parametrize("source_sha256", [None, "0" * 64, "bad"])
def test_rag_retrieval_rejects_missing_or_stale_source_digest(
    monkeypatch,
    source_sha256,
):
    source = "allergy/rules.md"
    content = "현재 검증된 정책 문맥"
    current_digest = "a" * 64
    document = _RetrievalDocument(source, source_sha256, content)
    _configure_retrieval_fixture(
        monkeypatch,
        [document],
        {_retrieval_key(source, current_digest, content)},
    )

    assert rag.get_rag_context_entries("질문") == []
    assert rag.get_rag_status()["rag_last_error"] == (
        "RAG context unavailable: retrieval_integrity_check_failed"
    )


def test_rag_retrieval_rejects_altered_content_and_unknown_source(monkeypatch):
    current_source = "allergy/rules.md"
    current_digest = "a" * 64
    trusted_content = "현재 검증된 정책 문맥"
    altered = _RetrievalDocument(
        current_source,
        current_digest,
        "조작된 정책 문맥",
    )
    unknown = _RetrievalDocument(
        "other/rules.md",
        current_digest,
        trusted_content,
    )
    _configure_retrieval_fixture(
        monkeypatch,
        [altered],
        {_retrieval_key(current_source, current_digest, trusted_content)},
    )
    assert rag.get_rag_context_entries("질문") == []

    _configure_retrieval_fixture(
        monkeypatch,
        [unknown],
        {_retrieval_key(current_source, current_digest, trusted_content)},
    )
    assert rag.get_rag_context_entries("질문") == []


def test_rag_retrieval_mixed_valid_and_tampered_batch_fails_closed(monkeypatch):
    source = "allergy/rules.md"
    digest = "a" * 64
    trusted_content = "현재 검증된 정책 문맥"
    valid = _RetrievalDocument(source, digest, trusted_content)
    tampered = _RetrievalDocument(source, digest, "조작된 정책 문맥")
    _configure_retrieval_fixture(
        monkeypatch,
        [valid, tampered],
        {_retrieval_key(source, digest, trusted_content)},
    )

    assert rag.get_rag_context_entries("질문", top_k=2) == []


def test_manifest_uses_relative_paths_and_detects_same_name_content_change(
    monkeypatch,
    tmp_path,
):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    first = knowledge_dir / "allergy" / "rules.md"
    second = knowledge_dir / "diet" / "rules.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("우유 알레르기", encoding="utf-8")
    second.write_text("저염식 기준", encoding="utf-8")

    metadata = _write_current_index(knowledge_dir, chroma_dir)

    assert [entry["path"] for entry in metadata["document_manifest"]] == [
        "allergy/rules.md",
        "diet/rules.md",
    ]
    assert rag.get_rag_status()["index_stale"] is False

    first.write_text("우유 알레르기 기준 변경", encoding="utf-8")

    status = rag.get_rag_status()
    assert status["index_stale"] is True
    assert status["retriever_ready"] is False


def test_manifest_detects_document_addition_and_deletion(monkeypatch, tmp_path):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    original = knowledge_dir / "original.md"
    original.write_text("original", encoding="utf-8")
    _write_current_index(knowledge_dir, chroma_dir)

    added = knowledge_dir / "nested" / "added.md"
    added.parent.mkdir()
    added.write_text("added", encoding="utf-8")
    assert rag.get_rag_status()["index_stale"] is True

    _write_current_index(knowledge_dir, chroma_dir)
    assert rag.get_rag_status()["index_stale"] is False

    original.unlink()
    assert rag.get_rag_status()["index_stale"] is True


def test_knowledge_document_symlink_outside_root_is_rejected(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    outside_document = tmp_path / "outside-secret.md"
    outside_document.write_text("outside secret", encoding="utf-8")
    linked_document = knowledge_dir / "linked.md"
    try:
        linked_document.symlink_to(outside_document)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(ValueError, match="symbolic links"):
        rag._build_document_manifest(knowledge_dir)

    status = rag.get_rag_status()
    assert status["retriever_ready"] is False
    assert status["loadable"] is False
    assert "symbolic links" in str(status["status_reason"])
    assert "outside-secret" not in str(status)


def test_knowledge_symlink_directory_is_rejected(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "secret.md").write_text("secret", encoding="utf-8")
    linked_directory = knowledge_dir / "linked-directory"
    try:
        linked_directory.symlink_to(outside_directory, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(ValueError, match="symbolic links"):
        rag._list_knowledge_documents(knowledge_dir)


def test_knowledge_document_size_limits_are_enforced(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "MAX_RAG_KNOWLEDGE_FILE_BYTES", 8)
    (knowledge_dir / "large.md").write_bytes(b"123456789")

    with pytest.raises(ValueError, match="per-file size limit"):
        rag._build_document_manifest(knowledge_dir)


def test_knowledge_document_count_limit_is_enforced(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "MAX_RAG_KNOWLEDGE_DOCUMENTS", 1)
    (knowledge_dir / "one.md").write_text("one", encoding="utf-8")
    (knowledge_dir / "two.md").write_text("two", encoding="utf-8")

    with pytest.raises(ValueError, match="document count limit"):
        rag._build_document_manifest(knowledge_dir)


def test_knowledge_document_total_size_limit_is_enforced(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(rag, "MAX_RAG_KNOWLEDGE_FILE_BYTES", 8)
    monkeypatch.setattr(rag, "MAX_RAG_KNOWLEDGE_TOTAL_BYTES", 5)
    (knowledge_dir / "one.md").write_bytes(b"123")
    (knowledge_dir / "two.md").write_bytes(b"456")

    with pytest.raises(ValueError, match="total size limit"):
        rag._build_document_manifest(knowledge_dir)


def test_knowledge_documents_require_utf8_without_nul(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    document = knowledge_dir / "rules.md"
    document.write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="valid UTF-8"):
        rag._build_document_manifest(knowledge_dir)

    document.write_bytes(b"rule\x00hidden")
    with pytest.raises(ValueError, match="NUL"):
        rag._build_document_manifest(knowledge_dir)


def test_safe_loader_uses_relative_source_and_verified_manifest(monkeypatch, tmp_path):
    knowledge_dir, _chroma_dir = _configure_paths(monkeypatch, tmp_path)
    nested = knowledge_dir / "nutrition"
    nested.mkdir()
    (nested / "rules.md").write_text("저염식 기준", encoding="utf-8")
    manifest = rag._build_document_manifest(knowledge_dir)

    documents = rag._load_documents(expected_manifest=manifest)

    assert len(documents) == 1
    assert documents[0].page_content == "저염식 기준"
    assert documents[0].metadata["source"] == "nutrition/rules.md"
    assert documents[0].metadata["source_sha256"] == manifest[0]["sha256"]

    (nested / "rules.md").write_text("변경된 기준", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed between"):
        rag._load_documents(expected_manifest=manifest)


@pytest.mark.parametrize(
    "value",
    [
        "/private/rules.md",
        "../rules.md",
        "nested/../../rules.md",
        "rules.txt",
        "rules.md\x00hidden",
        "",
        None,
    ],
)
def test_rag_source_identifier_rejects_untrusted_metadata(value):
    assert rag._internal_policy_source_identifier(value) is None


def test_rag_source_identifier_labels_internal_policy_without_absolute_path():
    assert rag._internal_policy_source_identifier("nutrition/rules.md") == (
        "internal-policy:nutrition/rules.md"
    )


def test_rag_search_limit_is_bounded_before_provider_access(monkeypatch):
    search_called = False

    def unexpected_build():
        nonlocal search_called
        search_called = True
        raise AssertionError("provider/index must not be touched")

    monkeypatch.setattr(rag, "build_vectorstore", unexpected_build)

    assert rag.get_rag_context_entries("query", top_k=6) == []
    assert search_called is False
    assert rag.get_rag_status()["rag_last_error"] == (
        "RAG context unavailable: invalid_search_limit"
    )


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [
        (rag.MAX_RAG_CHUNK_SIZE + 1, 0),
        (2000, rag.MAX_RAG_CHUNK_OVERLAP + 1),
        (100, 51),
    ],
)
def test_rag_chunk_settings_reject_cost_amplifying_values(
    monkeypatch,
    chunk_size,
    chunk_overlap,
):
    monkeypatch.setenv("RAG_CHUNK_SIZE", str(chunk_size))
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", str(chunk_overlap))

    with pytest.raises(ValueError, match="RAG_CHUNK"):
        rag._get_index_settings()


@pytest.mark.parametrize(
    ("environment_name", "changed_value"),
    [
        ("EMBEDDING_MODEL", "text-embedding-3-large"),
        ("RAG_CHUNK_SIZE", "600"),
        ("RAG_CHUNK_OVERLAP", "90"),
    ],
)
def test_index_settings_change_marks_index_stale(
    monkeypatch,
    tmp_path,
    environment_name,
    changed_value,
):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    _write_current_index(knowledge_dir, chroma_dir)

    monkeypatch.setenv(environment_name, changed_value)

    status = rag.get_rag_status()
    assert status["metadata_complete"] is True
    assert status["index_stale"] is True
    assert status["retriever_ready"] is False


def test_legacy_metadata_is_incomplete_and_stale(monkeypatch, tmp_path):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    chroma_dir.mkdir()
    (chroma_dir / "chroma.sqlite3").write_bytes(b"sqlite-placeholder")
    rag._write_index_metadata(
        {
            "last_indexed_at": "2026-01-01T00:00:00",
            "indexed_document_count": 1,
            "indexed_chunk_count": 1,
            "documents": ["rules.md"],
            "embedding_model": "text-embedding-3-small",
            "chunk_size": 500,
            "chunk_overlap": 80,
        },
        chroma_dir,
    )

    status = rag.get_rag_status()
    assert status["metadata_complete"] is False
    assert status["index_stale"] is True
    assert status["retriever_ready"] is False


class _Collection:
    def __init__(self, count: int):
        self._count = count

    def count(self):
        return self._count


class _CachedVectorstore:
    def __init__(self, count: int = 1):
        self._collection = _Collection(count)


def test_cached_vectorstore_is_not_returned_after_document_change(monkeypatch, tmp_path):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    document = knowledge_dir / "rules.md"
    document.write_text("before", encoding="utf-8")
    _write_current_index(knowledge_dir, chroma_dir)
    monkeypatch.setattr(rag, "_rag_dependencies_available", lambda: True)
    rag._vectorstore = _CachedVectorstore()
    rag._vectorstore_index_dir = chroma_dir.resolve()

    document.write_text("after", encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale"):
        rag.build_vectorstore()
    assert rag._vectorstore is None


def test_existing_invalid_index_is_blocked_instead_of_loaded(monkeypatch, tmp_path):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    chroma_dir.mkdir()
    (chroma_dir / "chroma.sqlite3").write_bytes(b"sqlite-placeholder")
    monkeypatch.setattr(rag, "_rag_dependencies_available", lambda: True)

    with pytest.raises(RuntimeError, match="metadata is missing or incomplete"):
        rag.build_vectorstore()


@pytest.mark.parametrize("index_state", ["incomplete", "stale"])
def test_search_fails_closed_before_touching_invalid_cached_store(
    monkeypatch,
    tmp_path,
    index_state,
):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    document = knowledge_dir / "rules.md"
    document.write_text("before", encoding="utf-8")
    if index_state == "stale":
        _write_current_index(knowledge_dir, chroma_dir)
        document.write_text("after", encoding="utf-8")
    else:
        chroma_dir.mkdir()
        (chroma_dir / "chroma.sqlite3").write_bytes(b"sqlite-placeholder")
        rag._write_index_metadata({"metadata_version": 2}, chroma_dir)

    class SearchTrackingVectorstore(_CachedVectorstore):
        search_calls = 0

        def similarity_search(self, _query, *, k):
            self.search_calls += 1
            return []

    cached = SearchTrackingVectorstore()
    rag._vectorstore = cached
    rag._vectorstore_index_dir = chroma_dir.resolve()
    monkeypatch.setattr(rag, "_rag_dependencies_available", lambda: True)

    entries = rag.get_rag_context_entries("검색하면 안 되는 인덱스")

    assert entries == []
    assert cached.search_calls == 0
    assert rag._vectorstore is None
    status = rag.get_rag_status()
    assert status["retriever_ready"] is False
    assert status["index_stale"] is True
    assert index_state in str(status["rag_last_error"]).lower() or (
        index_state == "incomplete"
        and "metadata is missing or incomplete" in str(status["rag_last_error"]).lower()
    )


def test_loaded_collection_count_must_match_metadata(monkeypatch, tmp_path):
    knowledge_dir, chroma_dir = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    _write_current_index(knowledge_dir, chroma_dir, chunk_count=2)
    monkeypatch.setattr(rag, "_rag_dependencies_available", lambda: True)
    monkeypatch.setattr(rag, "_get_embeddings", lambda: object())

    class WrongCountChroma:
        def __init__(self, **_kwargs):
            self._collection = _Collection(1)

    monkeypatch.setattr(rag, "Chroma", WrongCountChroma)

    with pytest.raises(RuntimeError, match="does not match its metadata"):
        rag.build_vectorstore()


def test_rebuild_rejects_protected_and_unmarked_custom_directories(monkeypatch, tmp_path):
    knowledge_dir, custom_dir = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    custom_dir.mkdir()
    (custom_dir / "unrelated.txt").write_text("do not delete", encoding="utf-8")

    with pytest.raises(RuntimeError, match="without the RAG index marker"):
        rag._validate_rebuild_target(custom_dir)
    with pytest.raises(RuntimeError, match="protected directory"):
        rag._validate_rebuild_target(rag.APP_DIR)

    rag._write_index_marker(custom_dir)
    rag._validate_rebuild_target(custom_dir)


def test_rebuild_rejects_symlink_target(monkeypatch, tmp_path):
    knowledge_dir, _ = _configure_paths(monkeypatch, tmp_path)
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    actual_dir = tmp_path / "actual-index"
    actual_dir.mkdir()
    linked_dir = tmp_path / "linked-index"
    linked_dir.symlink_to(actual_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        rag._validate_rebuild_target(linked_dir)


class _FakeSplitter:
    def __init__(self, **_kwargs):
        pass

    def split_documents(self, _documents):
        return [object()]


def _prepare_rebuild(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "rules.md").write_text("rules", encoding="utf-8")
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    sentinel = chroma_dir / "old-index.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    monkeypatch.setenv("RAG_DOCS_DIR", str(knowledge_dir))
    monkeypatch.delenv("CHROMA_DB_DIR", raising=False)
    monkeypatch.setattr(rag, "DEFAULT_CHROMA_DIR", chroma_dir)
    monkeypatch.setattr(rag, "_rag_dependencies_available", lambda: True)
    monkeypatch.setattr(
        rag,
        "_load_documents",
        lambda *, expected_manifest=None: [object()],
    )
    monkeypatch.setattr(rag, "RecursiveCharacterTextSplitter", _FakeSplitter)
    monkeypatch.setattr(rag, "_get_embeddings", lambda: object())
    return chroma_dir, sentinel


def test_failed_staged_build_preserves_existing_index(monkeypatch, tmp_path):
    chroma_dir, sentinel = _prepare_rebuild(monkeypatch, tmp_path)

    class FailingChroma:
        @classmethod
        def from_documents(cls, **_kwargs):
            raise RuntimeError("embedding provider failed")

    monkeypatch.setattr(rag, "Chroma", FailingChroma)

    with pytest.raises(RuntimeError, match="embedding provider failed"):
        rag.build_vectorstore(force_rebuild=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.build-*"))


def test_completed_staged_index_is_swapped_and_marked(monkeypatch, tmp_path):
    chroma_dir, sentinel = _prepare_rebuild(monkeypatch, tmp_path)

    class FakeChroma:
        def __init__(self, *, persist_directory, embedding_function):
            assert embedding_function is not None
            assert (Path(persist_directory) / "chroma.sqlite3").is_file()
            self._collection = _Collection(1)

        @classmethod
        def from_documents(cls, *, documents, embedding, persist_directory):
            assert embedding is not None
            target = Path(persist_directory)
            (target / "chroma.sqlite3").write_bytes(b"new-index")
            instance = object.__new__(cls)
            instance._collection = _Collection(len(documents))
            return instance

    monkeypatch.setattr(rag, "Chroma", FakeChroma)

    result = rag.build_vectorstore(force_rebuild=True)

    assert result._collection.count() == 1
    assert not sentinel.exists()
    assert rag._has_valid_index_marker(chroma_dir)
    assert rag.get_rag_status()["index_stale"] is False
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.backup-*"))


def test_final_load_failure_rolls_back_existing_index(monkeypatch, tmp_path):
    chroma_dir, sentinel = _prepare_rebuild(monkeypatch, tmp_path)

    class LoadFailingChroma:
        def __init__(self, **_kwargs):
            raise RuntimeError("new index cannot be loaded")

        @classmethod
        def from_documents(cls, *, documents, embedding, persist_directory):
            target = Path(persist_directory)
            (target / "chroma.sqlite3").write_bytes(b"new-index")
            instance = object.__new__(cls)
            instance._collection = _Collection(len(documents))
            return instance

    monkeypatch.setattr(rag, "Chroma", LoadFailingChroma)

    with pytest.raises(RuntimeError, match="new index cannot be loaded"):
        rag.build_vectorstore(force_rebuild=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.backup-*"))
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.build-*"))


def test_concurrent_rebuild_fails_fast_and_preserves_live_index(monkeypatch, tmp_path):
    chroma_dir, sentinel = _prepare_rebuild(monkeypatch, tmp_path)
    first_build_started = Event()
    allow_first_build_to_finish = Event()
    counter_lock = Lock()
    build_calls = 0
    thread_errors = []

    class BlockingChroma:
        def __init__(self, *, persist_directory, embedding_function):
            assert embedding_function is not None
            assert (Path(persist_directory) / "chroma.sqlite3").is_file()
            self._collection = _Collection(1)

        @classmethod
        def from_documents(cls, *, documents, embedding, persist_directory):
            nonlocal build_calls
            assert embedding is not None
            target = Path(persist_directory)
            (target / "chroma.sqlite3").write_bytes(b"new-index")
            instance = object.__new__(cls)
            instance._collection = _Collection(len(documents))
            with counter_lock:
                build_calls += 1
                call_number = build_calls
            if call_number == 1:
                first_build_started.set()
                if not allow_first_build_to_finish.wait(timeout=5):
                    raise RuntimeError("test timed out waiting to finish first build")
            return instance

    monkeypatch.setattr(rag, "Chroma", BlockingChroma)

    def run_first_rebuild():
        try:
            rag.build_vectorstore(force_rebuild=True)
        except BaseException as exc:  # Captured so the main test can report it.
            thread_errors.append(exc)

    first_thread = Thread(target=run_first_rebuild, daemon=True)
    first_thread.start()
    assert first_build_started.wait(timeout=5)
    assert rag.get_rag_status()["rebuild_in_progress"] is True

    with pytest.raises(RuntimeError, match="already in progress"):
        rag.build_vectorstore(force_rebuild=True)

    assert build_calls == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve me"

    allow_first_build_to_finish.set()
    first_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert thread_errors == []
    assert rag.get_rag_status()["rebuild_in_progress"] is False
    assert rag.get_rag_status()["index_stale"] is False
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.backup-*"))
    assert not list(chroma_dir.parent.glob(f".{chroma_dir.name}.build-*"))


@pytest.mark.skipif(rag.fcntl is None, reason="POSIX advisory locking is unavailable")
def test_filesystem_lock_blocks_a_second_worker_handle(tmp_path):
    chroma_dir = tmp_path / "chroma"
    lock_path = rag._get_rebuild_lock_path(chroma_dir)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    rag.fcntl.flock(descriptor, rag.fcntl.LOCK_EX | rag.fcntl.LOCK_NB)

    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            with rag._acquire_rebuild_lock(chroma_dir):
                pytest.fail("a second filesystem lock must not be acquired")
    finally:
        rag.fcntl.flock(descriptor, rag.fcntl.LOCK_UN)
        os.close(descriptor)

    assert rag.get_rag_status()["rebuild_in_progress"] is False
