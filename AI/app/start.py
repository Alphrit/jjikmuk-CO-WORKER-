"""Opt-in deployment entrypoint: prepare the RAG index before serving requests."""

import argparse
import logging
import os
import threading

from app.infrastructure.rag import get_rag_status, rebuild_rag_index

logger = logging.getLogger(__name__)


def rebuild_stale_rag_in_background() -> threading.Thread | None:
    """At API startup, rebuild a missing or stale index when external AI is on.

    The operator already allows OpenAI by enabling external AI, so a plain
    restart picks up new knowledge documents. Requests are served meanwhile;
    retrieval stays off until the index is current, as before.
    """
    from app.infrastructure.llm import external_ai_processing_enabled
    if (os.getenv("FOOD_CHATBOT_RAG_AUTO_REBUILD", "true").strip().lower() in {"0", "false", "no", "off"}
            or os.getenv("FOOD_CHATBOT_ENV", "").strip().lower() == "test"
            or not external_ai_processing_enabled() or not os.getenv("OPENAI_API_KEY")):
        return None
    status = get_rag_status()
    if status.get("retriever_ready") and not status.get("index_stale"):
        return None

    def run():
        try:
            result = rebuild_rag_index()
            logger.info("RAG index rebuilt at startup: ready=%s stale=%s",
                        result.get("retriever_ready"), result.get("index_stale"))
        except Exception as exc:
            logger.warning("RAG index rebuild at startup failed: error_type=%s", type(exc).__name__)

    thread = threading.Thread(target=run, name="rag-startup-rebuild", daemon=True)
    thread.start()
    return thread


def prepare_rag(*, rebuild: bool = False) -> None:
    status = get_rag_status()
    if status.get("retriever_ready") and not status.get("index_stale"):
        return
    if rebuild:
        # Fail-fast entrypoint; the API itself rebuilds in the background
        # only when external AI is enabled (rebuild_stale_rag_in_background).
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
