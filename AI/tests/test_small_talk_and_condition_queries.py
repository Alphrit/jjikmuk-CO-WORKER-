"""Greetings and stated conditions must not be read as catalog product names."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.infrastructure.repository import InMemoryProductRepository, InMemoryActivityRepository
from app.services import product, activity
from app.services.conversation_controls import (
    CONVERSATION_CONTROL_REASON, conversation_control_response, small_talk_kind, strip_leading_small_talk,
)
from app.services.conversation_router import _explicit_named_product_query
from backend_v2_helpers import TOKEN, request_payload, headers


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


def legacy(client, message, **extra):
    response = client.post("/chat", json={"message": message, **extra}, headers={"X-Food-Chatbot-Token": TOKEN})
    assert response.status_code == 200, response.text
    return response.json()


def v2(client, message):
    payload = request_payload(product=False)
    payload["message"] = message
    response = client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("message, kind", [
    ("안녕하세요", "greeting"), ("안녕!", "greeting"), ("안녕하세요 ㅎㅎ", "greeting"), ("하이", "greeting"),
    ("고마워요", "thanks"), ("감사합니다", "thanks"), ("잘 가", "farewell"),
    ("넌 뭘 할 수 있어?", "capability"), ("너 누구야?", "capability"), ("ㅋㅋㅋ", "filler"),
])
def test_whole_message_small_talk_is_recognized(message, kind):
    assert small_talk_kind(message) == kind


@pytest.mark.parametrize("message", [
    "안녕달 손두부", "하이츄 먹어도 돼?", "안녕하세요 새우깡 먹어도 돼?", "고마워 근데 새우깡 성분 알려줘",
    # Short replies to a clarification keep their existing meaning.
    "응", "네", "아니",
])
def test_product_names_and_questions_are_not_small_talk(message):
    assert small_talk_kind(message) is None


@pytest.mark.parametrize("message, expected", [
    ("안녕하세요 새우깡 먹어도 돼?", "새우깡 먹어도 돼?"),
    ("고마워 근데 새우깡 성분 알려줘", "새우깡 성분 알려줘"),
    ("안녕, 저염 두부스낵 나트륨 얼마야?", "저염 두부스낵 나트륨 얼마야?"),
    ("안녕달 손두부 먹어도 돼?", "안녕달 손두부 먹어도 돼?"),
    ("하이츄 먹어도 돼?", "하이츄 먹어도 돼?"),
    ("안녕하세요", "안녕하세요"),
])
def test_only_a_separated_leading_greeting_is_stripped(message, expected):
    assert strip_leading_small_talk(message) == expected


def test_small_talk_keeps_the_selected_product_state():
    state = {"version": 1, "last_confirmed_product": {"barcode": "8801234567890"}}
    reply = conversation_control_response("고마워", state)
    assert reply.conversation_state == state
    assert reply.fallback_reason == CONVERSATION_CONTROL_REASON


@pytest.mark.parametrize("message, expected", [
    ("유당불내증인데 새우깡 먹어도 돼?", "새우깡"),
    ("유당불내증 있는데 새우깡 먹어도 돼?", "새우깡"),
    ("유당불내증인데 초콜릿 먹어도 괜찮아?", "초콜릿"),
    ("그럼 원재료에 들어갔다는 건 아니지?", None),
])
def test_condition_clause_and_label_field_are_not_product_names(message, expected):
    assert _explicit_named_product_query(message, None) == expected


@pytest.mark.parametrize("team", ["false", "true"])
@pytest.mark.parametrize("shape", [legacy, v2])
def test_greeting_reply_survives_every_mode(client, monkeypatch, team, shape):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", team)
    monkeypatch.setenv("FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES", "")
    data = shape(client, "안녕하세요")
    assert data["answer"].startswith("안녕하세요! 찍먹 챗봇이에요.")
    assert not data.get("product_candidates")
    assert "질문할 상품을 검색해 선택한 뒤" not in data["answer"]


def test_greeting_prefix_does_not_join_the_product_name(client):
    data = legacy(client, "안녕하세요 새우깡 먹어도 돼?")
    names = [candidate["product_name"] for candidate in data["product_candidates"]]
    assert names and all("새우깡" in name for name in names)
    assert "안녕하세요 새우깡" not in data["answer"]


@pytest.mark.parametrize("message", [
    "땅콩 알레르기인데 저염 두부스낵 먹어도 돼?",
    "땅콩 알레르기라서 저염 두부스낵 먹어도 돼?",
    "땅콩 알레르기가 있는데 저염 두부스낵 먹어도 돼?",
])
def test_allergy_clause_in_any_form_resolves_the_named_product(client, message):
    data = legacy(client, message)
    assert data["intent"] == "can_i_eat"
    assert "저염 두부스낵" in data["answer"]


def test_earlier_question_does_not_turn_a_followup_into_a_recommendation():
    from app.core.models import ChatHistoryItem
    from app.intent.rules import rule_based_classify_intent
    history = [
        ChatHistoryItem(role="user", content="다른 제품이랑 제조 공장이 같대. 무슨 의미야?"),
        ChatHistoryItem(role="assistant", content="같은 제조시설 문구는 혼입 가능성을 알리는 주의문구예요."),
    ]
    intent = rule_based_classify_intent("얼마나 섞였는지 %로 알려줘", None, None, history)
    assert intent != "alternative_food_recommend"
    assert rule_based_classify_intent("다른 제품 추천해줘", None, None, history) == "alternative_food_recommend"


def test_intolerance_clause_resolves_the_named_product(client):
    data = legacy(client, "유당불내증인데 저염 두부스낵 먹어도 돼?")
    assert data["intent"] == "can_i_eat"
    assert "저염 두부스낵" in data["answer"]
    assert "유당불내증인데" not in data["answer"]
