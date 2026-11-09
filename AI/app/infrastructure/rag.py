import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from string import hexdigits
from typing import Dict, Iterator, List, Optional

import httpx

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows falls back to process-local locking.
    fcntl = None

from dotenv import load_dotenv

try:
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_openai import OpenAIEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    _RAG_IMPORT_ERROR: Optional[Exception] = None
except ModuleNotFoundError as exc:
    Chroma = None
    Document = None
    OpenAIEmbeddings = None
    RecursiveCharacterTextSplitter = None
    _RAG_IMPORT_ERROR = exc

from app.core.models import Product, Profile
from app.core.nutrition_basis import nutrition_basis_label
from app.core.product_provenance import (
    effective_label_lineage_status,
    label_freshness_status,
    label_provenance_limitation_reason,
    label_provenance_supports,
)
from app.infrastructure.external_ai_consent import (
    external_ai_request_allowed,
    mark_external_ai_used,
)
from app.infrastructure.external_endpoint_policy import (
    ExternalEndpointDecision,
    external_endpoint_policy_status,
    validate_external_ai_url,
)
from app.infrastructure.llm import external_ai_processing_enabled
from app.infrastructure.external_ai_state import required as persistent_state_required
from app.infrastructure.knowledge_review import parse_released_excerpt, excerpt_reference


logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[1]
AI_DIR = APP_DIR.parent
load_dotenv(dotenv_path=AI_DIR / ".env")
DEFAULT_KNOWLEDGE_DIR = APP_DIR / "knowledge"
DEFAULT_CHROMA_DIR = APP_DIR / "chroma_db"
RAG_INDEX_METADATA_FILENAME = "rag_index_metadata.json"
RAG_INDEX_MARKER_FILENAME = ".jjikmuk-rag-index"
RAG_INDEX_MARKER_CONTENT = "jjikmuk-rag-index-v3\n"
RAG_INDEX_METADATA_VERSION = 3
DEFAULT_OPENAI_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
MAX_RAG_KNOWLEDGE_DOCUMENTS = 256
MAX_RAG_KNOWLEDGE_FILE_BYTES = 1024 * 1024
MAX_RAG_KNOWLEDGE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_RAG_KNOWLEDGE_RELATIVE_PATH_LENGTH = 512
MAX_RAG_CHUNK_SIZE = 4000
MAX_RAG_CHUNK_OVERLAP = 1000
MAX_RAG_INDEX_CHUNKS = 20_000
MAX_RAG_SEARCH_RESULTS = 5
MAX_RAG_CONTEXT_ENTRY_CHARS = 4000

_vectorstore: Optional[Chroma] = None
_vectorstore_index_dir: Optional[Path] = None
_vectorstore_embedding_endpoint: Optional[str] = None
_last_rag_error: Optional[str] = None
_rebuild_in_progress = False
_rebuild_thread_lock = threading.Lock()
_index_swap_lock = threading.RLock()


def _rag_dependencies_available() -> bool:
    return all([
        Chroma is not None,
        Document is not None,
        OpenAIEmbeddings is not None,
        RecursiveCharacterTextSplitter is not None,
    ])


def get_knowledge_dir() -> Path:
    configured = os.getenv("RAG_DOCS_DIR")
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return APP_DIR.parent / path
    return DEFAULT_KNOWLEDGE_DIR


def get_chroma_dir() -> Path:
    configured = os.getenv("CHROMA_DB_DIR")
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return APP_DIR.parent / path
    return DEFAULT_CHROMA_DIR


def get_rag_index_metadata_path(chroma_dir: Optional[Path] = None) -> Path:
    return (chroma_dir or get_chroma_dir()) / RAG_INDEX_METADATA_FILENAME


def _get_rag_index_marker_path(chroma_dir: Path) -> Path:
    return chroma_dir / RAG_INDEX_MARKER_FILENAME


def _set_last_rag_error(error: Optional[str]) -> None:
    global _last_rag_error
    _last_rag_error = error


def _read_index_metadata(chroma_dir: Optional[Path] = None) -> Dict[str, object]:
    path = get_rag_index_metadata_path(chroma_dir)
    if not path.exists():
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("metadata root must be an object")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _set_last_rag_error(f"RAG index metadata read failed: {exc}")
        return {}


def _write_index_metadata(
    metadata: Dict[str, object],
    chroma_dir: Optional[Path] = None,
) -> None:
    path = get_rag_index_metadata_path(chroma_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_index_marker(chroma_dir: Path) -> None:
    _get_rag_index_marker_path(chroma_dir).write_text(
        RAG_INDEX_MARKER_CONTENT,
        encoding="utf-8",
    )


def _has_valid_index_marker(chroma_dir: Path) -> bool:
    marker = _get_rag_index_marker_path(chroma_dir)
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8") == RAG_INDEX_MARKER_CONTENT
    except OSError:
        return False


def _get_rebuild_lock_path(chroma_dir: Path) -> Path:
    return chroma_dir.parent / f".{chroma_dir.name}.rebuild.lock"


@contextmanager
def _acquire_rebuild_lock(chroma_dir: Path) -> Iterator[None]:
    """Fail fast when another rebuild can mutate the same live index.

    The thread lock handles concurrent calls in this interpreter. On POSIX,
    flock also serializes workers/processes that share the index filesystem.
    The small lock file is intentionally retained so a later process cannot
    race against an unlinked, already-locked inode.
    """
    global _rebuild_in_progress

    if not _rebuild_thread_lock.acquire(blocking=False):
        raise RuntimeError("RAG index rebuild is already in progress.")

    descriptor: Optional[int] = None
    filesystem_lock_acquired = False
    try:
        lock_path = _get_rebuild_lock_path(chroma_dir)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise RuntimeError(f"RAG rebuild lock could not be opened safely: {exc}") from exc

        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("RAG rebuild lock path is not a regular file.")

        if fcntl is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("RAG index rebuild is already in progress.") from exc
            filesystem_lock_acquired = True

        _rebuild_in_progress = True
        yield
    finally:
        _rebuild_in_progress = False
        try:
            if descriptor is not None:
                if filesystem_lock_acquired and fcntl is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except OSError:
                        logger.warning("RAG rebuild filesystem lock release failed.")
                os.close(descriptor)
        finally:
            _rebuild_thread_lock.release()


def _get_index_settings() -> Dict[str, object]:
    embedding_model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    if not embedding_model:
        raise ValueError("EMBEDDING_MODEL must not be empty")

    try:
        chunk_size = int(os.getenv("RAG_CHUNK_SIZE", "500"))
        chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", "80"))
    except ValueError as exc:
        raise ValueError("RAG chunk settings must be integers") from exc

    if chunk_size <= 0:
        raise ValueError("RAG_CHUNK_SIZE must be greater than zero")
    if chunk_size > MAX_RAG_CHUNK_SIZE:
        raise ValueError(
            f"RAG_CHUNK_SIZE must not exceed {MAX_RAG_CHUNK_SIZE}"
        )
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("RAG_CHUNK_OVERLAP must be between zero and chunk_size - 1")
    if chunk_overlap > MAX_RAG_CHUNK_OVERLAP or chunk_overlap > chunk_size // 2:
        raise ValueError(
            "RAG_CHUNK_OVERLAP must not exceed 1000 or half of chunk_size"
        )

    return {
        "embedding_model": embedding_model,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validated_knowledge_root(knowledge_dir: Path) -> Path:
    root = _absolute_path(knowledge_dir)
    if not root.exists():
        return root
    if _path_has_symlink_component(root):
        raise ValueError("RAG knowledge directory must not use symbolic links.")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError("RAG knowledge directory could not be inspected safely.") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("RAG knowledge path must be a directory.")
    return root


def _validated_relative_knowledge_path(path: Path, root: Path) -> PurePosixPath:
    try:
        relative = _absolute_path(path).relative_to(root)
    except ValueError as exc:
        raise ValueError("RAG knowledge document escapes the configured directory.") from exc

    relative_posix = PurePosixPath(relative.as_posix())
    value = relative_posix.as_posix()
    if (
        not value
        or value == "."
        or relative_posix.is_absolute()
        or ".." in relative_posix.parts
        or len(value) > MAX_RAG_KNOWLEDGE_RELATIVE_PATH_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("RAG knowledge document has an invalid relative path.")
    return relative_posix


def _raise_knowledge_walk_error(_error: OSError) -> None:
    raise ValueError("RAG knowledge directory could not be traversed safely.")


def _list_knowledge_documents(knowledge_dir: Optional[Path] = None) -> List[Path]:
    root = _validated_knowledge_root(knowledge_dir or get_knowledge_dir())
    if not root.exists():
        return []

    documents: List[Path] = []
    total_declared_bytes = 0
    try:
        walker = os.walk(root, topdown=True, followlinks=False, onerror=_raise_knowledge_walk_error)
        for directory, directory_names, file_names in walker:
            directory_path = Path(directory)

            for directory_name in directory_names:
                child = directory_path / directory_name
                child_stat = child.lstat()
                if stat.S_ISLNK(child_stat.st_mode):
                    raise ValueError(
                        "RAG knowledge directory must not contain symbolic links."
                    )
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise ValueError(
                        "RAG knowledge directory contains an invalid directory entry."
                    )

            # Downloads are unreviewed source material, not internal policies.
            # Even an accidentally placed .md must not enter the live corpus.
            if directory_path == root:
                directory_names[:] = [name for name in directory_names if name != "raw"]

            for file_name in file_names:
                if not file_name.endswith((".md", ".source.json")):
                    continue
                path = directory_path / file_name
                file_stat = path.lstat()
                if stat.S_ISLNK(file_stat.st_mode):
                    raise ValueError(
                        "RAG knowledge documents must not be symbolic links."
                    )
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ValueError(
                        "RAG knowledge documents must be regular files."
                    )
                _validated_relative_knowledge_path(path, root)
                if file_stat.st_size > MAX_RAG_KNOWLEDGE_FILE_BYTES:
                    raise ValueError(
                        "A RAG knowledge document exceeds the per-file size limit."
                    )
                total_declared_bytes += file_stat.st_size
                if total_declared_bytes > MAX_RAG_KNOWLEDGE_TOTAL_BYTES:
                    raise ValueError(
                        "RAG knowledge documents exceed the total size limit."
                    )
                documents.append(path)
                if len(documents) > MAX_RAG_KNOWLEDGE_DOCUMENTS:
                    raise ValueError(
                        "RAG knowledge documents exceed the document count limit."
                    )
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("RAG knowledge directory could not be traversed safely.") from exc

    return sorted(
        documents,
        key=lambda path: _validated_relative_knowledge_path(path, root).as_posix(),
    )


def _read_knowledge_document_bytes(path: Path, root: Path) -> bytes:
    root = _validated_knowledge_root(root)
    relative = _validated_relative_knowledge_path(path, root)
    if not root.exists():
        raise ValueError("RAG knowledge directory does not exist.")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    close_on_exec_flag = getattr(os, "O_CLOEXEC", 0)
    descriptors: List[int] = []
    try:
        current_descriptor = os.open(
            root,
            directory_flags | nofollow_flag | close_on_exec_flag,
        )
        descriptors.append(current_descriptor)
        for part in relative.parts[:-1]:
            current_descriptor = os.open(
                part,
                directory_flags | nofollow_flag | close_on_exec_flag,
                dir_fd=current_descriptor,
            )
            descriptors.append(current_descriptor)

        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow_flag | close_on_exec_flag,
            dir_fd=current_descriptor,
        )
        descriptors.append(file_descriptor)
        initial_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(initial_stat.st_mode):
            raise ValueError("RAG knowledge documents must be regular files.")
        if initial_stat.st_size > MAX_RAG_KNOWLEDGE_FILE_BYTES:
            raise ValueError(
                "A RAG knowledge document exceeds the per-file size limit."
            )

        content = bytearray()
        while True:
            block = os.read(file_descriptor, min(1024 * 1024, MAX_RAG_KNOWLEDGE_FILE_BYTES + 1))
            if not block:
                break
            content.extend(block)
            if len(content) > MAX_RAG_KNOWLEDGE_FILE_BYTES:
                raise ValueError(
                    "A RAG knowledge document exceeds the per-file size limit."
                )

        final_stat = os.fstat(file_descriptor)
        if (
            initial_stat.st_dev != final_stat.st_dev
            or initial_stat.st_ino != final_stat.st_ino
            or initial_stat.st_size != final_stat.st_size
            or initial_stat.st_mtime_ns != final_stat.st_mtime_ns
            or len(content) != final_stat.st_size
        ):
            raise ValueError("A RAG knowledge document changed while it was being read.")
        return bytes(content)
    except ValueError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise ValueError("RAG knowledge document could not be read safely.") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _decode_knowledge_document(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("RAG knowledge documents must use valid UTF-8.") from exc
    if "\x00" in text:
        raise ValueError("RAG knowledge documents must not contain NUL bytes.")
    return text


def _read_knowledge_document(path: Path, root: Path) -> tuple[bytes, str]:
    content = _read_knowledge_document_bytes(path, root)
    return content, _decode_knowledge_document(content)


def _build_document_manifest(
    knowledge_dir: Optional[Path] = None,
) -> List[Dict[str, str]]:
    root = _validated_knowledge_root(knowledge_dir or get_knowledge_dir())
    manifest: List[Dict[str, str]] = []
    total_read_bytes = 0
    for path in _list_knowledge_documents(root):
        content, document_text = _read_knowledge_document(path, root)
        if path.name.endswith(".source.json"):
            parse_released_excerpt(document_text)
        total_read_bytes += len(content)
        if total_read_bytes > MAX_RAG_KNOWLEDGE_TOTAL_BYTES:
            raise ValueError("RAG knowledge documents exceed the total size limit.")
        manifest.append(
            {
                "path": _validated_relative_knowledge_path(path, root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return manifest


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _metadata_is_complete(metadata: Dict[str, object]) -> bool:
    manifest = metadata.get("document_manifest")
    if not isinstance(manifest, list) or not manifest:
        return False

    valid_manifest = all(
        isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and bool(entry.get("path"))
        and not PurePosixPath(str(entry.get("path"))).is_absolute()
        and ".." not in PurePosixPath(str(entry.get("path"))).parts
        and PurePosixPath(str(entry.get("path"))).as_posix() == entry.get("path")
        and isinstance(entry.get("sha256"), str)
        and len(str(entry.get("sha256"))) == 64
        and all(character in hexdigits for character in str(entry.get("sha256")))
        for entry in manifest
    )
    if not valid_manifest:
        return False

    return bool(
        metadata.get("metadata_version") == RAG_INDEX_METADATA_VERSION
        and isinstance(metadata.get("last_indexed_at"), str)
        and metadata.get("last_indexed_at")
        and _is_positive_int(metadata.get("document_count"))
        and _is_positive_int(metadata.get("indexed_document_count"))
        and _is_positive_int(metadata.get("indexed_chunk_count"))
        and metadata.get("document_count") == len(manifest)
        and metadata.get("indexed_document_count") == len(manifest)
        and isinstance(metadata.get("embedding_model"), str)
        and bool(metadata.get("embedding_model"))
        and _is_positive_int(metadata.get("chunk_size"))
        and _is_non_negative_int(metadata.get("chunk_overlap"))
    )


def _build_index_metadata(
    *,
    document_manifest: List[Dict[str, str]],
    indexed_chunk_count: int,
    settings: Dict[str, object],
) -> Dict[str, object]:
    document_paths = [entry["path"] for entry in document_manifest]
    return {
        "metadata_version": RAG_INDEX_METADATA_VERSION,
        "last_indexed_at": datetime.now().isoformat(timespec="seconds"),
        "document_count": len(document_manifest),
        "indexed_document_count": len(document_manifest),
        "indexed_chunk_count": indexed_chunk_count,
        # Kept for status/API compatibility; the manifest is authoritative.
        "documents": document_paths,
        "document_manifest": document_manifest,
        **settings,
    }


def _metadata_matches_current_inputs(
    metadata: Dict[str, object],
    *,
    document_manifest: List[Dict[str, str]],
    settings: Dict[str, object],
) -> bool:
    return bool(
        _metadata_is_complete(metadata)
        and metadata.get("document_manifest") == document_manifest
        and all(metadata.get(key) == value for key, value in settings.items())
    )


def _embedding_endpoint_decision() -> ExternalEndpointDecision:
    configured = (os.getenv("OPENAI_BASE_URL") or "").strip()
    return validate_external_ai_url(
        configured or DEFAULT_OPENAI_EMBEDDING_BASE_URL,
        require_configured_allowlist=bool(configured),
    )


def get_rag_embedding_endpoint_policy_status() -> Dict[str, object]:
    """Return URL-free embedding endpoint readiness for operator diagnostics."""

    decision = _embedding_endpoint_decision()
    return {
        "endpoint_allowed": decision.allowed,
        "reason": decision.reason,
        "allowlist": external_endpoint_policy_status(),
    }


def _get_embeddings() -> OpenAIEmbeddings:
    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. OpenAI 임베딩을 사용하려면 .env에 OPENAI_API_KEY를 설정해야 합니다.")

    endpoint = _embedding_endpoint_decision()
    if not endpoint.allowed or not endpoint.canonical_url:
        raise RuntimeError(
            "OpenAI embedding endpoint rejected by policy: "
            f"reason={endpoint.reason}"
        )

    from app.infrastructure.budgeted_embedding_transport import (
        BudgetedEmbeddingTransport, BudgetedAsyncEmbeddingTransport,
    )
    from app.infrastructure.external_ai_state import required
    model = str(_get_index_settings()["embedding_model"])
    transport_args = dict(endpoint=endpoint.canonical_url.rstrip('/') + '/embeddings', model=model)
    budgeted = required()
    return OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=endpoint.canonical_url,
        # OpenAI's default HTTP clients follow redirects. Explicit clients keep
        # both sync user searches and any async caller on the same no-redirect,
        # no-environment-proxy transport boundary.
        http_client=httpx.Client(follow_redirects=False, trust_env=False,
            **({'transport': BudgetedEmbeddingTransport(**transport_args)} if budgeted else {})),
        http_async_client=httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            **({'transport': BudgetedAsyncEmbeddingTransport(**transport_args)} if budgeted else {}),
        ),
        openai_proxy=None,
        # At most 2 x 4,000 characters (including six-byte JSON escapes) plus
        # envelope fits the 64 KiB transport limit. Released excerpts <=3,000.
        # Count every batch separately; normal non-budgeted SDK settings stay.
        **(dict(chunk_size=2, max_retries=0, request_timeout=10.0,
                check_embedding_ctx_length=False) if budgeted else {}),
    )


def _load_documents(
    *,
    expected_manifest: Optional[List[Dict[str, str]]] = None,
):
    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")

    knowledge_dir = _validated_knowledge_root(get_knowledge_dir())

    if not knowledge_dir.exists():
        return []

    docs = []
    loaded_manifest: List[Dict[str, str]] = []
    total_read_bytes = 0
    for path in _list_knowledge_documents(knowledge_dir):
        content, text = _read_knowledge_document(path, knowledge_dir)
        total_read_bytes += len(content)
        if total_read_bytes > MAX_RAG_KNOWLEDGE_TOTAL_BYTES:
            raise ValueError("RAG knowledge documents exceed the total size limit.")
        relative_path = _validated_relative_knowledge_path(
            path,
            knowledge_dir,
        ).as_posix()
        digest = hashlib.sha256(content).hexdigest()
        loaded_manifest.append({"path": relative_path, "sha256": digest})
        metadata = {"source": relative_path, "source_sha256": digest}
        if path.name.endswith(".source.json"):
            excerpt = parse_released_excerpt(text)
            text = excerpt.text
            metadata["source_kind"] = "official_excerpt"
        docs.append(
            Document(
                page_content=text,
                metadata=metadata,
            )
        )

    if expected_manifest is not None and loaded_manifest != expected_manifest:
        raise RuntimeError(
            "RAG knowledge documents changed between manifest verification and loading."
        )
    return docs


def _build_rag_splitter(settings: Optional[Dict[str, object]] = None):
    """Build the one splitter used by both indexing and retrieval verification."""
    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")
    effective_settings = settings or _get_index_settings()
    return RecursiveCharacterTextSplitter(
        chunk_size=int(effective_settings["chunk_size"]),
        chunk_overlap=int(effective_settings["chunk_overlap"]),
        separators=["\n\n", "\n", ".", " ", ""],
    )


def _split_rag_documents(documents, settings=None):
    """Reviewed excerpts are bounded semantic units: never split off exceptions."""
    splitter = _build_rag_splitter(settings)
    chunks = []
    for document in documents:
        if getattr(document, "metadata", {}).get("source_kind") == "official_excerpt":
            chunks.append(document)
        else:
            chunks.extend(splitter.split_documents([document]))
    return chunks


def _verified_rag_chunk_keys() -> set[tuple[str, str, str]]:
    """Return chunk identities reproducible from the current trusted documents."""
    manifest = _build_document_manifest()
    settings = _get_index_settings()
    documents = _load_documents(expected_manifest=manifest)
    chunks = _split_rag_documents(documents, settings)
    return {
        (
            str(chunk.metadata["source"]),
            str(chunk.metadata["source_sha256"]),
            hashlib.sha256(
                str(chunk.page_content).encode("utf-8")
            ).hexdigest(),
        )
        for chunk in chunks
        if isinstance(getattr(chunk, "metadata", None), dict)
        and isinstance(chunk.metadata.get("source"), str)
        and isinstance(chunk.metadata.get("source_sha256"), str)
        and isinstance(getattr(chunk, "page_content", None), str)
    }


def _has_chroma_sqlite(chroma_dir: Path) -> bool:
    sqlite_path = chroma_dir / "chroma.sqlite3"
    return sqlite_path.is_file() and not sqlite_path.is_symlink()


def _inspect_index(chroma_dir: Optional[Path] = None) -> Dict[str, object]:
    target = chroma_dir or get_chroma_dir()
    metadata = _read_index_metadata(target)
    metadata_complete = _metadata_is_complete(metadata)
    index_exists = target.is_dir() and not target.is_symlink() and _has_chroma_sqlite(target)
    input_error: Optional[str] = None

    try:
        manifest = _build_document_manifest()
        settings = _get_index_settings()
    except (OSError, ValueError) as exc:
        manifest = []
        settings = {}
        input_error = str(exc)

    inputs_match = bool(
        not input_error
        and _metadata_matches_current_inputs(
            metadata,
            document_manifest=manifest,
            settings=settings,
        )
    )
    index_stale = bool(index_exists and (not metadata_complete or not inputs_match))
    current = bool(index_exists and metadata_complete and inputs_match)

    return {
        "target": target,
        "metadata": metadata,
        "metadata_complete": metadata_complete,
        "index_exists": index_exists,
        "index_stale": index_stale,
        "current": current,
        "document_manifest": manifest,
        "settings": settings,
        "input_error": input_error,
    }


def _require_current_index(chroma_dir: Path) -> Dict[str, object]:
    inspection = _inspect_index(chroma_dir)
    if inspection["input_error"]:
        raise RuntimeError(f"RAG index inputs could not be verified: {inspection['input_error']}")
    if not inspection["index_exists"]:
        raise RuntimeError("RAG index is missing or invalid. Run a verified rebuild before search.")
    if not inspection["metadata_complete"]:
        raise RuntimeError("RAG index metadata is missing or incomplete. Run rebuild before search.")
    if inspection["index_stale"]:
        raise RuntimeError("RAG index is stale. Knowledge documents or index settings changed; rebuild required.")
    return inspection


def _path_has_symlink_component(path: Path) -> bool:
    absolute_path = Path(os.path.abspath(path))
    current = Path(absolute_path.anchor)
    for part in absolute_path.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _is_same_or_ancestor(candidate: Path, protected: Path) -> bool:
    return candidate == protected or candidate in protected.parents


def _validate_rebuild_target(chroma_dir: Path) -> None:
    """Fail closed before any existing directory can be replaced or removed."""
    target = Path(os.path.abspath(chroma_dir))
    if _path_has_symlink_component(target):
        raise RuntimeError("Refusing RAG rebuild because CHROMA_DB_DIR uses a symlink.")
    if target.exists() and not target.is_dir():
        raise RuntimeError("Refusing RAG rebuild because CHROMA_DB_DIR is not a directory.")

    resolved_target = target.resolve(strict=False)
    protected_paths = {
        Path(resolved_target.anchor),
        Path.home().resolve(strict=False),
        AI_DIR.parent.resolve(strict=False),
        AI_DIR.resolve(strict=False),
        APP_DIR.resolve(strict=False),
        DEFAULT_KNOWLEDGE_DIR.resolve(strict=False),
        get_knowledge_dir().resolve(strict=False),
    }
    if any(_is_same_or_ancestor(resolved_target, path) for path in protected_paths):
        raise RuntimeError(
            "Refusing RAG rebuild because CHROMA_DB_DIR targets a protected directory or its ancestor."
        )

    is_default_target = resolved_target == DEFAULT_CHROMA_DIR.resolve(strict=False)
    if target.exists():
        try:
            nonempty = next(target.iterdir(), None) is not None
        except OSError as exc:
            raise RuntimeError(f"Unable to inspect CHROMA_DB_DIR safely: {exc}") from exc
        if nonempty and not is_default_target and not _has_valid_index_marker(target):
            raise RuntimeError(
                "Refusing to replace a nonempty custom CHROMA_DB_DIR without the RAG index marker."
            )


def _validate_loaded_vectorstore(
    vectorstore: Chroma,
    *,
    expected_chunk_count: int,
) -> None:
    try:
        collection = getattr(vectorstore, "_collection", None)
        if collection is None or not hasattr(collection, "count"):
            raise RuntimeError("RAG collection does not expose a verifiable chunk count.")
        actual_chunk_count = int(collection.count())
    except Exception as exc:
        raise RuntimeError(f"RAG collection validation failed: {exc}") from exc

    if actual_chunk_count <= 0 or actual_chunk_count != expected_chunk_count:
        raise RuntimeError(
            "RAG collection chunk count does not match its metadata "
            f"(expected {expected_chunk_count}, got {actual_chunk_count})."
        )


def _load_vectorstore_at(chroma_dir: Path) -> Chroma:
    inspection = _require_current_index(chroma_dir)
    metadata = inspection["metadata"]
    vectorstore = Chroma(
        persist_directory=str(chroma_dir),
        embedding_function=_get_embeddings(),
    )
    _validate_loaded_vectorstore(
        vectorstore,
        expected_chunk_count=int(metadata["indexed_chunk_count"]),
    )
    return vectorstore


def _remove_generated_directory(path: Path, *, parent: Path, prefix: str) -> None:
    if not path.exists():
        return
    if path.is_symlink() or path.parent != parent or not path.name.startswith(prefix):
        raise RuntimeError(f"Refusing to remove unverified RAG build artifact: {path}")
    shutil.rmtree(path)


def _activate_staged_index_locked(staging_dir: Path, chroma_dir: Path) -> Chroma:
    """Swap a completed index into place, restoring the old one on load failure."""
    parent = chroma_dir.parent
    backup_prefix = f".{chroma_dir.name}.backup-"
    backup_dir = parent / f"{backup_prefix}{uuid.uuid4().hex}"
    had_existing = chroma_dir.exists()

    if had_existing:
        os.replace(chroma_dir, backup_dir)

    try:
        os.replace(staging_dir, chroma_dir)
        vectorstore = _load_vectorstore_at(chroma_dir)
    except Exception:
        rollback_error: Optional[Exception] = None
        try:
            if chroma_dir.exists() and not staging_dir.exists():
                os.replace(chroma_dir, staging_dir)
            if had_existing and backup_dir.exists():
                os.replace(backup_dir, chroma_dir)
        except Exception as exc:  # Preserve both paths for manual recovery.
            rollback_error = exc

        if rollback_error is not None:
            raise RuntimeError(
                f"RAG index activation and automatic rollback both failed: {rollback_error}"
            ) from rollback_error
        raise

    if had_existing and backup_dir.exists():
        try:
            _remove_generated_directory(
                backup_dir,
                parent=parent,
                prefix=backup_prefix,
            )
        except Exception as exc:
            logger.warning("Old RAG index backup cleanup failed: %s", exc)
    return vectorstore


def _activate_staged_index(staging_dir: Path, chroma_dir: Path) -> Chroma:
    """Keep in-process searches outside the brief live/backup swap window."""
    with _index_swap_lock:
        return _activate_staged_index_locked(staging_dir, chroma_dir)


def _build_staged_index(
    *,
    chroma_dir: Path,
    chunks: List[object],
    document_manifest: List[Dict[str, str]],
    settings: Dict[str, object],
) -> Path:
    parent = chroma_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    build_prefix = f".{chroma_dir.name}.build-"
    staging_dir = Path(tempfile.mkdtemp(prefix=build_prefix, dir=str(parent)))

    try:
        staged_vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=_get_embeddings(),
            persist_directory=str(staging_dir),
        )
        _write_index_metadata(
            _build_index_metadata(
                document_manifest=document_manifest,
                indexed_chunk_count=len(chunks),
                settings=settings,
            ),
            staging_dir,
        )
        _write_index_marker(staging_dir)
        _require_current_index(staging_dir)
        _validate_loaded_vectorstore(
            staged_vectorstore,
            expected_chunk_count=len(chunks),
        )
        return staging_dir
    except Exception:
        _remove_generated_directory(
            staging_dir,
            parent=parent,
            prefix=build_prefix,
        )
        raise


def _build_vectorstore_impl(force_rebuild: bool = False) -> Chroma:
    global _vectorstore, _vectorstore_index_dir, _vectorstore_embedding_endpoint

    if not _rag_dependencies_available():
        message = f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}"
        _set_last_rag_error(message)
        raise RuntimeError(message)

    chroma_dir = get_chroma_dir()
    canonical_chroma_dir = chroma_dir.resolve(strict=False)

    try:
        endpoint = _embedding_endpoint_decision()
        if not endpoint.allowed or not endpoint.canonical_url:
            raise RuntimeError(
                "OpenAI embedding endpoint rejected by policy: "
                f"reason={endpoint.reason}"
            )
        canonical_embedding_endpoint = endpoint.canonical_url

        if _vectorstore is not None and not force_rebuild:
            inspection = _require_current_index(chroma_dir)
            if (
                _vectorstore_index_dir != canonical_chroma_dir
                or _vectorstore_embedding_endpoint
                != canonical_embedding_endpoint
            ):
                reset_rag_cache()
            else:
                _validate_loaded_vectorstore(
                    _vectorstore,
                    expected_chunk_count=int(inspection["metadata"]["indexed_chunk_count"]),
                )
                _set_last_rag_error(None)
                return _vectorstore

        if chroma_dir.exists() and not force_rebuild:
            _vectorstore = _load_vectorstore_at(chroma_dir)
            _vectorstore_index_dir = canonical_chroma_dir
            _vectorstore_embedding_endpoint = canonical_embedding_endpoint
            _set_last_rag_error(None)
            return _vectorstore

        _validate_rebuild_target(chroma_dir)
        reset_rag_cache()

        settings = _get_index_settings()
        document_manifest = _build_document_manifest()
        documents = _load_documents(expected_manifest=document_manifest)
        if not documents or not document_manifest:
            raise RuntimeError(
                "RAG 문서가 없습니다. 내부 .md 또는 승인된 .source.json 문서가 필요합니다."
            )

        chunks = _split_rag_documents(documents, settings)
        if not chunks:
            raise RuntimeError("RAG chunk 생성 실패")
        if len(chunks) > MAX_RAG_INDEX_CHUNKS:
            raise RuntimeError(
                "RAG chunk count exceeds the verified index build limit."
            )

        staging_dir = _build_staged_index(
            chroma_dir=chroma_dir,
            chunks=chunks,
            document_manifest=document_manifest,
            settings=settings,
        )
        try:
            _vectorstore = _activate_staged_index(staging_dir, chroma_dir)
        finally:
            if staging_dir.exists():
                _remove_generated_directory(
                    staging_dir,
                    parent=chroma_dir.parent,
                    prefix=f".{chroma_dir.name}.build-",
                )
        _vectorstore_index_dir = canonical_chroma_dir
        _vectorstore_embedding_endpoint = canonical_embedding_endpoint
        _set_last_rag_error(None)
        return _vectorstore
    except Exception as exc:
        reset_rag_cache()
        _set_last_rag_error(
            f"RAG index unavailable: error_type={type(exc).__name__}"
        )
        raise


def build_vectorstore(force_rebuild: bool = False) -> Chroma:
    chroma_dir = get_chroma_dir()
    rebuild_required = force_rebuild or not chroma_dir.exists()
    if not rebuild_required:
        return _build_vectorstore_impl(force_rebuild=False)

    # Validate before creating a sibling lock file. The implementation repeats
    # this validation immediately before staging as defense in depth.
    _validate_rebuild_target(chroma_dir)
    with _acquire_rebuild_lock(chroma_dir):
        return _build_vectorstore_impl(force_rebuild=force_rebuild)


def reset_rag_cache() -> None:
    global _vectorstore, _vectorstore_index_dir, _vectorstore_embedding_endpoint
    with _index_swap_lock:
        _vectorstore = None
        _vectorstore_index_dir = None
        _vectorstore_embedding_endpoint = None


def rebuild_rag_index() -> Dict[str, object]:
    build_vectorstore(force_rebuild=True)
    return get_rag_status(verify_retriever=True)


def _indexed_chunk_count_from_vectorstore() -> Optional[int]:
    if _vectorstore is None:
        return None

    try:
        collection = getattr(_vectorstore, "_collection", None)
        if collection is not None and hasattr(collection, "count"):
            return int(collection.count())
    except Exception as exc:
        _set_last_rag_error(f"RAG collection count failed: {exc}")

    return None


def get_rag_status(verify_retriever: bool = False) -> Dict[str, object]:
    knowledge_dir = get_knowledge_dir()
    chroma_dir = get_chroma_dir()
    document_input_error: Optional[str] = None
    try:
        documents = _list_knowledge_documents(knowledge_dir)
    except (OSError, ValueError) as exc:
        documents = []
        document_input_error = str(exc)
    document_names = [path.relative_to(knowledge_dir).as_posix() for path in documents]
    inspection = _inspect_index(chroma_dir)
    metadata = inspection["metadata"]
    index_exists = bool(inspection["index_exists"])
    metadata_complete = bool(inspection["metadata_complete"])
    index_stale = bool(inspection["index_stale"])
    input_error = inspection["input_error"] or document_input_error
    indexed_document_count = metadata.get("indexed_document_count")
    indexed_chunk_count = metadata.get("indexed_chunk_count")
    indexed_documents = metadata.get("documents") or []
    endpoint_status = get_rag_embedding_endpoint_policy_status()
    retriever_ready = (
        _rag_dependencies_available()
        and bool(inspection["current"])
        and bool(os.getenv("OPENAI_API_KEY"))
        and bool(endpoint_status["endpoint_allowed"])
    )

    if verify_retriever and retriever_ready:
        try:
            vectorstore = build_vectorstore(force_rebuild=False)
            collection = getattr(vectorstore, "_collection", None)
            if collection is not None and hasattr(collection, "count"):
                indexed_chunk_count = int(collection.count())
                retriever_ready = indexed_chunk_count > 0
            _set_last_rag_error(None)
        except Exception as exc:
            retriever_ready = False
            _set_last_rag_error(
                "RAG retriever verification failed: "
                f"error_type={type(exc).__name__}"
            )

    if indexed_chunk_count is None:
        indexed_chunk_count = _indexed_chunk_count_from_vectorstore()

    if not _rag_dependencies_available():
        status_reason = f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}"
    elif not knowledge_dir.exists():
        status_reason = "RAG knowledge directory does not exist."
    elif input_error:
        status_reason = f"RAG index inputs could not be verified: {input_error}"
    elif not documents:
        status_reason = "RAG documents were not found."
    elif not index_exists:
        status_reason = "RAG index does not exist. Run rebuild before using RAG search."
    elif not metadata_complete:
        status_reason = "RAG index metadata is missing or incomplete. Run rebuild before using RAG search."
    elif index_stale:
        status_reason = "RAG index is stale. Knowledge documents or index settings changed after the last rebuild."
    elif not os.getenv("OPENAI_API_KEY"):
        status_reason = "OPENAI_API_KEY is not set, so RAG embeddings/search cannot run."
    elif not endpoint_status["endpoint_allowed"]:
        status_reason = (
            "RAG embedding endpoint is blocked by the external endpoint policy: "
            f"reason={endpoint_status['reason']}"
        )
    elif indexed_chunk_count is not None and int(indexed_chunk_count) <= 0:
        status_reason = "RAG index exists but contains no chunks."
    elif retriever_ready:
        status_reason = "RAG retriever is ready."
    else:
        status_reason = "RAG retriever is not ready."

    return {
        "dependencies_available": _rag_dependencies_available(),
        "import_error": str(_RAG_IMPORT_ERROR) if _RAG_IMPORT_ERROR else None,
        "knowledge_dir": str(knowledge_dir),
        "knowledge_dir_exists": knowledge_dir.exists(),
        "documents_found": knowledge_dir.exists() and bool(documents),
        "document_count": len(documents),
        "documents": document_names,
        "chroma_dir": str(chroma_dir),
        "chroma_dir_exists": chroma_dir.exists(),
        "index_exists": index_exists,
        "metadata_complete": metadata_complete,
        "index_stale": index_stale,
        "rebuild_in_progress": _rebuild_in_progress,
        "retriever_ready": retriever_ready,
        "status_reason": status_reason,
        "last_indexed_at": metadata.get("last_indexed_at"),
        "indexed_document_count": indexed_document_count,
        "indexed_chunk_count": indexed_chunk_count,
        "indexed_documents": indexed_documents,
        "metadata_path": str(get_rag_index_metadata_path()),
        "metadata_exists": get_rag_index_metadata_path().exists(),
        "rag_last_error": _last_rag_error,
        "embedding_model": inspection["settings"].get(
            "embedding_model",
            os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        ),
        "openai_api_key_set": bool(os.getenv("OPENAI_API_KEY")),
        "embedding_endpoint_policy": endpoint_status,
        "loadable": knowledge_dir.exists() and bool(documents) and not input_error,
    }


def _build_query(
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
) -> str:
    parts = [message]

    if profile:
        allergies = profile.resolved_allergies()
        diets = profile.resolved_special_diets()

        if allergies:
            parts.append("사용자 알레르기: " + ", ".join(allergies))
        if diets:
            parts.append("사용자 식이습관: " + ", ".join(diets))

    if product and not product.facts_verified:
        parts.append("제품 정보: DB 미검증(요청에 포함된 제품 사실은 검색 근거에서 제외)")
    elif product:
        if product.product_name:
            parts.append("제품명: " + product.product_name)

        provenance = product.label_provenance
        parts.append(
            f"제품 표시 계보: {effective_label_lineage_status(product)}"
        )
        parts.append(
            f"제품 표시 freshness 운영 상태: {label_freshness_status(product)}"
        )
        if provenance.label_observed_at is not None:
            parts.append(
                "제품 표시 관찰일: "
                + provenance.label_observed_at.isoformat()
                + " (최신성 증명 아님)"
            )

        ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
        if ingredients:
            parts.append("원재료명: " + ingredients)

        allergies = product.resolved_allergies()
        if allergies:
            parts.append("제품 알레르기 성분: " + ", ".join(allergies))
        if not label_provenance_supports(
            product,
            ("ingredients", "allergens"),
        ):
            parts.append(
                "표시 계보 제약: "
                + label_provenance_limitation_reason(
                    product,
                    ("ingredients", "allergens"),
                )
                + " 원재료·알레르기 성분이 없다는 "
                "부재/무충돌 주장을 하지 않음"
            )

        if product.nutrition:
            nutrition_text = ", ".join(
                f"{key}: {value}" for key, value in product.nutrition.items()
            )
            parts.append("영양정보: " + nutrition_text)
            parts.append("영양 수치 기준: " + nutrition_basis_label(product))
            parts.append(
                "해석 제약: 자체 임계치를 적용하거나 높음·낮음·식이 적합성을 "
                "추론하지 않음"
            )

    return "\n".join(parts)


def _internal_policy_source_identifier(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if (
        not value
        or len(value) > MAX_RAG_KNOWLEDGE_RELATIVE_PATH_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    source_path = PurePosixPath(value)
    if (
        source_path.is_absolute()
        or ".." in source_path.parts
        or source_path.as_posix() != value
        or source_path.suffix != ".md"
    ):
        return None
    return f"internal-policy:{value}"


def _official_reference_from_file(raw_source: str, digest: str) -> dict[str, str]:
    """Never trust a vector provider's citation/page/URL metadata."""
    relative = PurePosixPath(raw_source)
    if (
        relative.is_absolute() or ".." in relative.parts
        or relative.as_posix() != raw_source
        or len(raw_source) > MAX_RAG_KNOWLEDGE_RELATIVE_PATH_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in raw_source)
    ):
        raise ValueError("Official source relative path is invalid.")
    root = _validated_knowledge_root(get_knowledge_dir())
    path = root / raw_source
    _validated_relative_knowledge_path(path, root)
    if not raw_source.endswith(".source.json"):
        raise ValueError("Official source extension is invalid.")
    content, text = _read_knowledge_document(path, root)
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Official source changed after retrieval verification.")
    excerpt = parse_released_excerpt(text)
    return {
        **excerpt_reference(excerpt), "excerpt_content": excerpt.text,
        "topics": ",".join(excerpt.topics),
    }


def local_official_entries(source_topics: set[str]) -> List[Dict[str, str]]:
    """Approved excerpts whose reviewed topics match, read from local files only.

    No embedding, index or provider call: this serves a recognized topic when
    external processing is not permitted. Callers still verify each entry.
    """
    if not source_topics:
        return []
    entries: List[Dict[str, str]] = []
    manifest = _build_document_manifest()
    for document in _load_documents(expected_manifest=manifest):
        if document.metadata.get("source_kind") != "official_excerpt":
            continue
        raw_source = document.metadata["source"]
        digest = document.metadata["source_sha256"]
        reference = _official_reference_from_file(raw_source, digest)
        if not source_topics.intersection(filter(None, reference["topics"].split(","))):
            continue
        text = " ".join(reference["excerpt_content"].split())
        entries.append({
            "source": reference["source"], "content": text, "source_file": raw_source,
            "source_sha256": digest, "text": f"[{reference['citation']}] {text}",
        })
    return entries


def official_citation_from_entry(entry: dict[str, str]) -> Optional[str]:
    """Recheck local approval at final response assembly, not just retrieval."""
    reference = verified_official_reference(entry)
    return reference["citation"] if reference else None


def verified_official_reference(entry: dict[str, str]) -> Optional[dict[str, str]]:
    """Return only content and scope re-read from the currently approved file."""
    try:
        reference = _official_reference_from_file(entry["source_file"], entry["source_sha256"])
        if (
            entry.get("source") != reference["source"]
            or entry.get("content") != " ".join(reference["excerpt_content"].split())
        ):
            return None
        return reference
    except (KeyError, TypeError, ValueError, OSError):
        return None


def get_rag_context_entries(
    message: str,
    profile: Optional[Profile] = None,
    product: Optional[Product] = None,
    top_k: int = 3,
    *,
    official_only: bool = False,
    source_topics: Optional[set[str]] = None,
) -> List[Dict[str, str]]:
    # Similarity search uses OpenAIEmbeddings for the user/profile/product query.
    # Do not even build that query unless both the operator gate and this exact
    # signed request permit external processing. Administrative index rebuilds
    # are intentionally governed separately and do not pass through this path.
    if not external_ai_processing_enabled() or not external_ai_request_allowed():
        return []

    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or not 1 <= top_k <= MAX_RAG_SEARCH_RESULTS
    ):
        _set_last_rag_error("RAG context unavailable: invalid_search_limit")
        return []

    endpoint = _embedding_endpoint_decision()
    if not endpoint.allowed:
        _set_last_rag_error(
            "RAG context unavailable: embedding_endpoint_policy="
            f"{endpoint.reason}"
        )
        logger.warning(
            "RAG context unavailable: stage=endpoint reason=%s",
            endpoint.reason,
        )
        return []

    failure_stage = "index"
    try:
        with _index_swap_lock:
            # A question consents to query processing, not bulk document upload.
            # Provisioning/rebuild remains an explicit administrative action.
            try:
                _require_current_index(get_chroma_dir())
            except Exception:
                reset_rag_cache()
                raise
            vectorstore = build_vectorstore()
            failure_stage = "search"
            query = _build_query(message=message, profile=profile, product=product)

            if official_only:
                search_filter = {"source_kind": "official_excerpt"}
                if source_topics:
                    # Resolve scope from current approved local files, not
                    # provider metadata. Existing index source paths suffice.
                    eligible_sources = [
                        entry["path"] for entry in _build_document_manifest()
                        if entry["path"].endswith(".source.json") and source_topics.intersection(
                            _official_reference_from_file(entry["path"], entry["sha256"])["topics"].split(",")
                        )
                    ]
                    if not eligible_sources:
                        return []
                    search_filter = {"$and": [
                        {"source_kind": "official_excerpt"}, {"source": {"$in": eligible_sources}},
                    ]}
                # A failed/timeout search can still have disclosed its query.
                if not persistent_state_required():
                    mark_external_ai_used("rag")
                docs = vectorstore.similarity_search(
                    query, k=top_k, filter=search_filter,
                )
            else:
                if not persistent_state_required():
                    mark_external_ai_used("rag")
                docs = vectorstore.similarity_search(query, k=top_k)
            verified_chunk_keys = _verified_rag_chunk_keys()

            results = []
            retrieval_integrity_failed = False
            for doc in docs:
                metadata = getattr(doc, "metadata", {})
                if not isinstance(metadata, dict):
                    retrieval_integrity_failed = True
                    break
                raw_source = metadata.get("source")
                source = _internal_policy_source_identifier(raw_source)
                official = isinstance(raw_source, str) and raw_source.endswith(".source.json")
                page_content = getattr(doc, "page_content", None)
                if (source is None and not official) or not isinstance(page_content, str):
                    retrieval_integrity_failed = True
                    break
                text = " ".join(page_content.split())
                if not text or len(text) > MAX_RAG_CONTEXT_ENTRY_CHARS:
                    retrieval_integrity_failed = True
                    break
                source_sha256 = metadata.get("source_sha256")
                chunk_key = (
                    str(raw_source),
                    str(source_sha256),
                    hashlib.sha256(page_content.encode("utf-8")).hexdigest(),
                )
                if chunk_key not in verified_chunk_keys:
                    retrieval_integrity_failed = True
                    break
                if official:
                    reference = _official_reference_from_file(raw_source, str(source_sha256))
                    if page_content != reference["excerpt_content"]:
                        retrieval_integrity_failed = True
                        break
                    results.append({
                        "source": reference["source"], "content": text,
                        "source_file": raw_source, "source_sha256": str(source_sha256),
                        "text": f"[{reference['citation']}] {text}",
                    })
                    continue
                results.append({
                    "source": source,
                    "content": text,
                    "text": f"[{source}] {text}",
                })

            if retrieval_integrity_failed:
                _set_last_rag_error(
                    "RAG context unavailable: retrieval_integrity_check_failed"
                )
                logger.warning(
                    "RAG context unavailable: stage=retrieval_integrity"
                )
                return []

        return results

    except Exception as exc:
        # Vector/embedding provider errors may echo the query, which includes
        # user health context. Keep only a non-sensitive type code in memory/logs.
        error_type = type(exc).__name__
        if failure_stage == "index":
            # Status reasons are generated from local manifest/metadata booleans,
            # so they remain actionable without copying the provider exception.
            safe_reason = str(get_rag_status().get("status_reason") or "").strip()
            error_summary = safe_reason or f"error_type={error_type}"
        else:
            error_summary = f"error_type={error_type}"
        _set_last_rag_error(f"RAG context unavailable: {error_summary}")
        logger.warning(
            "RAG context unavailable: stage=%s error_type=%s",
            failure_stage,
            error_type,
        )
        return []


def get_rag_context(
    message: str,
    profile: Optional[Profile] = None,
    product: Optional[Product] = None,
    top_k: int = 3,
) -> List[str]:
    return [
        item["text"]
        for item in get_rag_context_entries(
            message=message,
            profile=profile,
            product=product,
            top_k=top_k,
        )
    ]
