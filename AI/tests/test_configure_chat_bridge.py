from dotenv import dotenv_values
import pytest

from evaluation.configure_chat_bridge import configure_bridge


def test_setup_shares_secret_preserves_credentials_and_defaults_to_no_external_processing(tmp_path):
    env, props = tmp_path / ".env", tmp_path / ".chat-ai.properties"
    env.write_text("OPENAI_API_KEY=fixture-key\nFOOD_CHATBOT_API_TOKEN=fixture-service-token\nUNRELATED=keep-me\n")
    configure_bridge(env, props)
    first = dotenv_values(env)
    assert first["UNRELATED"] == "keep-me" and first["OPENAI_API_KEY"] == "fixture-key"
    assert first["FOOD_CHATBOT_ENABLE_EXTERNAL_AI"] == "false"
    assert first["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"] in props.read_text()
    assert len(first["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"]) >= 32
    configure_bridge(env, props, enable_external=True)
    second = dotenv_values(env)
    assert second["FOOD_CHATBOT_ENABLE_EXTERNAL_AI"] == "true"
    assert "FOOD_CHATBOT_ENABLE_EXTERNAL_AI=true" in props.read_text()
    assert first["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"] == second["FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET"]
    assert env.stat().st_mode & 0o777 == props.stat().st_mode & 0o777 == 0o600


def test_missing_api_key_refuses_enablement_without_writing_files(tmp_path):
    env, props = tmp_path / ".env", tmp_path / ".chat-ai.properties"
    with pytest.raises(ValueError):
        configure_bridge(env, props, enable_external=True)
    assert not env.exists() and not props.exists()


@pytest.mark.parametrize("url", ["file:///tmp/ai", "http://user:password@host", "http://localhost:8000/?secret=anything"])
def test_invalid_endpoint_does_not_write_configuration(tmp_path, url):
    with pytest.raises(ValueError):
        configure_bridge(tmp_path / ".env", tmp_path / ".chat-ai.properties", base_url=url)
    assert not list(tmp_path.iterdir())
