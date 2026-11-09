"""Optional OpenAI wording/recipe client.

Rules and the product database own every allergy, diet, and product decision.
The external model is deliberately limited to phrasing and recipe composition,
and is called only when both the operator switch and signed user consent allow it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Optional
from urllib import error, request as urllib_request

from dotenv import load_dotenv
from app.infrastructure.llm_trace import observe_llm_call
from app.infrastructure.external_ai_state import reserve_call, required as persistent_state_required

from app.infrastructure.external_ai_consent import (
    external_ai_request_allowed,
    mark_external_ai_used,
)
from app.infrastructure.external_endpoint_policy import (
    ExternalEndpointDecision,
    external_endpoint_policy_status,
    validate_external_ai_url,
)


LLMClient = Callable[[str], Optional[str]]
logger = logging.getLogger(__name__)
AI_DIR = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=AI_DIR / ".env")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-6-sol"
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
# The backend reads the AI response for at most 30 s; one answer can need two
# generation calls (draft + grounding review). Bound the whole request.
DEFAULT_REQUEST_BUDGET_SECONDS = 25.0
MIN_LLM_CALL_SECONDS = 2.0
_REQUEST_DEADLINE: ContextVar[Optional[float]] = ContextVar("llm_request_deadline", default=None)
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_REASONING_EFFORT = "high"
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 8_192
MAX_LLM_OUTPUT_TOKENS = 32_768
DEFAULT_LLM_MAX_RESPONSE_BYTES = 2_097_152
MAX_CONFIGURABLE_LLM_RESPONSE_BYTES = 10_485_760
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}
_SUPPORTED_PROVIDERS = {"openai", "disabled"}

_override_client: Optional[LLMClient] = None


class _RejectRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _SafeURLRequestTransport:
    Request = urllib_request.Request

    def __init__(self) -> None:
        self._opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}),
            _RejectRedirectHandler(),
        )

    def urlopen(self, req, *, timeout):
        return self._opener.open(req, timeout=timeout)


# Kept as a small test seam; production traffic still rejects proxies/redirects.
request = _SafeURLRequestTransport()


def _bounded_float(
    name: str, *, default: float, minimum: float, maximum: float
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = float("nan")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        logger.warning("Invalid %s; using the safe default.", name)
        return default
    return value


def _bounded_int(
    name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        logger.warning("Invalid %s; using the safe default.", name)
        return default
    if not minimum <= value <= maximum:
        logger.warning("Out-of-range %s; using the safe default.", name)
        return default
    return value


def external_ai_processing_enabled() -> bool:
    raw = os.getenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI")
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE_ENV_VALUES:
        return True
    if value in _FALSE_ENV_VALUES:
        return False
    logger.warning(
        "Invalid FOOD_CHATBOT_ENABLE_EXTERNAL_AI; external processing remains disabled."
    )
    return False


def set_llm_client(client: Optional[LLMClient]) -> None:
    global _override_client
    _override_client = client


def get_llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "openai").strip().lower()


def is_supported_llm_provider(provider: Optional[str] = None) -> bool:
    return (provider or get_llm_provider()) in _SUPPORTED_PROVIDERS


def get_llm_model() -> str:
    if get_llm_provider() != "openai":
        return ""
    return (
        (os.getenv("OPENAI_MODEL") or "").strip()
        or (os.getenv("LLM_MODEL") or "").strip()
        or DEFAULT_LLM_MODEL
    )


def _endpoint_decision() -> ExternalEndpointDecision:
    configured = (os.getenv("OPENAI_BASE_URL") or "").strip()
    base = validate_external_ai_url(
        configured or DEFAULT_OPENAI_BASE_URL,
        require_configured_allowlist=bool(configured),
    )
    if not base.allowed or not base.canonical_url:
        return base
    return validate_external_ai_url(
        f"{base.canonical_url.rstrip('/')}/responses",
        require_configured_allowlist=bool(configured),
    )


def get_llm_endpoint_policy_status(
    provider: Optional[str] = None,
) -> dict[str, object]:
    selected = provider or get_llm_provider()
    if selected != "openai":
        return {
            "provider": selected if selected == "disabled" else "unsupported",
            "endpoint_allowed": False,
            "reason": "provider_disabled" if selected == "disabled" else "provider_unsupported",
            "allowlist": external_endpoint_policy_status(),
        }
    decision = _endpoint_decision()
    return {
        "provider": "openai",
        "endpoint_allowed": decision.allowed,
        "reason": decision.reason,
        "allowlist": external_endpoint_policy_status(),
    }


def is_llm_provider_available() -> bool:
    if _override_client is not None:
        return True
    return bool(
        external_ai_processing_enabled()
        and get_llm_provider() == "openai"
        and os.getenv("OPENAI_API_KEY")
        and _endpoint_decision().allowed
    )


def is_llm_enabled() -> bool:
    if _override_client is not None:
        return True
    return is_llm_provider_available() and external_ai_request_allowed()


@contextmanager
def llm_request_budget():
    """Share one time budget across every generation call of a /chat request."""
    budget = _bounded_float(
        "FOOD_CHATBOT_REQUEST_BUDGET_SECONDS", default=DEFAULT_REQUEST_BUDGET_SECONDS,
        minimum=1.0, maximum=300.0,
    )
    token = _REQUEST_DEADLINE.set(time.monotonic() + budget)
    try:
        yield
    finally:
        _REQUEST_DEADLINE.reset(token)


def _call_timeout() -> Optional[float]:
    configured = _bounded_float(
        "LLM_TIMEOUT_SECONDS", default=DEFAULT_LLM_TIMEOUT_SECONDS, minimum=0.1, maximum=300.0,
    )
    deadline = _REQUEST_DEADLINE.get()
    if deadline is None:
        return configured
    remaining = deadline - time.monotonic()
    if remaining < MIN_LLM_CALL_SECONDS:
        return None
    return min(configured, remaining)


def _post_json(url: str, payload: dict, headers: dict) -> Optional[dict]:
    timeout = _call_timeout()
    if timeout is None:
        # Too little time is left for the caller to receive an answer; skip
        # before reserving a call so no budget or consent is spent.
        logger.warning("OpenAI call skipped: request time budget exhausted.")
        return None
    endpoint = validate_external_ai_url(url)
    if not endpoint.allowed or not endpoint.canonical_url:
        logger.warning("OpenAI endpoint rejected by policy: reason=%s", endpoint.reason)
        return None
    canonical_url = endpoint.canonical_url
    req = request.Request(
        canonical_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        reserve_call('generation')
        if persistent_state_required():
            mark_external_ai_used('llm')
        with request.urlopen(
            req,
            timeout=timeout,
        ) as response:
            get_url = getattr(response, "geturl", None)
            if callable(get_url) and get_url() != canonical_url:
                logger.warning("OpenAI redirect response was rejected.")
                return None
            maximum = _bounded_int(
                "LLM_MAX_RESPONSE_BYTES",
                default=DEFAULT_LLM_MAX_RESPONSE_BYTES,
                minimum=1_024,
                maximum=MAX_CONFIGURABLE_LLM_RESPONSE_BYTES,
            )
            raw = response.read(maximum + 1)
            if len(raw) > maximum:
                logger.warning("OpenAI response exceeded the configured byte limit.")
                return None
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
    except error.HTTPError as exc:
        logger.warning(
            "OpenAI request failed: error_type=%s status=%s",
            type(exc).__name__,
            getattr(exc, "code", None),
        )
    except (error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("OpenAI request failed: error_type=%s", type(exc).__name__)
    except Exception as exc:
        # Provider and test doubles must not take down the deterministic rule path.
        logger.warning("OpenAI request failed: error_type=%s", type(exc).__name__)
    return None


def _extract_output_text(body: dict) -> Optional[str]:
    if body.get("status") not in (None, "completed"):
        # A reasoning/token budget may be exhausted after a partial answer.
        # Never treat an unfinished draft as a successful generation.
        logger.warning("OpenAI response did not complete; keeping rule fallback.")
        return None
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = body.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "output_text"
                and isinstance(block.get("text"), str)
                and block["text"].strip()
            ):
                parts.append(block["text"].strip())
    return "\n".join(parts) or None


def _generate_openai(prompt: str, *, json_schema: Optional[dict] = None) -> Optional[str]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    endpoint = _endpoint_decision()
    if not api_key or not endpoint.allowed or not endpoint.canonical_url:
        return None
    model = get_llm_model()
    payload = {
        "model": model,
        "instructions": (
            "You are a concise Korean assistant inside an allergy-first food "
            "chatbot. Use only the supplied product facts and rule result. "
            "Never invent product ingredients, allergy safety, or nutrition facts."
        ),
        "input": prompt,
        "max_output_tokens": _bounded_int(
            "LLM_MAX_OUTPUT_TOKENS",
            default=DEFAULT_LLM_MAX_OUTPUT_TOKENS,
            minimum=1,
            maximum=MAX_LLM_OUTPUT_TOKENS,
        ),
        # The application owns chat retention; do not create a second provider
        # conversation store for these requests.
        "store": False,
    }
    if json_schema is not None:
        payload["text"] = {"format": {
            "type": "json_schema", "name": "food_chatbot_output",
            "strict": True, "schema": json_schema,
        }}
    if model.startswith(("gpt-6-", "gpt-6.")):
        effort = (os.getenv("LLM_REASONING_EFFORT") or "").strip().lower()
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            effort = DEFAULT_LLM_REASONING_EFFORT
        # GPT-6 reasoning requests reject incompatible sampling parameters.
        payload["reasoning"] = {"effort": effort}
    else:
        payload["temperature"] = _bounded_float(
            "LLM_TEMPERATURE",
            default=DEFAULT_LLM_TEMPERATURE,
            minimum=0.0,
            maximum=2.0,
        )
    body = _post_json(
        endpoint.canonical_url,
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    return _extract_output_text(body) if body else None


def generate_text(prompt: str, *, json_schema: Optional[dict] = None) -> Optional[str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    if _override_client is not None:
        try:
            result = observe_llm_call(lambda: _override_client(prompt), provider="test")
        except Exception as exc:
            logger.warning(
                "Injected LLM client failed: error_type=%s", type(exc).__name__
            )
            return None
        return result.strip() if isinstance(result, str) and result.strip() else None
    if not is_llm_enabled():
        return None
    if not persistent_state_required():
        mark_external_ai_used("llm")
    return observe_llm_call(
        lambda: _generate_openai(prompt, json_schema=json_schema) if json_schema is not None else _generate_openai(prompt),
        provider="openai",
    )


__all__ = [
    "external_ai_processing_enabled",
    "generate_text",
    "get_llm_endpoint_policy_status",
    "get_llm_model",
    "get_llm_provider",
    "is_llm_enabled",
    "is_llm_provider_available",
    "is_supported_llm_provider",
    "set_llm_client",
]
