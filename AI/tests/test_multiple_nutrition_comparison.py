import pytest

from app.api.main import chat
from app.core.models import ChatRequest, Profile
from app.infrastructure.repository import InMemoryProductRepository
from app.services.product import get_product_repository, set_product_repository
from test_chatbot import _verified_product


@pytest.fixture
def catalog():
    old = get_product_repository()
    products = [
        _verified_product(name="검사우유", product_id=9001, ingredients="원유", allergy=["우유"],
                          nutrition={"sodium": 40, "protein": 3}, nutrition_basis={"basis_type": "per_100ml"}),
        _verified_product(name="검사귀리음료", product_id=9002, ingredients="귀리, 물",
                          nutrition={"sodium": 20, "protein": 1}, nutrition_basis={"basis_type": "per_100ml"}),
    ]
    set_product_repository(InMemoryProductRepository(seed_data={p.barcode: p.model_dump(mode="json") for p in products}))
    yield products
    set_product_repository(old)


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("axis_phrase", ["단백질과 나트륨을 둘 다", "단백질과 나트륨 기준으로", "단백질, 나트륨으로"])
def test_multiple_axes_keep_product_targets_and_each_value(catalog, explicit, axis_phrase):
    state = {"comparison_status": "active", "last_intent": "food_compare",
             "comparison_products": [{"barcode": p.barcode} for p in catalog],
             "comparison_queries": [p.product_name for p in catalog]}
    question = ("검사우유와 검사귀리음료를 " if explicit else "") + axis_phrase + " 비교해줘"
    response = chat(ChatRequest(message=question, profile=Profile(allergies=["우유"]),
                                conversation_state=None if explicit else state))
    assert response.intent == "food_compare"
    assert [p.barcode for p in response.comparison_products] == [p.barcode for p in catalog]
    assert all(value in response.answer for value in ["단백질", "나트륨", "40", "20", "3g", "1g"])
    assert response.overall_decision == "avoid"  # Nutrition rankings never hide a conflict.
    assert "우유" in response.answer


def test_missing_second_axis_is_disclosed_without_inventing_values(catalog):
    other = _verified_product(name="영양일부누락", product_id=9003, ingredients="귀리, 물",
                              nutrition={"protein": 1}, nutrition_basis={"basis_type": "per_100ml"})
    products = [catalog[0], other]
    set_product_repository(InMemoryProductRepository(seed_data={p.barcode: p.model_dump(mode="json") for p in products}))
    response = chat(ChatRequest(message="단백질과 나트륨으로 비교해줘", profile=Profile(allergies=[]), conversation_state={
        "comparison_status": "active", "last_intent": "food_compare",
        "comparison_products": [{"barcode": p.barcode} for p in products],
        "comparison_queries": [p.product_name for p in products],
    }))
    assert "단백질" in response.answer and "나트륨" in response.answer
    assert any("영양일부누락" in item and "나트륨" in item for item in response.missing_information)
    assert response.assessment_status == "incomplete"


def test_saturated_fat_is_not_replaced_by_total_fat(catalog):
    from app.services.general_handlers import _comparison_axes
    axes = _comparison_axes("지방과 포화지방으로 비교해줘")
    assert [axis[0] for axis in axes] == ["fat", "saturated_fat"]
    # The products have neither value; both missing axes must be disclosed.
    response = chat(ChatRequest(message="지방과 포화지방으로 비교해줘", profile=Profile(allergies=[]), conversation_state={
        "comparison_status": "active", "last_intent": "food_compare",
        "comparison_products": [{"barcode": p.barcode} for p in catalog],
        "comparison_queries": [p.product_name for p in catalog],
    }))
    assert "지방 수치" in response.answer and "포화지방 수치" in response.answer
    assert any("포화지방" in item for item in response.missing_information)


def test_suggested_allergy_comparison_keeps_both_products(catalog):
    response = chat(ChatRequest(message="알레르기 성분까지 포함해서 비교해줘", profile=Profile(allergies=["우유"]), conversation_state={
        "comparison_status": "active", "last_intent": "food_compare",
        "comparison_products": [{"barcode": p.barcode} for p in catalog],
        "comparison_queries": [p.product_name for p in catalog],
    }))
    assert len(response.comparison_products) == 2
    assert response.overall_decision == "avoid"
    assert "비교할 제품 두 개" not in response.answer
    assert "영양 기준을 선택" not in " ".join(response.missing_information)
