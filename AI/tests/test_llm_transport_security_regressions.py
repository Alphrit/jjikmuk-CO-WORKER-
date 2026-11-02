import io
import logging
from urllib import error
from unittest.mock import patch

import pytest

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


def test_recipe_request_constrains_json_types_without_weakening_local_validation(monkeypatch):
    from app.services.recipe import _recipe_output_schema, _parse_recipe_draft
    import json
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-sol")
    schema = _recipe_output_schema("기본 식재료")
    with patch.object(llm, "_post_json", return_value={"output_text": "{}"}) as post:
        llm._generate_openai("레시피 JSON", json_schema=schema)
    payload = post.call_args.args[1]
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["properties"]["servings"]["enum"] == ["1인분"]
    assert payload["store"] is False and "temperature" not in payload
    invalid = {"title": "귀리", "servings": 1, "product_name": "기본 식재료",
               "ingredients": [{"name": "귀리", "amount": "마음껏"}], "steps": ["섞어요"], "substitutions": []}
    assert _parse_recipe_draft(json.dumps(invalid)) is None
    invalid["servings"] = "1인분"
    assert _parse_recipe_draft(json.dumps(invalid)) is None


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_gpt6_request_uses_reasoning_without_incompatible_sampling(monkeypatch, effort):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-astra")
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "8192")
    with patch.object(llm, "_post_json", return_value={"status": "completed", "output_text": "답변"}) as post:
        assert llm._generate_openai("질문") == "답변"
    payload = post.call_args.args[1]
    assert payload["reasoning"] == {"effort": effort}
    assert payload["max_output_tokens"] == 8192
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert payload["store"] is False


@pytest.mark.parametrize("effort", ["", "none", "minimal", "invalid"])
def test_unsupported_gpt6_reasoning_effort_uses_valid_default(monkeypatch, effort):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-astra")
    monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    with patch.object(llm, "_post_json", return_value={"output_text": "답변"}) as post:
        assert llm._generate_openai("질문") == "답변"
    assert post.call_args.args[1]["reasoning"] == {"effort": "high"}


def test_legacy_non_reasoning_model_retains_temperature_setting(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    with patch.object(llm, "_post_json", return_value={"output_text": "답변"}) as post:
        assert llm._generate_openai("질문") == "답변"
    payload = post.call_args.args[1]
    assert payload["temperature"] == 0.2
    assert "reasoning" not in payload


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", "queued"])
def test_partial_or_unfinished_provider_answer_is_not_accepted(monkeypatch, status):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    with patch.object(llm, "_post_json", return_value={"status": status, "output_text": "완료되지 않은 판정"}):
        assert llm._generate_openai("질문") is None


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
