"""Long messages within the 4000-character limit must not stall the server.

A backtracking regex holds the GIL, so one slow request blocks every other
request and the health check until the backend times out.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.core.models import MAX_CHAT_MESSAGE_LENGTH
from app.infrastructure.product_storage.common import _explicit_package_sizes
from app.infrastructure.repository import InMemoryProductRepository, InMemoryActivityRepository
from app.intent.request_semantics import current_topic_message
from app.services import product, activity
from backend_v2_helpers import TOKEN


def _elapsed(function, *args):
    started = time.perf_counter()
    function(*args)
    return time.perf_counter() - started


def test_topic_dismissal_keeps_behavior_and_is_linear():
    assert current_topic_message("새우깡 얘기는 됐고 신라면 나트륨 알려줘") == "신라면 나트륨 알려줘"
    assert current_topic_message("새우깡얘기는 됐고 원재료") == "원재료"
    assert current_topic_message("그 얘기 말고, 대두 들어있어?") == "대두 들어있어?"
    assert current_topic_message("아주 긴 문장 하나 둘 얘기는 됐고 X") == "아주 긴 문장 하나 둘 얘기는 됐고 X"
    assert current_topic_message("나트륨 설명해줘") == "나트륨 설명해줘"
    assert _elapsed(current_topic_message, "가" * MAX_CHAT_MESSAGE_LENGTH) < 0.5


def test_package_sizes_keep_behavior_and_are_linear():
    assert _explicit_package_sizes("새우깡 90g") == {("mass", 90.0)}
    assert _explicit_package_sizes("우유 1.5L, 200ml") == {("volume", 1500.0), ("volume", 200.0)}
    assert _explicit_package_sizes("2kg") == {("mass", 2000.0)}
    assert _explicit_package_sizes("90gram") == set()
    assert _elapsed(_explicit_package_sizes, "1" * MAX_CHAT_MESSAGE_LENGTH) < 0.5


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    with TestClient(api.app) as client:
        yield client
    activity.set_activity_repository(old)


@pytest.mark.parametrize("text", ["가", "1", "새우깡먹어도돼", "땅콩 알레르기인데 "])
def test_max_length_message_answers_well_within_the_backend_timeout(client, text):
    message = (text * MAX_CHAT_MESSAGE_LENGTH)[:MAX_CHAT_MESSAGE_LENGTH]
    started = time.perf_counter()
    response = client.post("/chat", json={"message": message}, headers={"X-Food-Chatbot-Token": TOKEN})
    assert response.status_code == 200
    assert time.perf_counter() - started < 10  # backend read timeout is 30 s
