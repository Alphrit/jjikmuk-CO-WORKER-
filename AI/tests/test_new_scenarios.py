"""Fresh scenario round: throat swelling, typed barcodes, English allergy words, amounts."""

import pytest

from app.core.models import Product
from app.infrastructure.repository import InMemoryProductRepository
from app.services import answer_polish
from app.services.emergency_triage import build_emergency_triage_response
from app.services.product import find_product_candidates_from_message
from app.services.product_facts import product_facts_text
from app.services.product_search import extract_product_search_query


@pytest.mark.parametrize("message", ["목이 붓고 두드러기가 났어", "목이 붓고 숨이 차", "갑자기 목이 부어올라", "혀가 붓는 것 같아요"])
def test_throat_or_tongue_swelling_is_an_emergency(message):
    response = build_emergency_triage_response(message, profiles=[], has_food_context=False)
    assert response is not None and response.intent == "emergency_safety"


@pytest.mark.parametrize("message", ["목이 부으면 어떻게 해야 해?", "운동했더니 숨이 차", "예전에 목이 부었던 적이 있어"])
def test_hypothetical_or_unrelated_reports_are_not_emergencies(message):
    response = build_emergency_triage_response(message, profiles=[], has_food_context=False)
    assert response is None or response.intent != "emergency_safety"


def test_a_typed_barcode_finds_that_product():
    repo = InMemoryProductRepository()
    product = repo.get_by_barcode("8801043035989") or next(iter(repo.search_candidates("새우깡", limit=1)), None)
    if product is None:
        pytest.skip("seed catalog has no product")
    found = find_product_candidates_from_message(f"{product.barcode} 먹어도 돼?", repository=repo)
    assert [item.barcode for item in found] == [product.barcode]


def test_english_allergy_words_are_not_part_of_the_product_name():
    assert extract_product_search_query("peanut allergy인데 홈런볼 괜찮아?") == "홈런볼"


def test_missing_nutrition_says_what_is_missing():
    product = Product.model_validate({"barcode": "c", "productName": "꼬깔콘"})
    text = product_facts_text(product, "칼로리 얼마야?", "nutrition_explain")
    assert "열량 값이 없어요" in text and "기준량" not in text


def test_vitamin_codes_are_not_values():
    assert answer_polish._numbers("비타민B12, 비타민B6, 나트륨 200mg, 나트륨1,790mg") == {"200", "1790"}


def test_asking_to_check_before_eating_is_not_reassurance():
    from app.core.models import ChatResponse
    original = "오징어집 기준으로 확인했어요. 새우 알레르기 기준으로는 등록된 알레르기 표시(밀, 오징어)에 새우가 없어요. 포장지를 확인해 주세요."
    response = ChatResponse(intent="can_i_eat", answer=original, answer_source="backend", risk_level="unknown")
    asks = "오징어집은 새우 알레르기 기준으로 등록된 알레르기 표시(밀, 오징어)에 새우가 없어요.\n\n드셔도 되는지 드시기 전에 포장지를 확인해 주세요."
    assert answer_polish.draft_keeps_facts(original, asks, response)
    reassures = "오징어집은 새우 알레르기가 있어도 드셔도 괜찮아요. 등록된 알레르기 표시(밀, 오징어)에 새우가 없어요. 포장지도 확인해 주세요."
    assert not answer_polish.draft_keeps_facts(original, reassures, response)


def test_long_decimals_are_rounded():
    product = Product.model_validate({"barcode": "k", "productName": "칙촉", "sugarG": 41.004})
    assert "당류: 41g" in product_facts_text(product, "당류는?", "nutrition_explain")


def test_basis_kind_words_are_not_values():
    from app.core.models import ChatResponse
    original = "칙촉: 41g, 초코파이: 11.7g이에요. 기준량(1회 제공량·100g·총 내용량)이 확인되지 않아 단정하지 않았어요. 포장지를 확인해 주세요."
    response = ChatResponse(intent="food_compare", answer=original, answer_source="rules", risk_level="unknown")
    draft = "칙촉은 41g, 초코파이는 11.7g이에요.\n\n기준량이 확인되지 않아 어느 쪽이 적다고 단정하지 않았어요. 포장지를 확인해 주세요."
    assert answer_polish.draft_keeps_facts(original, draft, response)
