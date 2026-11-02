"""Per-request authorization for processing user context with external AI.

The browser/app cannot grant this authorization with a JSON boolean.  A trusted
backend must sign the exact HTTP request with a short-lived, separate HMAC key.
The resulting decision lives in a :class:`ContextVar`, so concurrent requests do
not share consent or usage state.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Iterator, Mapping, Optional
from app.infrastructure import external_ai_state


CONSENT_HEADER = "x-food-chatbot-external-ai-consent"
TIMESTAMP_HEADER = "x-food-chatbot-external-ai-timestamp"
NONCE_HEADER = "x-food-chatbot-external-ai-nonce"
SIGNATURE_HEADER = "x-food-chatbot-external-ai-signature"
USER_ID_HEADER = "x-food-chatbot-user-id"

CONSENT_SECRET_ENV = "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"
CONSENT_MAX_AGE_ENV = "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_MAX_AGE_SECONDS"
DEFAULT_CONSENT_MAX_AGE_SECONDS = 60
MIN_CONSENT_MAX_AGE_SECONDS = 5
MAX_CONSENT_MAX_AGE_SECONDS = 300
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5
MIN_CONSENT_SECRET_BYTES = 32
MAX_CONSENT_NONCE_KEYS = 10_000

_HEX_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_VALID_CONSENT_VALUES = {"granted", "denied"}
_VALID_USAGE_SERVICES = {"llm", "rag"}


class _ConsentNonceRegistry:
    """Bounded, process-local one-time assertion registry.

    Development fallback only. Production or an explicitly configured ledger
    uses the persistent SQLite registry instead. Separate hosts still require
    an atomic shared replay boundary at the trusted backend or gateway.
    """

    def __init__(self, max_keys: int = MAX_CONSENT_NONCE_KEYS) -> None:
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._expires: OrderedDict[bytes, float] = OrderedDict()

    def consume(self, key: bytes, *, expires_at: float, now: float) -> bool:
        with self._lock:
            while self._expires:
                oldest_key, oldest_expiry = next(iter(self._expires.items()))
                if oldest_expiry > now:
                    break
                self._expires.pop(oldest_key, None)
            if key in self._expires:
                return False
            while len(self._expires) >= self._max_keys:
                self._expires.popitem(last=False)
            self._expires[key] = expires_at
            return True

    def reset(self) -> None:
        with self._lock:
            self._expires.clear()


_consent_nonce_registry = _ConsentNonceRegistry()


def reset_external_ai_consent_nonce_registry() -> None:
    """Clear process-local replay state for isolated tests."""

    _consent_nonce_registry.reset()


@dataclass(frozen=True)
class ExternalAIConsentDecision:
    """Internal request decision; identity is intentionally never serialized."""

    verified: bool = False
    consent_granted: bool = False
    reason: str = "not_evaluated"
    user_id: Optional[str] = None
    used_services: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.verified and self.consent_granted


_DEFAULT_DECISION = ExternalAIConsentDecision()
_request_decision: ContextVar[ExternalAIConsentDecision] = ContextVar(
    "external_ai_request_decision",
    default=_DEFAULT_DECISION,
)


def _visible_ascii(value: str) -> bool:
    return bool(value) and all("!" <= character <= "~" for character in value)


def _same_secret(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _raw_consent_secret() -> str:
    return (os.getenv(CONSENT_SECRET_ENV) or "").strip()


def _configured_comparison_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            (os.getenv("FOOD_CHATBOT_API_TOKEN") or "").strip(),
            (os.getenv("FOOD_CHATBOT_USER_IDENTITY_SECRET") or "").strip(),
        )
        if value
    )


def external_ai_consent_max_age_status() -> dict[str, object]:
    raw_value = os.getenv(CONSENT_MAX_AGE_ENV)
    if raw_value is None or not raw_value.strip():
        return {
            "valid": True,
            "seconds": DEFAULT_CONSENT_MAX_AGE_SECONDS,
        }
    try:
        seconds = int(raw_value)
    except (TypeError, ValueError, OverflowError):
        return {
            "valid": False,
            "seconds": DEFAULT_CONSENT_MAX_AGE_SECONDS,
        }
    valid = MIN_CONSENT_MAX_AGE_SECONDS <= seconds <= MAX_CONSENT_MAX_AGE_SECONDS
    return {
        "valid": valid,
        "seconds": seconds if valid else DEFAULT_CONSENT_MAX_AGE_SECONDS,
    }


def external_ai_consent_boundary_status() -> dict[str, object]:
    """Return non-secret configuration facts for readiness/diagnostics."""

    secret = _raw_consent_secret()
    long_enough = len(secret.encode("utf-8")) >= MIN_CONSENT_SECRET_BYTES
    header_safe = _visible_ascii(secret)
    distinct = bool(secret) and not any(
        _same_secret(secret, configured)
        for configured in _configured_comparison_secrets()
    )
    ttl = external_ai_consent_max_age_status()
    configured = bool(long_enough and header_safe and distinct and ttl["valid"])
    return {
        "configured": configured,
        "secret_present": bool(secret),
        "secret_long_enough": long_enough,
        "secret_header_safe": header_safe,
        "secret_distinct": distinct,
        "max_age_valid": ttl["valid"],
        "max_age_seconds": ttl["seconds"],
        "replay_scope": (
            "persistent_sqlite" if external_ai_state.status()["configured"]
            else "persistent_unavailable" if external_ai_state.required()
            else "process_local"
        ),
    }


def _usable_consent_secret() -> str:
    status = external_ai_consent_boundary_status()
    return _raw_consent_secret() if status["configured"] else ""


def request_body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_external_ai_consent_payload(
    *,
    timestamp: int,
    method: str,
    path: str,
    user_id: str,
    consent: str,
    nonce: str,
    body: bytes,
) -> bytes:
    """Build the v1 HMAC payload shared by the backend and this service.

    A successfully verified nonce is consumed once in this process. A global
    one-time-use guarantee across workers or instances still needs a shared
    atomic nonce store at the trusted gateway.
    """

    return "\n".join(
        [
            "v1",
            str(timestamp),
            method.upper(),
            path,
            user_id,
            consent,
            nonce,
            request_body_sha256(body),
        ]
    ).encode("utf-8")


def sign_external_ai_consent(
    *,
    secret: str,
    timestamp: int,
    method: str,
    path: str,
    user_id: str,
    consent: str,
    nonce: str,
    body: bytes,
) -> str:
    """Return the signature expected in ``SIGNATURE_HEADER``.

    This helper keeps independent trusted-backend implementations and tests on
    one canonical format.  It does not change the current request context.
    """

    payload = canonical_external_ai_consent_payload(
        timestamp=timestamp,
        method=method,
        path=path,
        user_id=user_id,
        consent=consent,
        nonce=nonce,
        body=body,
    )
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_external_ai_consent(
    *,
    headers: Mapping[str, str],
    method: str,
    path: str,
    body: bytes,
    operator_enabled: bool,
    now: Optional[int] = None,
) -> ExternalAIConsentDecision:
    """Verify a trusted-backend consent assertion and otherwise fail closed.

    Failure reasons are kept internal.  The public response deliberately groups
    expired, malformed, and tampered assertions into ``consent_not_verified``.
    """

    if not operator_enabled:
        return ExternalAIConsentDecision(reason="operator_disabled")

    secret = _usable_consent_secret()
    if not secret:
        return ExternalAIConsentDecision(reason="boundary_unavailable")

    consent = (headers.get(CONSENT_HEADER) or "").strip().lower()
    raw_timestamp = (headers.get(TIMESTAMP_HEADER) or "").strip()
    nonce = (headers.get(NONCE_HEADER) or "").strip()
    signature = (headers.get(SIGNATURE_HEADER) or "").strip().lower()
    user_id = (headers.get(USER_ID_HEADER) or "").strip()

    if not all([consent, raw_timestamp, nonce, signature, user_id]):
        return ExternalAIConsentDecision(reason="missing_assertion")
    if consent not in _VALID_CONSENT_VALUES:
        return ExternalAIConsentDecision(reason="invalid_assertion")
    if not _NONCE_RE.fullmatch(nonce) or not _USER_ID_RE.fullmatch(user_id):
        return ExternalAIConsentDecision(reason="invalid_assertion")
    if not _HEX_SIGNATURE_RE.fullmatch(signature):
        return ExternalAIConsentDecision(reason="invalid_assertion")
    try:
        timestamp = int(raw_timestamp)
    except (TypeError, ValueError, OverflowError):
        return ExternalAIConsentDecision(reason="invalid_assertion")

    current_time = int(time.time()) if now is None else int(now)
    max_age = int(external_ai_consent_max_age_status()["seconds"])
    if (
        timestamp <= 0
        or timestamp > current_time + MAX_FUTURE_CLOCK_SKEW_SECONDS
        or current_time - timestamp > max_age
    ):
        return ExternalAIConsentDecision(reason="expired_assertion")

    expected_signature = sign_external_ai_consent(
        secret=secret,
        timestamp=timestamp,
        method=method,
        path=path,
        user_id=user_id,
        consent=consent,
        nonce=nonce,
        body=body,
    )
    if not hmac.compare_digest(signature, expected_signature):
        return ExternalAIConsentDecision(reason="invalid_assertion")

    replay_key = hashlib.sha256(
        f"external_ai_consent\0{user_id}\0{nonce}".encode("utf-8")
    ).digest()
    consume = (external_ai_state.consume_nonce if external_ai_state.required()
               else _consent_nonce_registry.consume)
    if not consume(
        replay_key,
        expires_at=max(float(current_time), float(timestamp))
        + max_age
        + MAX_FUTURE_CLOCK_SKEW_SECONDS,
        now=float(current_time),
    ):
        return ExternalAIConsentDecision(reason="replayed_assertion")

    return ExternalAIConsentDecision(
        verified=True,
        consent_granted=consent == "granted",
        reason="verified" if consent == "granted" else "consent_denied",
        user_id=user_id,
    )


def set_external_ai_request_context(
    decision: ExternalAIConsentDecision,
) -> Token[ExternalAIConsentDecision]:
    return _request_decision.set(decision)


def reset_external_ai_request_context(
    token: Token[ExternalAIConsentDecision],
) -> None:
    _request_decision.reset(token)


@contextmanager
def external_ai_request_context(
    decision: ExternalAIConsentDecision,
) -> Iterator[None]:
    token = set_external_ai_request_context(decision)
    try:
        yield
    finally:
        reset_external_ai_request_context(token)


def external_ai_request_allowed() -> bool:
    return _request_decision.get().allowed


def mark_external_ai_used(service: str) -> None:
    """Record an attempted external disclosure before making the provider call."""

    if service not in _VALID_USAGE_SERVICES:
        raise ValueError("Unsupported external AI service marker.")
    current = _request_decision.get()
    if not current.allowed or service in current.used_services:
        return
    _request_decision.set(
        replace(
            current,
            used_services=(*current.used_services, service),
        )
    )


def current_external_ai_processing_summary() -> dict[str, object]:
    """Return a public, identity-free summary suitable for ``ChatResponse``."""

    decision = _request_decision.get()
    services = list(decision.used_services)
    if services:
        status = "used"
    elif decision.allowed:
        status = "allowed_not_used"
    elif decision.reason == "operator_disabled":
        status = "operator_disabled"
    elif decision.verified and not decision.consent_granted:
        status = "consent_denied"
    elif decision.reason == "not_evaluated":
        status = "not_evaluated"
    else:
        status = "consent_not_verified"
    return {
        "allowed_for_request": decision.allowed,
        "used": bool(services),
        "status": status,
        "services": services,
    }
