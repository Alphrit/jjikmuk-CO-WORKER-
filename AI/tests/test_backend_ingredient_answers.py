"""Backend-verdict answers must answer the asked ingredient from registered label text."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.infrastructure.repository import InMemoryProductRepository, InMemoryActivityRepository
from app.services import product, activity
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


def _ask(client, message):
    payload = request_payload(statuses=("DANGER",))
    payload["message"] = message
    payload["product"].update(productName="새우깡", rawMaterials="곡류가공품, 미강유, 팜유, 향미유, 기타가공품",
                              allergy=["대두", "새우", "우유"])
    response = client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("message, expected", [
    ("대두 들어있어?", "등록된 표시에서 대두 관련 표기가 확인돼요"),
    ("콩 들어있어?", "등록된 표시에서 콩 관련 표기가 확인돼요: 대두"),
    ("새우깡에 땅콩 들어있어?", "땅콩 관련 표기는 찾지 못했어요"),
])
def test_asked_ingredient_is_answered_first(client, message, expected):
    data = _ask(client, message)
    assert data["intent"] == "ingredient_check" and data["answer_source"] == "backend"
    assert expected in data["answer"]
    assert "등록된 알레르기 표시: 대두, 새우, 우유" in data["answer"]


def test_absence_is_never_asserted_from_unverified_label(client):
    answer = _ask(client, "땅콩 들어있어?")["answer"]
    assert "단정할 수는 없어요" in answer
    assert "땅콩이 없" not in answer and "들어있지 않아요" not in answer


def test_product_name_is_not_read_as_the_asked_ingredient(client):
    answer = _ask(client, "새우깡에 땅콩 들어있어?")["answer"]
    assert "새우 관련 표기" not in answer


def test_lactose_intolerance_is_named_as_outside_the_verdict_with_the_milk_label_fact(client):
    data = _ask(client, "유당불내증인데 새우깡 먹어도 돼?")
    assert data["answer_source"] == "backend"
    assert "유당불내증은 알레르기 판정 항목이 아니어서" in data["answer"]
    assert "우유 관련 표기가 확인돼요" in data["answer"]


def test_turn_local_allergy_keeps_the_saved_profile_verdict_and_shows_the_label_fact(client):
    data = _ask(client, "새우 알레르기인데 이거 먹어도 돼?")
    assert data["intent"] == "profile_based_recheck" and data["answer_source"] == "backend"
    # The DANGER for the saved profile conditions must not disappear.
    assert data["risk_level"] == "high" and data["overall_decision"] == "avoid"
    assert "피해주세요" in data["answer"]
    assert "새우 관련 표기가 확인돼요" in data["answer"]
    assert "아래 프로필 기준 판정에 반영되지 않았어요" in data["answer"]
