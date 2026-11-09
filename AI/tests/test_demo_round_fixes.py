"""Fixes from the 2026-11-09 exploratory round of realistic questions (presentation prep)."""

import pytest

from app.core.models import ChatResponse, Product
from app.intent.explanation_topics import requested_topics
from app.intent.rules import rule_based_classify_intent
from app.services.conversation_router import _explicit_named_product_query
from app.services.product_search import extract_product_search_query


@pytest.mark.parametrize("message, expected", [
    ("새우깡 몇 칼로리야", "새우깡"), ("오레오 성분", "오레오"), ("진라면 순한맛 나트륨", "진라면 순한맛"),
    ("새우깡 먹어두 돼??", "새우깡"), ("새우깡머거도돼?", "새우깡"), ("새우깡 칼로리야?", "새우깡"),
])
def test_product_name_is_cut_before_trailing_fields_and_typo_verbs(message, expected):
    assert extract_product_search_query(message) == expected


@pytest.mark.parametrize("message, expected", [
    ("진라면 순한맛 나트륨", "진라면 순한맛"),  # '라면' in a name is not '~라면'
    ("새우깡 먹어두 돼??", "새우깡"),
    ("저염 두부스낵 찾아줘", "저염 두부스낵"),
    ("제로콜라 칼로리 얼마야?", "제로콜라"),
    ("제로 음료는 칼로리가 0이야?", None),  # a claim on a kind of food
    ("저당 과자는 당류가 없어?", None),
])
def test_named_product_query(message, expected):
    assert _explicit_named_product_query(message, None) == expected


def test_zero_drink_energy_question_is_a_general_claim_topic():
    assert "zero_energy" in requested_topics("제로 음료는 칼로리가 0이야?")


@pytest.mark.parametrize("message", ["다이어트 간식 추천", "아무거나 추천해줘", "마실 거 추천해줘"])
def test_vague_food_requests_are_recommendations(message):
    assert rule_based_classify_intent(message, None, None, []) == "alternative_food_recommend"


def test_recipe_requests_stay_recipes():
    assert rule_based_classify_intent("간식 레시피 추천해줘", None, None, []) == "recipe_recommend"


def test_team_help_offers_only_available_features(monkeypatch):
    from app.services.conversation_controls import conversation_control_response
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "true")
    for message in ("도움말", "넌 누구야?"):
        reply = conversation_control_response(message, {})
        assert "레시피" not in reply.answer and "추천" not in reply.answer
        assert not any("레시피" in question for question in reply.recommended_questions)


def _product(name, barcode, confidence=0.82):
    return Product(product_name=name, barcode=barcode, report_number=barcode, product_found=True,
                   match_confidence=confidence)


def test_comparison_resolves_exact_names_and_the_tapped_barcode_without_state(monkeypatch):
    from app.services import comparison_context
    catalog = {
        "신라면": [_product("신라면", "1", 1.0), _product("신라면컵", "2")],
        "진라면": [_product("진라면(순한맛)", "3"), _product("진라면 매운맛", "4")],
    }
    monkeypatch.setattr(comparison_context, "find_product_candidates_from_message",
                        lambda query, limit=3: catalog[query.replace(" 찾아줘", "")])
    products, confirmation, _, _ = comparison_context.resolve_comparison(
        "신라면이랑 진라면 나트륨 비교", _product("진라면(순한맛)", "3"), {})
    assert confirmation is None
    assert [product.product_name for product in products] == ["신라면", "진라면(순한맛)"]
    _, confirmation, _, _ = comparison_context.resolve_comparison("신라면이랑 진라면 나트륨 비교", None, {})
    assert confirmation is not None  # '진라면' is ambiguous until a candidate is tapped


def test_team_filter_keeps_a_no_conflict_comparison_with_a_neutral_verdict():
    from app.core.public_product import public_product_payload
    from app.services.team_catalog import COMPARISON_NO_CONFLICT_NOTE, restrict_team_catalog_response
    response = ChatResponse(
        intent="food_compare", answer="신라면: 현재 DB 표시에서 등록 알레르기의 직접 충돌을 찾지 못했어요.",
        answer_source="rules", risk_level="low", overall_decision="no_known_conflict", allergy_status="no_known_match",
        comparison_products=[public_product_payload(_product("신라면", "1"), view="candidate"),
                             public_product_payload(_product("진라면", "3"), view="candidate")],
    )
    bounded = restrict_team_catalog_response(response)
    assert COMPARISON_NO_CONFLICT_NOTE in bounded.answer
    assert bounded.overall_decision == "check_required" and bounded.risk_level == "unknown"
    assert bounded.comparison_products


def test_comparison_answer_gets_no_single_product_allergy_alert():
    from app.services.chat import attach_profile_allergy_alert
    from app.core.models import Profile
    response = ChatResponse(intent="food_compare", task_type="product_chat", answer="비교", risk_level="unknown")
    product = Product(product_name="쌀새우깡", barcode="5", allergy=["새우"], product_found=True)
    assert attach_profile_allergy_alert(response, product, Profile(name="검증", allergies=["새우"])) is response
