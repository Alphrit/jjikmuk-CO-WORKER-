from fastapi.testclient import TestClient

from app.api import main as api_main


def _assert_no_store(response):
    assert response.headers.get("cache-control") == "no-store, private, max-age=0"
    assert response.headers.get("pragma") == "no-cache"


def test_sensitive_chat_and_conversation_errors_are_not_cached():
    client = TestClient(api_main.app)

    responses = [
        client.post("/chat", json={}),
        client.get("/conversations"),
        client.get("/conversations/not-a-uuid"),
    ]

    assert all(response.status_code >= 400 for response in responses)
    for response in responses:
        _assert_no_store(response)

    client.close()


def test_public_health_and_root_paths_are_not_changed_to_private_cache_policy():
    client = TestClient(api_main.app)
    responses = [client.get("/"), client.get("/health")]

    assert all(response.status_code == 200 for response in responses)
    assert all(
        response.headers.get("cache-control") != "no-store, private, max-age=0"
        for response in responses
    )

    client.close()
