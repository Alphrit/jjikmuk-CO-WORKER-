"""Follow-ups after a candidate choice, productless references and follow-up chips."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.core.models import ChatResponse
from app.infrastructure.repository import InMemoryProductRepository, InMemoryActivityRepository
from app.intent.rules import rule_based_classify_intent
from app.services import product, activity
from app.services.conversation_router import _explicit_named_product_query, _has_unresolved_product_confirmation
from app.services.followup_questions import tidy_followups
from app.services.team_catalog import RECIPE_UNAVAILABLE_ANSWER, RECOMMENDATION_UNAVAILABLE_ANSWER
from app.core.models import ChatHistoryItem
from backend_v2_helpers import TOKEN, request_payload, headers

TEAM_CANDIDATE_PROMPT = "검색된 상품 중 질문하려는 제품을 선택해 주세요. 같은 이름의 다른 제품일 수 있어 제조사와 포장도 확인해 주세요."


@pytest.mark.parametrize("message", ["이 상품의 원재료를 알려줘", "이 상품의 영양정보를 알려줘", "칼로리는?", "열량은?",
                                     "이 제품에 특정 성분이 들어 있어?"])
def test_productless_reference_asks_which_product(message):
    assert rule_based_classify_intent(message, None, None, []) == "missing_product"


@pytest.mark.parametrize("message", ["트랜스지방이 뭐야?", "무설탕과 무가당의 차이를 알려줘", "이 제품이랑 비슷한 거 추천해줘"])
def test_general_and_recommendation_questions_keep_their_intent(message):
    assert rule_based_classify_intent(message, None, None, []) != "missing_product"


def test_deictic_phrase_is_not_a_catalog_name():
    assert _explicit_named_product_query("이 제품에 특정 성분이 들어 있어?", None) is None


def test_team_candidate_prompt_blocks_guessing_from_history():
    history = [ChatHistoryItem(role="user", content="새우깡 먹어도 돼?"),
               ChatHistoryItem(role="assistant", content=TEAM_CANDIDATE_PROMPT),
               ChatHistoryItem(role="user", content="새우깡 먹어도 돼?"),
               ChatHistoryItem(role="assistant", content="쌀새우깡 기준으로 확인했어요.")]
    assert _has_unresolved_product_confirmation(history)


def _response(**update):
    # Only the fields the final pass reads; validation is covered by API tests.
    fields = {"intent": "unknown", "answer": "답변", "answer_source": "rules", "risk_level": "unknown",
              "recommended_questions": [], "current_product": None} | update
    return ChatResponse.model_construct(**fields)


def test_chips_drop_templates_productless_deictics_blocked_features_and_the_same_question():
    response = _response(recommended_questions=[
        "제품명 기준으로 나트륨 함량 알려줘", "바코드 기준으로 영양성분 확인해줘", "이 제품 먹어도 괜찮아?",
        "저염 레시피 추천해줘", "대체 상품을 추천해줘", "무설탕과 무가당의 차이를 설명해줘", "영양성분을 설명해줘",
    ])
    tidy = tidy_followups(response, "무설탕과 무가당의 차이를 알려줘", team_mode=True)
    assert tidy.recommended_questions == ["영양성분을 설명해줘"]


def test_chips_keep_product_and_feature_questions_when_allowed():
    response = _response(recommended_questions=["이 상품의 원재료를 알려줘", "저염 레시피 추천해줘"],
                         current_product={"product_name": "새우깡"})
    assert tidy_followups(response, "새우깡 먹어도 돼?", team_mode=False).recommended_questions == [
        "이 상품의 원재료를 알려줘", "저염 레시피 추천해줘"]


def test_sugar_free_question_is_tied_to_the_source_term():
    response = _response(answer="무당(제로슈거)과 무가당은 같은 뜻이 아닙니다.", answer_source="rag")
    assert tidy_followups(response, "무설탕과 무가당의 차이를 알려줘", team_mode=False).answer.startswith("자료에서는")


@pytest.fixture
def team_client(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "true")
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    with TestClient(api.app) as client:
        yield client
    activity.set_activity_repository(old)


@pytest.mark.parametrize("message, expected", [
    ("김치찌개 만드는 법 알려줘", RECIPE_UNAVAILABLE_ANSWER),
    ("비건 레시피 추천해줘", RECIPE_UNAVAILABLE_ANSWER),
    ("저염 과자 추천해줘", RECOMMENDATION_UNAVAILABLE_ANSWER),
])
def test_team_mode_productless_stage_c_requests_get_the_unavailable_answer(team_client, message, expected):
    payload = request_payload(statuses=("DANGER",), product=False)
    payload["message"] = message
    response = team_client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == expected
    assert not any("레시피" in question or "추천" in question for question in data["recommended_questions"])
