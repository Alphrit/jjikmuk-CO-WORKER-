"""Opt-in deployment entrypoint: prepare the RAG index before serving requests."""

import argparse

from app.infrastructure.rag import get_rag_status, rebuild_rag_index


def prepare_rag(*, rebuild: bool = False) -> None:
    status = get_rag_status()
    if status.get("retriever_ready") and not status.get("index_stale"):
        return
    if rebuild:
        # Explicit deployment authorization only; ordinary API startup never
        # silently sends documents to an external embedding service.
        status = rebuild_rag_index()
    if not status.get("retriever_ready") or status.get("index_stale"):
        raise RuntimeError("RAG index is not ready; provision it or explicitly use --rebuild-rag")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-rag", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    prepare_rag(rebuild=args.rebuild_rag)
    import uvicorn
    uvicorn.run("app.api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
