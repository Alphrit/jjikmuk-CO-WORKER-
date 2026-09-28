"""The optional external generation boundary supports OpenAI only."""

from app.infrastructure import llm


def test_openai_is_the_only_network_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")

    assert llm.get_llm_provider() == "openai"
    assert llm.get_llm_model() == "test-model"
    assert llm.is_supported_llm_provider() is True
    assert llm.is_llm_provider_available() is True


def test_removed_or_misspelled_provider_fails_closed(monkeypatch):
    for provider in ("gemini", "anthropic", "custom", "opneai"):
        monkeypatch.setenv("LLM_PROVIDER", provider)
        assert llm.is_supported_llm_provider() is False
        assert llm.is_llm_provider_available() is False
        assert llm.get_llm_model() == ""


def test_operator_switch_and_user_consent_are_both_required(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")

    assert llm.is_llm_provider_available() is False
    assert llm.is_llm_enabled() is False


def test_injected_local_client_remains_a_non_network_test_seam():
    llm.set_llm_client(lambda prompt: f"응답: {prompt}")
    try:
        assert llm.generate_text("테스트") == "응답: 테스트"
    finally:
        llm.set_llm_client(None)
