import io
import logging
from urllib import error
from unittest.mock import patch

from app.infrastructure import llm


def test_openai_request_uses_bearer_header_and_disables_provider_storage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    with patch.object(
        llm,
        "_post_json",
        return_value={"output_text": "테스트 답변"},
    ) as post_json:
        assert llm._generate_openai("사용자 질문") == "테스트 답변"

    url, payload, headers = post_json.call_args.args
    assert url.endswith("/responses")
    assert payload["store"] is False
    assert payload["input"] == "사용자 질문"
    assert headers["Authorization"] == "Bearer openai-test-secret"
    assert "openai-test-secret" not in url


def test_http_error_log_omits_secret_url_and_response_body(monkeypatch, caplog):
    secret = "query-secret-value"
    private_body = "사용자의 민감한 알레르기 대화"
    provider_error = error.HTTPError(
        f"https://provider.invalid/generate?key={secret}",
        429,
        "Too Many Requests",
        hdrs=None,
        fp=io.BytesIO(private_body.encode("utf-8")),
    )
    monkeypatch.setenv("FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS", "provider.invalid")

    caplog.set_level(logging.DEBUG, logger="app.infrastructure.llm")
    with patch.object(llm.request, "urlopen", side_effect=provider_error):
        assert llm._post_json(
            "https://provider.invalid/generate",
            {"input": private_body},
            {"Authorization": "Bearer another-secret"},
        ) is None

    assert "HTTPError" in caplog.text
    assert "status=429" in caplog.text
    assert secret not in caplog.text
    assert private_body not in caplog.text
    assert "another-secret" not in caplog.text


def test_unexpected_openai_body_is_not_logged(monkeypatch, caplog):
    private_output = "민감한 사용자 질문이 포함된 공급자 응답"
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    caplog.set_level(logging.DEBUG, logger="app.infrastructure.llm")

    with patch.object(llm, "_post_json", return_value={"unexpected": private_output}):
        assert llm._generate_openai("질문") is None

    assert private_output not in caplog.text


class _LargeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return "https://provider.invalid/generate"

    def read(self, limit):
        return b"x" * limit


def test_oversized_llm_response_is_rejected_before_json_parsing(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RESPONSE_BYTES", "1024")
    monkeypatch.setenv("FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS", "provider.invalid")
    with patch.object(llm.request, "urlopen", return_value=_LargeResponse()):
        assert llm._post_json(
            "https://provider.invalid/generate",
            {"input": "test"},
            {},
        ) is None


def test_invalid_numeric_settings_use_bounded_defaults(monkeypatch):
    for invalid_value in ["", "-1", "nan", "inf", "999999999999"]:
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", invalid_value)
        monkeypatch.setenv("LLM_TEMPERATURE", invalid_value)
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", invalid_value)
        monkeypatch.setenv("LLM_MAX_RESPONSE_BYTES", invalid_value)
        assert llm._bounded_float(
            "LLM_TIMEOUT_SECONDS", default=llm.DEFAULT_LLM_TIMEOUT_SECONDS,
            minimum=0.1, maximum=300.0,
        ) == llm.DEFAULT_LLM_TIMEOUT_SECONDS
        assert llm._bounded_float(
            "LLM_TEMPERATURE", default=llm.DEFAULT_LLM_TEMPERATURE,
            minimum=0.0, maximum=2.0,
        ) == llm.DEFAULT_LLM_TEMPERATURE
        assert llm._bounded_int(
            "LLM_MAX_OUTPUT_TOKENS", default=llm.DEFAULT_LLM_MAX_OUTPUT_TOKENS,
            minimum=1, maximum=llm.MAX_LLM_OUTPUT_TOKENS,
        ) == llm.DEFAULT_LLM_MAX_OUTPUT_TOKENS
        assert llm._bounded_int(
            "LLM_MAX_RESPONSE_BYTES", default=llm.DEFAULT_LLM_MAX_RESPONSE_BYTES,
            minimum=1_024, maximum=llm.MAX_CONFIGURABLE_LLM_RESPONSE_BYTES,
        ) == llm.DEFAULT_LLM_MAX_RESPONSE_BYTES
