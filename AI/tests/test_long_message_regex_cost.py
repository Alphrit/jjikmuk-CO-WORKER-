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


# One unit repeated to the 4000-character limit, through all three request
# modes. Every shape here once took longer than a second somewhere (or is a
# variant of one that did); together they cover the regexes on each path.
SHAPES = {
    "hangul": "가", "digits": "1", "decimal": "1.", "latin": "a", "latin_spaced": "ab ", "sizes": "12g ",
    "sentence": "새우깡에 들어있는 원재료가 궁금하고 알레르기도 걱정돼요. ", "condition": "땅콩 알레르기인데 ",
    "allergens": "땅콩 대두 우유 ", "recommend": "저염 과자 추천해줘 ", "dismiss": "얘기는 됐고 ",
    "unspaced": "새우깡먹어도돼", "compare": "새우깡이랑 신라면 비교 ", "greeting": "안녕하세요 ",
    "punctuation": "?!.,~ ", "parentheses": "(대두)", "deictic": "이 상품의 ", "amount": "양",
    "productless": "제품 없이 ", "family": "엄마는 ", "nutrients": "나트륨 당류 칼로리 ", "newline": "새우깡\n",
}


def _shape(unit):
    return (unit * (MAX_CHAT_MESSAGE_LENGTH // len(unit) + 1))[:MAX_CHAT_MESSAGE_LENGTH]


@pytest.mark.parametrize("mode", ["legacy", "v2", "history"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_long_input_shape_answers_fast_on_every_path(client, mode, shape):
    from backend_v2_helpers import headers, request_payload
    text = _shape(SHAPES[shape])
    started = time.perf_counter()
    if mode == "legacy":
        response = client.post("/chat", json={"message": text}, headers={"X-Food-Chatbot-Token": TOKEN})
    elif mode == "v2":
        payload = request_payload(statuses=("DANGER",))
        payload["message"] = text
        response = client.post("/chat", json=payload, headers=headers(payload))
    else:
        history = [{"role": "user", "content": text}, {"role": "assistant", "content": text}] * 2
        response = client.post("/chat", json={"message": "나트륨 얼마야?", "chat_history": history},
                               headers={"X-Food-Chatbot-Token": TOKEN})
    assert response.status_code == 200
    # Generous for slow CI; every shape answers well under a second locally.
    assert time.perf_counter() - started < 3
