from __future__ import annotations

import logging
import json
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from starlette.responses import JSONResponse

from app.infrastructure.external_ai_consent import (
    reset_external_ai_request_context,
    set_external_ai_request_context,
    verify_external_ai_consent,
)
from app.infrastructure.llm import external_ai_processing_enabled


logger = logging.getLogger(__name__)


DEFAULT_MAX_CHAT_BODY_BYTES = 1_048_576
MAX_CONFIGURABLE_CHAT_BODY_BYTES = 10_485_760
DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
MAX_RATE_LIMIT_REQUESTS = 10_000
MAX_RATE_LIMIT_WINDOW_SECONDS = 3_600
MAX_TRACKED_RATE_LIMIT_KEYS = 10_000
# Private request-scope key carrying the exact bytes already consumed by this
# middleware. The authenticated endpoint uses it for the body-bound identity
# proof; it is never serialized, logged, or retained after the request scope.
EXACT_REQUEST_BODY_SCOPE_KEY = "food_chatbot_exact_request_body"

_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}


def secure_text_matches(provided: str, expected: str) -> bool:
    """Compare UTF-8 secrets in constant time without rejecting non-ASCII text."""

    try:
        return secrets.compare_digest(
            provided.encode("utf-8"),
            expected.encode("utf-8"),
        )
    except (AttributeError, UnicodeEncodeError):
        return False


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value in _TRUE_ENV_VALUES:
        return True
    if value in _FALSE_ENV_VALUES:
        return False

    logger.warning("Invalid boolean environment variable %s; using false.", name)
    return False


def _bounded_positive_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer environment variable %s; using %s.", name, default)
        return default
    if value < minimum or value > maximum:
        logger.warning("Out-of-range environment variable %s; using %s.", name, default)
        return default
    return value


def chat_api_token_configured() -> bool:
    return bool((os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip())


def max_chat_body_bytes() -> int:
    return _bounded_positive_int(
        "FOOD_CHATBOT_MAX_REQUEST_BYTES",
        default=DEFAULT_MAX_CHAT_BODY_BYTES,
        minimum=1_024,
        maximum=MAX_CONFIGURABLE_CHAT_BODY_BYTES,
    )


def chat_rate_limit_enabled() -> bool:
    return _env_flag("FOOD_CHATBOT_ENABLE_RATE_LIMIT", default=False)


def chat_rate_limit_requests() -> int:
    return _bounded_positive_int(
        "FOOD_CHATBOT_RATE_LIMIT_REQUESTS",
        default=DEFAULT_RATE_LIMIT_REQUESTS,
        minimum=1,
        maximum=MAX_RATE_LIMIT_REQUESTS,
    )


def chat_rate_limit_window_seconds() -> int:
    return _bounded_positive_int(
        "FOOD_CHATBOT_RATE_LIMIT_WINDOW_SECONDS",
        default=DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
        minimum=1,
        maximum=MAX_RATE_LIMIT_WINDOW_SECONDS,
    )


def trust_proxy_headers() -> bool:
    return _env_flag("FOOD_CHATBOT_TRUST_PROXY_HEADERS", default=False)


@dataclass
class _Window:
    started_at: float
    count: int


class FixedWindowRateLimiter:
    """Small single-process limiter for the expensive public chat endpoint.

    This is deliberately bounded so spoofed client keys cannot grow process memory
    without limit. Multi-worker deployments still need a shared gateway/Redis
    limiter; this class is only a local safety net.
    """

    def __init__(self, *, max_keys: int = MAX_TRACKED_RATE_LIMIT_KEYS) -> None:
        self.max_keys = max_keys
        self._windows: Dict[str, _Window] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()

    def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int]:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            window = self._windows.get(key)
            if window is None or current_time - window.started_at >= window_seconds:
                if window is None and len(self._windows) >= self.max_keys:
                    self._prune(current_time, window_seconds)
                if window is None and len(self._windows) >= self.max_keys:
                    oldest_key = min(
                        self._windows,
                        key=lambda item: self._windows[item].started_at,
                    )
                    self._windows.pop(oldest_key, None)
                self._windows[key] = _Window(started_at=current_time, count=1)
                return True, window_seconds

            retry_after = max(
                1,
                math.ceil(window_seconds - (current_time - window.started_at)),
            )
            if window.count >= limit:
                return False, retry_after
            window.count += 1
            return True, retry_after

    def _prune(self, now: float, window_seconds: int) -> None:
        expired = [
            key
            for key, window in self._windows.items()
            if now - window.started_at >= window_seconds
        ]
        for key in expired:
            self._windows.pop(key, None)


chat_rate_limiter = FixedWindowRateLimiter()


def _headers(scope: Dict[str, Any]) -> Dict[str, str]:
    decoded: Dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        decoded[name] = raw_value.decode("latin-1")
    return decoded


def _provided_api_token(headers: Dict[str, str]) -> str:
    explicit_token = headers.get("x-food-chatbot-token", "").strip()
    if explicit_token:
        return explicit_token
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _client_key(scope: Dict[str, Any], headers: Dict[str, str]) -> str:
    if trust_proxy_headers():
        forwarded_for = headers.get("x-forwarded-for", "")
        forwarded_client = forwarded_for.split(",", 1)[0].strip()
        if forwarded_client:
            return forwarded_client[:256]

    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])[:256]
    return "unknown-client"


async def _send_json(
    send: Callable[[Dict[str, Any]], Awaitable[None]],
    *,
    status_code: int,
    detail: str,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    response = JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers,
    )
    await response({"type": "http"}, None, send)


class ChatRequestGuardMiddleware:
    """Protect chat and authenticated transcript writes before body parsing."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        path_parts = path.split("/")
        is_chat_request = method == "POST" and path == "/chat"
        is_conversation_write = (
            path == "/conversations" or path.startswith("/conversations/")
        ) and method in {"POST", "PATCH", "DELETE"}
        is_product_report = (
            method == "POST"
            and len(path_parts) == 4
            and path_parts[1] == "products"
            and bool(path_parts[2])
            and path_parts[3] == "report"
        )
        if not (is_chat_request or is_conversation_write or is_product_report):
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        expected_token = (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip()
        conversations_enabled = _env_flag(
            "FOOD_CHATBOT_ENABLE_CONVERSATIONS",
            default=False,
        )
        if is_conversation_write and conversations_enabled and not expected_token:
            await _send_json(
                send,
                status_code=503,
                detail="Conversation storage requires a configured chatbot API token.",
            )
            return
        if expected_token:
            provided_token = _provided_api_token(headers)
            if not provided_token or not secure_text_matches(
                provided_token, expected_token
            ):
                await _send_json(
                    send,
                    status_code=401,
                    detail="A valid chatbot API token is required.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return

        if is_chat_request and chat_rate_limit_enabled():
            allowed, retry_after = chat_rate_limiter.allow(
                _client_key(scope, headers),
                limit=chat_rate_limit_requests(),
                window_seconds=chat_rate_limit_window_seconds(),
            )
            if not allowed:
                await _send_json(
                    send,
                    status_code=429,
                    detail="Chat request rate limit exceeded.",
                    headers={"Retry-After": str(retry_after)},
                )
                return

        configured_max_bytes = max_chat_body_bytes()
        raw_content_length = headers.get("content-length")
        if raw_content_length:
            try:
                content_length = int(raw_content_length)
            except ValueError:
                await _send_json(
                    send,
                    status_code=400,
                    detail="Invalid Content-Length header.",
                )
                return
            if content_length < 0:
                await _send_json(
                    send,
                    status_code=400,
                    detail="Invalid Content-Length header.",
                )
                return
            if content_length > configured_max_bytes:
                await _send_json(
                    send,
                    status_code=413,
                    detail="Request body is too large.",
                )
                return

        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            total_bytes += len(chunk)
            if total_bytes > configured_max_bytes:
                await _send_json(
                    send,
                    status_code=413,
                    detail="Request body is too large.",
                )
                return
            if chunk:
                chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        # Validate JSON primitives before schema/error rendering, while keeping
        # the exact bytes for HMAC verification. Python's JSON parser accepts
        # infinity and lone surrogates which cannot be returned as valid JSON.
        if body:
            try:
                pending_values = [(json.loads(body), 0)]
                while pending_values:
                    value, depth = pending_values.pop()
                    if depth > 128:
                        raise ValueError("JSON nesting limit")
                    if isinstance(value, str):
                        value.encode("utf-8", errors="strict")
                    elif isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("Non-finite number")
                    elif isinstance(value, dict):
                        pending_values.extend((item, depth + 1) for pair in value.items() for item in pair)
                    elif isinstance(value, list):
                        pending_values.extend((item, depth + 1) for item in value)
            except (ValueError, UnicodeError, RecursionError):
                await _send_json(send, status_code=400, detail="Request body must contain valid JSON text and finite numbers.")
                return
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[EXACT_REQUEST_BODY_SCOPE_KEY] = body
        delivered = False

        async def replay_receive() -> Dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        consent_token = None
        if is_chat_request:
            consent = verify_external_ai_consent(
                headers=headers,
                method=method,
                path=path,
                body=body,
                operator_enabled=external_ai_processing_enabled(),
            )
            consent_token = set_external_ai_request_context(consent)
        try:
            await self.app(scope, replay_receive, send)
        finally:
            if consent_token is not None:
                reset_external_ai_request_context(consent_token)
