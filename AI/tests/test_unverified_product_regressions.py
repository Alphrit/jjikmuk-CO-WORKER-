import pytest

from app.api.main import chat
from app.core.models import ChatRequest, ChatResponse, Product, Profile
from app.infrastructure.rag import _build_query
from app.prompts.builder import _product_summary
from app.services.chat import generate_final_response
from app.services.general_handlers import (
    handle_alternative_food_recommend,
    handle_food_compare,
)
from app.services.handlers import (
    build_can_i_eat_result,
    handle_ingredient_check,
    handle_nutrition_explain,
)
from app.services.product import recommend_alternative_products
from app.services.product_status_handlers import handle_product_summary


UNTRUSTED_FACT_MARKERS = ("금가루", "우유분말", "9876kcal", "9876")


def _profile() -> Profile:
    return Profile(
        profile_name="검증 프로필",
        allergies=["우유"],
        conditions=["고혈압"],
        special_diets=[],
    )


def _unverified_product() -> Product:
    return Product(
        product_name="위조 제품",
        rawmtrl_nm="우유분말, 금가루",
        allergy=["우유"],
        nutrition={"kcal": 9876, "sodium": 9876},
        facts_verified=False,
    )


def _assert_fact_free_failure(response: ChatResponse, intent: str) -> None:
    assert response.intent == intent
    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert response.answer_source == "rules"
    assert "DB" in response.answer
    assert "근거로 사용하지 않았어요" in response.answer
    assert not any(marker in response.answer for marker in UNTRUSTED_FACT_MARKERS)
    assert not any(marker in " ".join(response.reasons) for marker in UNTRUSTED_FACT_MARKERS)


@pytest.mark.parametrize(
    ("intent", "handler", "message"),
    [
        ("product_summary", handle_product_summary, "이 제품 정보를 알려줘"),
        ("ingredient_check", handle_ingredient_check, "알레르기 성분 알려줘"),
        ("ingredient_check", handle_ingredient_check, "우유 들어 있어?"),
        ("nutrition_explain", handle_nutrition_explain, "칼로리 몇이야?"),
        ("can_i_eat", build_can_i_eat_result, "이거 먹어도 돼?"),
    ],
)
def test_direct_product_fact_handlers_reject_unverified_facts(
    intent,
    handler,
    message,
):
    response = handler(_profile(), _unverified_product(), message)
    _assert_fact_free_failure(response, intent)


@pytest.mark.parametrize(
    ("intent", "handler", "message"),
    [
        ("alternative_food_recommend", handle_alternative_food_recommend, "대체 제품 추천해줘"),
        ("food_compare", handle_food_compare, "칼로리 비교해줘"),
    ],
)
def test_comparison_and_recommendation_handlers_reject_unverified_reference(
    intent,
    handler,
    message,
):
    response = handler(_profile(), _unverified_product(), message)
    _assert_fact_free_failure(response, intent)


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("설명해줘", "product_summary"),
        ("알레르기 성분 알려줘", "ingredient_check"),
        ("칼로리 몇이야?", "nutrition_explain"),
    ],
)
def test_chat_does_not_echo_or_persist_identifierless_unverified_product_facts(
    message,
    expected_intent,
):
    response = chat(ChatRequest(
        message=message,
        profile=_profile(),
        product=_unverified_product(),
    ))

    _assert_fact_free_failure(response, expected_intent)
    assert response.current_product is None
    assert "last_confirmed_product" not in response.conversation_state


def test_unverified_product_never_reaches_llm_or_product_postprocessing(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("unverified product reached an enrichment path")

    monkeypatch.setattr("app.services.chat.attach_profile_allergy_alert", fail_if_called)
    monkeypatch.setattr("app.services.chat.enhance_rule_response", fail_if_called)
    monkeypatch.setattr("app.services.chat.attach_product_recommendations", fail_if_called)

    response = generate_final_response(
        rule_response=handle_product_summary(
            _profile(),
            _unverified_product(),
            "이 제품 정보 알려줘",
        ),
        message="이 제품 정보 알려줘",
        profile=_profile(),
        product=_unverified_product(),
    )

    _assert_fact_free_failure(response, "product_summary")


def test_unverified_product_is_excluded_from_prompt_and_rag_query():
    product = _unverified_product()

    prompt_summary = _product_summary(product)
    rag_query = _build_query("이 제품 알려줘", _profile(), product)

    assert "DB 미검증" in prompt_summary
    assert "DB 미검증" in rag_query
    for marker in UNTRUSTED_FACT_MARKERS:
        assert marker not in prompt_summary
        assert marker not in rag_query


def test_direct_recommendation_service_does_not_query_for_unverified_product():
    class ExplodingRepository:
        def recommend_alternatives(self, product, limit=20):
            raise AssertionError("repository must not receive unverified product facts")

    assert recommend_alternative_products(
        _unverified_product(),
        profile=_profile(),
        repository=ExplodingRepository(),
    ) == []


def test_verified_product_summary_does_not_claim_safety_assessment():
    product = Product(
        product_name="DB 검증 제품",
        rawmtrl_nm="쌀, 소금",
        allergy=["밀"],
        nutrition={"kcal": 120},
        facts_verified=True,
    )

    response = handle_product_summary(_profile(), product, "제품 정보 알려줘")

    assert response.risk_level == "unknown"
    assert response.assessment_status == "incomplete"
    assert "섭취 안전성 미평가" in response.reasons
    assert "DB 검증 제품" in response.answer
    assert "쌀, 소금" in response.answer
    assert "120" in response.answer
