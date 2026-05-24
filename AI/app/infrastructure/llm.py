import json
import os
import logging
from typing import Callable, Optional
from urllib import error, request


LLMClient = Callable[[str], Optional[str]]
logger = logging.getLogger(__name__)

_override_client: Optional[LLMClient] = None


def set_llm_client(client: Optional[LLMClient]) -> None:
    global _override_client
    _override_client = client


def get_llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "openai").strip().lower()


def get_llm_model() -> str:
    provider = get_llm_provider()

    if provider == "gemini":
        return os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"

    if provider == "claude":
        return os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest"

    return os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"


def is_llm_enabled() -> bool:
    if _override_client is not None:
        return True

    provider = get_llm_provider()

    if provider == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

    if provider == "claude":
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))

    return bool(os.getenv("LLM_API_URL") and os.getenv("LLM_API_KEY"))


def _post_json(url: str, payload: dict, headers: dict) -> Optional[dict]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(
            req,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    except error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            error_body = ""
        logger.warning("LLM request failed: %s", e)
        if error_body:
            logger.debug("LLM error body: %s", error_body)
        return None

    except (error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("LLM request failed: %s", e)
        return None


def _generate_openai(prompt: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": get_llm_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise Korean assistant for a food safety chatbot. "
                    "Use only the provided product facts, rule result, and RAG context. "
                    "Do not invent facts."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
        "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500")),
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = _post_json(url, payload, headers)
    if not body:
        return None

    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    logger.debug("OpenAI text extraction failed: %s", body)
    return None


def _generate_gemini(prompt: str) -> Optional[str]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    model = get_llm_model()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "너는 식품 안전 챗봇의 한국어 답변 생성기다. "
                            "반드시 제공된 제품 정보, 규칙 판단 결과, RAG 근거 안에서만 답해라. "
                            "새로운 성분, 수치, 효능, 질병 치료 효과는 만들지 마라.\n\n"
                            + prompt
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
            "maxOutputTokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500")),
            "topP": 0.9,
            "topK": 40,
        },
    }

    headers = {"Content-Type": "application/json"}

    body = _post_json(url, payload, headers)
    if not body:
        return None

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        logger.debug("Gemini candidates missing: %s", body)
        return None

    texts = []

    for candidate in candidates:
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        if isinstance(parts, list):
            for part in parts:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())

    final_text = "\n".join(texts).strip()

    if not final_text:
        logger.debug("Gemini text extraction failed: %s", body)
        return None

    return final_text


def _generate_claude(prompt: str) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    url = f"{base_url}/v1/messages"

    payload = {
        "model": get_llm_model(),
        "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500")),
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
        "system": (
            "You are a concise Korean assistant for a food safety chatbot. "
            "Use only the provided product facts, rule result, and RAG context. "
            "Do not invent facts."
        ),
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    body = _post_json(url, payload, headers)
    if not body:
        return None

    content = body.get("content")
    if isinstance(content, list):
        texts = [
            item.get("text", "").strip()
            for item in content
            if item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)

    logger.debug("Claude text extraction failed: %s", body)
    return None


def _generate_custom(prompt: str) -> Optional[str]:
    api_url = os.getenv("LLM_API_URL")
    if not api_url:
        return None

    payload = {
        "prompt": prompt,
        "max_output_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500")),
    }

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = _post_json(api_url, payload, headers)
    if not body:
        return None

    for key in ["output_text", "answer", "text", "content"]:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    logger.debug("Custom LLM text extraction failed: %s", body)
    return None


def _default_http_client(prompt: str) -> Optional[str]:
    provider = get_llm_provider()

    if provider == "gemini":
        return _generate_gemini(prompt)

    if provider == "claude":
        return _generate_claude(prompt)

    if provider == "openai":
        return _generate_openai(prompt)

    return _generate_custom(prompt)


def generate_text(prompt: str) -> Optional[str]:
    client = _override_client or _default_http_client
    return client(prompt)
