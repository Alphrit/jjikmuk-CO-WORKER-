"""Short-lived HMAC v2 proof for trusted conversation identity.

Replay protection is deliberately process-local. Deployments with multiple
workers or instances must add a shared atomic nonce store at the integration
layer; this module alone cannot provide global replay prevention.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

IDENTITY_SECRET_ENV = "FOOD_CHATBOT_USER_IDENTITY_SECRET"
IDENTITY_MAX_AGE_ENV = "FOOD_CHATBOT_IDENTITY_MAX_AGE_SECONDS"
DEFAULT_MAX_AGE_SECONDS = 300
MIN_MAX_AGE_SECONDS = 30
MAX_MAX_AGE_SECONDS = 900
MAX_FUTURE_SKEW_SECONDS = 5
MIN_SECRET_BYTES = 32
MAX_NONCE_KEYS = 10_000

_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[!-~]{16,128}$")
_PURPOSE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_MAX_USER_ID = 9_223_372_036_854_775_807


@dataclass(frozen=True)
class ConversationIdentityVerification:
    verified: bool
    reason: str
    user_id: int | None = None
    timestamp: int | None = None
    nonce: str | None = None


class _NonceRegistry:
    def __init__(self, max_keys: int = MAX_NONCE_KEYS) -> None:
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


_nonce_registry = _NonceRegistry()


def reset_conversation_identity_nonce_registry() -> None:
    """Clear process-local replay state for isolated tests."""
    _nonce_registry.reset()


def _visible_ascii(value: str) -> bool:
    return bool(value) and all("!" <= character <= "~" for character in value)


def _same_secret(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left.encode(), right.encode())


def _max_age_status() -> tuple[bool, int]:
    raw = os.getenv(IDENTITY_MAX_AGE_ENV)
    if raw is None or not raw.strip():
        return True, DEFAULT_MAX_AGE_SECONDS
    try:
        seconds = int(raw)
    except (TypeError, ValueError, OverflowError):
        return False, DEFAULT_MAX_AGE_SECONDS
    valid = MIN_MAX_AGE_SECONDS <= seconds <= MAX_MAX_AGE_SECONDS
    return valid, seconds if valid else DEFAULT_MAX_AGE_SECONDS


def conversation_identity_boundary_status() -> dict[str, object]:
    """Return readiness facts without returning or deriving the secret."""
    secret = (os.getenv(IDENTITY_SECRET_ENV) or "").strip()
    comparisons = [
        (os.getenv(name) or "").strip()
        for name in (
            "FOOD_CHATBOT_API_TOKEN",
            "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET",
        )
    ]
    long_enough = len(secret.encode("ascii", errors="ignore")) >= MIN_SECRET_BYTES
    header_safe = _visible_ascii(secret) and secret.isascii()
    distinct = bool(secret) and not any(_same_secret(secret, value) for value in comparisons if value)
    max_age_valid, max_age = _max_age_status()
    return {
        "configured": bool(long_enough and header_safe and distinct and max_age_valid),
        "secret_long_enough": long_enough,
        "secret_visible_ascii": header_safe,
        "secret_distinct": distinct,
        "max_age_valid": max_age_valid,
        "max_age_seconds": max_age,
        "replay_scope": "process_local",
    }


def _configured_secret() -> bytes | None:
    if not conversation_identity_boundary_status()["configured"]:
        return None
    return (os.getenv(IDENTITY_SECRET_ENV) or "").strip().encode("ascii")


def _canonical_user_id(value: int | str) -> str | None:
    if isinstance(value, bool):
        return None
    raw = str(value)
    if not raw.isascii() or not raw.isdigit():
        return None
    try:
        parsed = int(raw)
    except (ValueError, OverflowError):
        return None
    canonical = str(parsed)
    if raw != canonical or not 1 <= parsed <= _MAX_USER_ID:
        return None
    return canonical


def _canonical_timestamp(value: int | str) -> tuple[str, int] | None:
    if isinstance(value, bool):
        return None
    raw = str(value)
    if not raw.isascii() or not raw.isdigit():
        return None
    try:
        parsed = int(raw)
    except (ValueError, OverflowError):
        return None
    if raw != str(parsed) or parsed <= 0:
        return None
    return raw, parsed


def _canonical_payload(*, timestamp: str, method: str, path: str, user_id: str,
                       nonce: str, body: bytes, purpose: str) -> bytes:
    return "\n".join((
        "v2", purpose, timestamp, method, path, user_id, nonce,
        hashlib.sha256(body).hexdigest(),
    )).encode("utf-8")


def _valid_request_parts(method: str, path: str, nonce: str, purpose: str) -> bool:
    return bool(
        method and method == method.upper() and method.isascii()
        and method.isalpha() and len(method) <= 16
        and path.startswith("/") and "\n" not in path and "\r" not in path
        and len(path.encode("utf-8")) <= 2048
        and _NONCE_RE.fullmatch(nonce)
        and _PURPOSE_RE.fullmatch(purpose)
    )


def sign_conversation_identity(*, timestamp: int | str, method: str, path: str,
                               user_id: int | str, nonce: str, body: bytes,
                               purpose: str = "conversation") -> str:
    """Sign exact request bytes; raise ValueError for unsafe configuration/input."""
    secret = _configured_secret()
    ts = _canonical_timestamp(timestamp)
    uid = _canonical_user_id(user_id)
    if secret is None:
        raise ValueError("conversation identity signing is not safely configured")
    if ts is None or uid is None or not isinstance(body, bytes) or not _valid_request_parts(method, path, nonce, purpose):
        raise ValueError("invalid conversation identity proof input")
    payload = _canonical_payload(timestamp=ts[0], method=method, path=path,
                                 user_id=uid, nonce=nonce, body=body, purpose=purpose)
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_conversation_identity(*, timestamp: int | str, method: str, path: str,
                                 user_id: int | str, nonce: str, signature: str,
                                 body: bytes, purpose: str = "conversation",
                                 now: float | None = None) -> ConversationIdentityVerification:
    """Verify and atomically consume a valid proof in this process."""
    secret = _configured_secret()
    if secret is None:
        return ConversationIdentityVerification(False, "not_configured")
    ts = _canonical_timestamp(timestamp)
    uid = _canonical_user_id(user_id)
    if ts is None or uid is None or not isinstance(body, bytes) or not _valid_request_parts(method, path, nonce, purpose):
        return ConversationIdentityVerification(False, "invalid_input")
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        return ConversationIdentityVerification(False, "invalid_signature")
    max_age_valid, max_age = _max_age_status()
    if not max_age_valid:
        return ConversationIdentityVerification(False, "not_configured")
    current = time.time() if now is None else now
    if ts[1] > current + MAX_FUTURE_SKEW_SECONDS:
        return ConversationIdentityVerification(False, "timestamp_in_future")
    if current - ts[1] > max_age:
        return ConversationIdentityVerification(False, "timestamp_expired")
    payload = _canonical_payload(timestamp=ts[0], method=method, path=path,
                                 user_id=uid, nonce=nonce, body=body, purpose=purpose)
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return ConversationIdentityVerification(False, "invalid_signature")
    replay_key = hashlib.sha256(f"{purpose}\0{uid}\0{nonce}".encode("utf-8")).digest()
    if not _nonce_registry.consume(
        replay_key,
        expires_at=max(current, float(ts[1])) + max_age + MAX_FUTURE_SKEW_SECONDS,
        now=current,
    ):
        return ConversationIdentityVerification(False, "replay")
    return ConversationIdentityVerification(True, "verified", int(uid), ts[1], nonce)
