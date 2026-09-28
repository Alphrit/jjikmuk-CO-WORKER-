import io
import json
import logging
from email.message import Message
from urllib import error, request as urllib_request, response as urllib_response
from unittest.mock import patch

import pytest

from app.infrastructure import llm
from app.infrastructure.external_ai_consent import (
    ExternalAIConsentDecision,
    reset_external_ai_request_context,
    set_external_ai_request_context,
)
from app.infrastructure.external_endpoint_policy import (
    external_endpoint_policy_status,
    validate_external_ai_url,
)


@pytest.fixture(autouse=True)
def _clean_endpoint_environment(monkeypatch):
    for name in [
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "OPENAI_BASE_URL",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_openai_is_the_only_built_in_official_host():
    decision = validate_external_ai_url("https://api.openai.com/v1/responses")

    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert validate_external_ai_url(
        "https://generativelanguage.googleapis.com/v1beta/models/test"
    ).allowed is False
    assert validate_external_ai_url(
        "https://api.anthropic.com/v1/messages"
    ).allowed is False


def test_custom_host_requires_an_exact_normalized_allowlist_entry(monkeypatch):
    url = "https://Provider.Example.Invalid./generate"
    assert validate_external_ai_url(url).allowed is False

    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.example.invalid.",
    )
    decision = validate_external_ai_url(url)

    assert decision.allowed is True
    assert decision.canonical_url == "https://provider.example.invalid/generate"


def test_idna_hostname_is_compared_after_canonicalization(monkeypatch):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "xn--tst-qla.invalid",
    )

    decision = validate_external_ai_url("https://täst.invalid/generate")

    assert decision.allowed is True
    assert decision.canonical_url == "https://xn--tst-qla.invalid/generate"


def test_suffix_confusion_and_wildcards_never_match(monkeypatch):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.invalid",
    )

    assert validate_external_ai_url(
        "https://provider.invalid.evil.invalid/generate"
    ).allowed is False

    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "*.invalid,provider.invalid",
    )
    decision = validate_external_ai_url("https://provider.invalid/generate")
    assert decision.allowed is False
    assert decision.reason == "configured_allowlist_invalid"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://provider.invalid/generate", "https_required"),
        ("https://user:pass@provider.invalid/generate", "userinfo_forbidden"),
        ("https://provider.invalid/generate?key=secret", "query_forbidden"),
        ("https://provider.invalid/generate#private", "fragment_forbidden"),
        (
            "https://provider.invalid:8443/generate",
            "nonstandard_port_forbidden",
        ),
        ("https://localhost/generate", "localhost_forbidden"),
        ("https://service.local/generate", "local_domain_forbidden"),
        ("https://single-label/generate", "hostname_ambiguous"),
        ("https://provider.invalid\\@evil.invalid/generate", "url_invalid"),
    ],
)
def test_unsafe_url_shapes_are_rejected_even_when_host_is_allowlisted(
    monkeypatch,
    url,
    reason,
):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.invalid,localhost,service.local,single-label,evil.invalid",
    )

    decision = validate_external_ai_url(url)

    assert decision.allowed is False
    assert decision.reason == reason


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "[::1]",
        "[fe80::1]",
        "[ff02::1]",
    ],
)
def test_non_global_ip_literals_are_blocked_even_if_allowlisted(
    monkeypatch,
    host,
):
    monkeypatch.setenv("FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS", host)

    decision = validate_external_ai_url(f"https://{host}/generate")

    assert decision.allowed is False
    assert decision.reason == "ip_literal_forbidden"


def test_public_ip_literal_can_only_be_used_with_an_exact_allowlist(monkeypatch):
    url = "https://8.8.8.8/generate"
    assert validate_external_ai_url(url).allowed is False

    monkeypatch.setenv("FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS", "8.8.8.8")
    assert validate_external_ai_url(url).allowed is True


@pytest.mark.parametrize(
    "legacy_host",
    [
        "0x7f.0.0.1",
        "0x7f000001",
        "0177.0.0.1",
        "127.1",
        "2130706433",
    ],
)
def test_legacy_or_ambiguous_ip_spellings_are_never_dns_allowlist_hosts(
    monkeypatch,
    legacy_host,
):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        legacy_host,
    )

    decision = validate_external_ai_url(f"https://{legacy_host}/generate")

    assert decision.allowed is False
    assert decision.reason in {"ip_literal_ambiguous", "hostname_ambiguous"}


def test_explicit_official_base_override_still_requires_configured_allowlist(
    monkeypatch,
):
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    assert llm.is_llm_provider_available() is False
    assert llm.get_llm_endpoint_policy_status()["reason"] == (
        "override_host_not_allowed"
    )

    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "api.openai.com",
    )
    assert llm.is_llm_provider_available() is True


def test_invalid_openai_endpoint_disables_provider_before_transport(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://provider.invalid/v1")
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.invalid,127.0.0.1",
    )
    token = set_external_ai_request_context(
        ExternalAIConsentDecision(
            verified=True,
            consent_granted=True,
            reason="verified",
            user_id="endpoint-test",
        )
    )
    try:
        assert llm.is_llm_provider_available() is False
        assert llm.is_llm_enabled() is False
        with patch.object(llm, "_post_json") as post_json:
            assert llm._generate_openai("private prompt") is None
        post_json.assert_not_called()
    finally:
        reset_external_ai_request_context(token)


@pytest.mark.parametrize("provider", ["gemini", "claude", "custom"])
def test_removed_providers_are_explicitly_unsupported(monkeypatch, provider):
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("LLM_PROVIDER", provider)

    assert llm.is_supported_llm_provider() is False
    assert llm.is_llm_provider_available() is False
    assert llm.get_llm_endpoint_policy_status()["reason"] == "provider_unsupported"


class _JSONResponse:
    def __init__(self, url: str, payload: dict):
        self._url = url
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, _limit):
        return json.dumps(self._payload).encode("utf-8")


def test_direct_post_revalidates_url_and_keeps_urlopen_monkeypatch_seam(
    monkeypatch,
):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.invalid",
    )
    url = "https://provider.invalid/generate"
    with patch.object(
        llm.request,
        "urlopen",
        return_value=_JSONResponse(url, {"ok": True}),
    ) as urlopen:
        assert llm._post_json(url, {"prompt": "private"}, {}) == {"ok": True}
    urlopen.assert_called_once()

    with patch.object(llm.request, "urlopen") as blocked_transport:
        assert llm._post_json(
            "https://provider.invalid/generate?credential=secret",
            {"prompt": "private"},
            {},
        ) is None
    blocked_transport.assert_not_called()


def test_redirects_are_rejected_without_contacting_redirect_target(monkeypatch):
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        "provider.invalid,redirected.invalid",
    )
    url = "https://provider.invalid/generate"
    redirect_error = error.HTTPError(
        url,
        302,
        "Found",
        hdrs={"Location": "https://redirected.invalid/target"},
        fp=io.BytesIO(b""),
    )

    with patch.object(
        llm.request,
        "urlopen",
        side_effect=redirect_error,
    ) as urlopen:
        assert llm._post_json(url, {}, {}) is None

    # The no-redirect transport turns the first 3xx into HTTPError; no second
    # request is made even when the redirect target itself is allowlisted.
    urlopen.assert_called_once()
    assert _redirect_handler_result(url) is None


def test_real_urllib_opener_chain_rejects_redirect_before_second_open():
    calls = []
    first_url = "https://provider.invalid/generate"
    redirected_url = "https://redirected.invalid/private-target"

    class SyntheticHTTPSHandler(urllib_request.HTTPSHandler):
        def https_open(self, req):
            calls.append(req.full_url)
            headers = Message()
            headers["Location"] = redirected_url
            synthetic_response = urllib_response.addinfourl(
                io.BytesIO(b""),
                headers,
                req.full_url,
                code=302,
            )
            synthetic_response.msg = "Found"
            return synthetic_response

    transport = llm._SafeURLRequestTransport()
    transport._opener = urllib_request.build_opener(
        urllib_request.ProxyHandler({}),
        SyntheticHTTPSHandler(),
        llm._RejectRedirectHandler(),
    )

    with pytest.raises(error.HTTPError) as caught:
        transport.urlopen(
            transport.Request(first_url, method="POST"),
            timeout=1,
        )

    assert caught.value.code == 302
    assert calls == [first_url]
    assert redirected_url not in calls


def _redirect_handler_result(url):
    handler = llm._RejectRedirectHandler()
    req = llm.request.Request(url)
    return handler.redirect_request(
        req,
        None,
        302,
        "Found",
        {},
        "https://redirected.invalid/target",
    )


def test_endpoint_rejection_logs_only_reason_code(monkeypatch, caplog):
    secret = "query-secret"
    private_url = f"https://provider.invalid/generate?token={secret}"
    caplog.set_level(logging.WARNING, logger="app.infrastructure.llm")

    assert llm._post_json(private_url, {}, {}) is None

    assert "reason=query_forbidden" in caplog.text
    assert private_url not in caplog.text
    assert secret not in caplog.text


def test_public_policy_status_never_returns_configured_hostname(monkeypatch):
    private_hostname = "private-provider.invalid"
    monkeypatch.setenv(
        "FOOD_CHATBOT_ALLOWED_EXTERNAL_AI_HOSTS",
        private_hostname,
    )

    status = external_endpoint_policy_status()
    llm_status = llm.get_llm_endpoint_policy_status("openai")

    assert status["configured_allowlist_host_count"] == 1
    assert private_hostname not in repr(status)
    assert private_hostname not in repr(llm_status)
