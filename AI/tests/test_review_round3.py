"""Full-code review, priority 3: guard path prefix, 422 body echo, vegetarian label words."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.infrastructure.repository import InMemoryActivityRepository, InMemoryProductRepository
from app.services import activity, product
from backend_v2_helpers import TOKEN, headers, request_payload


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    yield lambda **kwargs: TestClient(api.app, **kwargs)
    activity.set_activity_repository(old)


def test_guard_applies_behind_a_path_prefix(client_factory):
    payload = request_payload(statuses=("PASS",))
    with client_factory(root_path="/api") as client:
        response = client.post("/api/chat", json=payload)
    assert response.status_code in {401, 403}


def test_validation_errors_do_not_echo_the_request_body(client_factory):
    payload = request_payload(statuses=("PASS",))
    payload["message"] = ""
    with client_factory() as client:
        response = client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("word", ["돈육", "닭가슴살", "굴소스", "우지"])
def test_vegetarian_blocks_meat_label_words(word):
    from app.data.dictionaries import SPECIAL_DIET_RESTRICTIONS
    blocked = SPECIAL_DIET_RESTRICTIONS["채식"]["blocked_ingredients"]
    assert any(term in word for term in blocked)
