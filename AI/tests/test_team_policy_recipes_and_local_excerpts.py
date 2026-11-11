"""Team catalog option 2, named-dish/vegan recipe requests and local reviewed excerpts."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.infrastructure.repository import InMemoryProductRepository, InMemoryActivityRepository
from app.services import product, activity
from app.services.recipe_request_context import requested_dish_name, unsupported_recipe_request
from app.services.team_catalog import (
    COMPARISON_SOURCE_NOTE, RECIPE_UNAVAILABLE_ANSWER, RECOMMENDATION_UNAVAILABLE_ANSWER, _bounded_verdict,
)
from app.core.models import ChatResponse
from backend_v2_helpers import TOKEN

PRODUCT_PROMPT = "질문할 상품을 검색해 선택한 뒤"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setenv("FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES", "")
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    with TestClient(api.app) as client:
        yield client
    activity.set_activity_repository(old)


def ask(client, message, **extra):
    response = client.post("/chat", json={"message": message, **extra}, headers={"X-Food-Chatbot-Token": TOKEN})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(params=["false", "true"])
def team(request, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", request.param)
    return request.param == "true"


@pytest.mark.parametrize("message, expected", [
    ("트랜스지방이 뭐야?", "트랜스지방"),
    ("알레르기 교차오염이 뭐야?", "교차접촉"),
    ("나트륨이랑 소금 차이가 뭐야?", "염화나트륨"),
])
def test_recognized_topic_gets_its_reviewed_excerpt_without_external_ai(client, team, message, expected):
    data = ask(client, message)
    assert expected in data["answer"]
    assert data["sources"], "the reviewed excerpt must carry its citation"
    assert PRODUCT_PROMPT not in data["answer"]
    assert not (data.get("external_ai_processing") or {}).get("used")


def test_local_excerpt_must_name_the_asked_allergen(client, team):
    data = ask(client, "\"우유 알레르기가 있다\"라는 말의 뜻만 알려줘")
    assert "우유" in data["answer"]


def test_out_of_domain_reply_survives_team_mode(client, team):
    data = ask(client, "오늘 날씨 어때?")
    assert data["intent"] == "out_of_domain"
    assert data["answer"].startswith("저는 식품 성분")


def test_team_mode_says_recommendations_and_recipes_are_unavailable(client, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "true")
    assert ask(client, "저염 과자 추천해줘")["answer"] == RECOMMENDATION_UNAVAILABLE_ANSWER
    assert ask(client, "간단한 레시피 추천해줘")["answer"] == RECIPE_UNAVAILABLE_ANSWER
    swap = ask(client, "이거 대신 먹을 만한 거 추천해줘", barcode="8800000000000",
               profile={"name": "검증", "allergies": ["대두"]})
    assert swap["answer"] == RECOMMENDATION_UNAVAILABLE_ANSWER  # the earlier product's warning is not repeated here
    assert not swap.get("recommended_products")


def test_team_mode_comparison_keeps_facts_with_a_source_note(client, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "true")
    data = ask(client, "저염 두부스낵이랑 고단백 두유바 비교해줘")
    assert data["intent"] == "food_compare"
    assert data["answer"].endswith(COMPARISON_SOURCE_NOTE)
    assert len(data["comparison_products"]) == 2


def test_bounded_verdict_ignores_only_the_whether_phrase():
    base = dict(task_type="general_chat", intent="out_of_domain", answer_source="rules", risk_level="unknown")
    meta = ChatResponse(answer="저는 제품 섭취 가능 여부를 안내하는 챗봇이에요.", **base)
    claim = ChatResponse(answer="이 제품은 섭취 가능해요.", **base)
    assert _bounded_verdict(meta)
    assert not _bounded_verdict(claim)


@pytest.mark.parametrize("message, dish", [
    ("김치찌개 만드는 법 알려줘", "김치찌개"),
    ("된장국 끓이는 법 알려줘", "된장국"),
    ("간단한 요리 만드는 법 알려줘", None),
    ("저염 레시피 추천해줘", None),
])
def test_requested_dish_name(message, dish):
    assert requested_dish_name(message) == dish


def test_unsupported_recipe_requests_explain_instead_of_substituting():
    answer, _ = unsupported_recipe_request("김치찌개 만드는 법 알려줘")
    assert "김치찌개" in answer and "검토된 레시피 자료에 없어" in answer
    answer, _ = unsupported_recipe_request("비건 레시피 추천해줘")
    assert "비건" in answer and "지원하지 않아요" in answer
    assert unsupported_recipe_request("오렌지와 당근 만남주스 만드는 법 알려줘") is None
    assert unsupported_recipe_request("간단한 레시피 추천해줘") is None


def test_named_dish_recipe_is_not_replaced_by_another_dish(client, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "false")
    kimchi = ask(client, "김치찌개 만드는 법 알려줘")
    assert not kimchi.get("recipe")
    assert "김치찌개" in kimchi["answer"]
    vegan = ask(client, "비건 레시피 추천해줘")
    assert not vegan.get("recipe") and "비건" in vegan["answer"]
    reviewed = ask(client, "오렌지와 당근 만남주스 만드는 법 알려줘")
    assert reviewed["recipe"]["title"] == "오렌지와 당근 만남주스"


def test_missing_recipe_source_names_what_was_not_found(client, monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "false")
    data = ask(client, "김치찌개 레시피 알려줘")
    assert data["intent"] == "missing_product"
    assert "‘김치찌개’ 상품을 DB에서 확인하지 못해" in data["answer"]
    assert "특정 요리의 조리법" in data["answer"]
