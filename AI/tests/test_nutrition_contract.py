"""Backend nutrition-v1 (fix/nutrition-basis-v17): basis and package values are shown, never re-scaled."""

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.core.models import Product
from app.core.nutrition_basis import nutrition_basis_label
from app.infrastructure.repository import InMemoryActivityRepository, InMemoryProductRepository
from app.services import activity, product as product_service
from app.services.general_handlers import _key_nutrient_overview
from app.services.product_facts import product_facts_text
from app.services.product_search import extract_product_search_query
from backend_v2_helpers import TOKEN, headers, request_payload


def contract(basis_amount=100, unit="g", package=44, per_package=None, per100=None, reason=None):
    return {
        "contractVersion": "nutrition-v1",
        "sourceBasis": {"raw": f"{basis_amount}{unit}", "status": "KNOWN", "amount": basis_amount, "unit": unit},
        "sourceValues": {"energyKcal": 500.0},
        "packageQuantity": {"amount": package, "unit": unit} if package else None,
        "per100g": {"amount": 100, "unit": "g", "values": per100 or {"energyKcal": 500.0}} if unit == "g" else None,
        "per100ml": {"amount": 100, "unit": "ml", "values": per100 or {"energyKcal": 500.0}} if unit == "ml" else None,
        "perPackage": ({"amount": package, "unit": unit, "values": per_package} if per_package else None),
        "packageCalculationStatus": "CALCULATED" if per_package else "UNAVAILABLE",
        "packageCalculationReason": reason,
    }


def test_source_basis_becomes_the_nutrition_basis():
    pocky = Product.model_validate({"barcode": "8801019311673", "productName": "포키 녹차", "energyKcal": 500.0,
                                    "nutrition": contract(per_package={"energyKcal": 220.0})})
    assert pocky.nutrition == {"kcal": 500.0}
    assert nutrition_basis_label(pocky) == "100g 기준"
    assert pocky.nutrition_contract.per_package.values["energyKcal"] == 220.0
    serving = Product.model_validate({"barcode": "2", "productName": "새우깡", "energyKcal": 160.0,
                                      "nutrition": contract(basis_amount=30, package=None, reason="PACKAGE_QUANTITY_UNKNOWN")})
    assert nutrition_basis_label(serving) == "1회 제공량(30g) 기준"
    legacy = Product.model_validate({"barcode": "3", "productName": "허니버터칩", "energyKcal": 325.2})
    assert legacy.nutrition_contract is None and nutrition_basis_label(legacy) == "영양 수치 기준 미확인"


def test_answers_state_the_basis_and_use_backend_package_values():
    pocky = Product.model_validate({"barcode": "1", "productName": "포키 녹차", "energyKcal": 500.0,
                                    "nutrition": contract(per_package={"energyKcal": 220.0})})
    text = product_facts_text(pocky, "칼로리 얼마야?", "nutrition_explain")
    assert "500kcal (100g 기준)" in text and "한 봉지(총 44g) 환산 220kcal" in text
    assert "기준량이 전달되지 않아" not in text
    serving = Product.model_validate({"barcode": "2", "productName": "새우깡", "energyKcal": 160.0,
                                      "nutrition": contract(basis_amount=30, package=None, per100={"energyKcal": 533.3333},
                                                            reason="PACKAGE_QUANTITY_UNKNOWN")})
    text = product_facts_text(serving, "100g당 칼로리 알려줘", "nutrition_explain")
    assert "160kcal (1회 제공량(30g) 기준)" in text and "100g당 환산 533.3kcal" in text
    assert "상품 전체 용량이 없어" in text


def test_without_the_contract_the_answer_is_unchanged_but_names_the_unknown_basis():
    legacy = Product.model_validate({"barcode": "3", "productName": "허니버터칩", "energyKcal": 325.2})
    text = product_facts_text(legacy, "칼로리", "nutrition_explain")
    assert "325.2kcal (기준량 미확인)" in text and "기준량이 전달되지 않아" in text


def test_comparison_uses_a_common_per_100_basis():
    a = Product.model_validate({"barcode": "a", "productName": "새우깡", "energyKcal": 160.0,
                                "nutrition": contract(basis_amount=30, package=None, per100={"energyKcal": 533.33})})
    b = Product.model_validate({"barcode": "b", "productName": "칸쵸", "energyKcal": 259.7,
                                "nutrition": contract(basis_amount=50, package=None, per100={"energyKcal": 519.4})})
    text = _key_nutrient_overview([a, b])
    assert "100g당 환산값으로 맞춰" in text and "새우깡 533.3kcal" in text and "칸쵸 519.4kcal" in text


@pytest.mark.parametrize("message, query", [
    ("새우깡 100g당 칼로리 알려줘", "새우깡"),
    ("매일우유 100ml 기준 당류는?", "매일우유"),
    ("새우깡 1회 제공량 열량", "새우깡"),
    ("포키스탠다드 42g 칼로리", "포키스탠다드 42g"),
])
def test_a_nutrition_basis_is_not_part_of_the_product_name(message, query):
    assert extract_product_search_query(message) == query


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setattr(product_service, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    with TestClient(api.app) as test_client:
        yield test_client
    activity.set_activity_repository(old)


def test_backend_request_with_nutrition_contract_end_to_end(client):
    payload = request_payload(statuses=("PASS",))
    payload["message"] = "칼로리 얼마야?"
    payload["product"]["nutrition"] = contract(basis_amount=100, unit="ml", package=200,
                                               per_package={"energyKcal": 120.0}, per100={"energyKcal": 60.0})
    response = client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "60kcal (100ml 기준)" in answer and "한 봉지(총 200ml) 환산 120kcal" in answer
