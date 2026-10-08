"""Opt-in, request-local metadata for the developer preview (never prompts/text)."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter


_events: ContextVar[list | None] = ContextVar("llm_preview_events", default=None)
_purpose: ContextVar[str] = ContextVar("llm_preview_purpose", default="unspecified")


@contextmanager
def capture_llm_calls():
    events = []
    token = _events.set(events)
    try:
        yield events
    finally:
        _events.reset(token)


@contextmanager
def llm_purpose(purpose: str):
    token = _purpose.set(purpose)
    try:
        yield
    finally:
        _purpose.reset(token)


def observe_llm_call(callback, *, provider: str):
    events = _events.get()
    if events is None:
        return callback()
    started = perf_counter()
    result = None
    try:
        result = callback()
        return result
    finally:
        events.append({
            "purpose": _purpose.get(),
            "provider": provider,
            # This is a client invocation, not proof of provider receipt/billing.
            "text_returned": isinstance(result, str) and bool(result.strip()),
            "elapsed_ms": round((perf_counter() - started) * 1000),
        })
