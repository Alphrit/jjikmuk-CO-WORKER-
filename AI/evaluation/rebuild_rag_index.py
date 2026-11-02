import json
import sys
from datetime import datetime
from pathlib import Path


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from app.infrastructure.rag import get_rag_status, rebuild_rag_index  # noqa: E402


RESULTS_DIR = Path(__file__).with_name("results")


def main() -> int:
    error = None
    try:
        rag_status = rebuild_rag_index()
    except Exception as exc:
        error = str(exc)
        rag_status = get_rag_status()

    result = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "RAG index rebuild",
        "ok": error is None,
        "error": error,
        "rag": rag_status,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"rag_rebuild_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    rag = result["rag"]
    print(f"documents_found: {rag.get('documents_found')}")
    print(f"document_count: {rag.get('document_count')}")
    print(f"index_exists: {rag.get('index_exists')}")
    print(f"retriever_ready: {rag.get('retriever_ready')}")
    print(f"indexed_document_count: {rag.get('indexed_document_count')}")
    print(f"indexed_chunk_count: {rag.get('indexed_chunk_count')}")
    print(f"last_indexed_at: {rag.get('last_indexed_at')}")
    if rag.get("rag_last_error"):
        print(f"rag_last_error: {rag.get('rag_last_error')}")
    print(f"saved_result: {output_path}")

    return 0 if result["ok"] and rag.get("index_exists") and rag.get("retriever_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
