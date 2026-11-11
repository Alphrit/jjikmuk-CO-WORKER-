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


def test_backend_main_payload_for_the_latest_db_row():
    # Shape of backend main ProductNutrition.from() for 허니버터칩 in the 2026-11-09 DB
    # (energy_kcal 542, nutrition_basis_raw '100g', total_weight '60g').
    def portion(amount, kcal, sodium):
        return {"amount": amount, "unit": "g", "values": {"energyKcal": kcal, "carbsG": None, "proteinG": None,
                                                          "fatG": None, "sugarG": None, "sodiumMg": sodium,
                                                          "cholesterolMg": None},
                "dailyReferencePercents": {"energyPercent": 16, "sodiumPercent": 11}}
    honey = Product.model_validate({
        "barcode": "8801019611278", "productName": "허니버터칩", "energyKcal": 542.0, "sodiumMg": 383.0,
        "nutrition": {"contractVersion": "nutrition-v1",
                      "sourceBasis": {"raw": "100g", "status": "KNOWN", "amount": 100, "unit": "g"},
                      "sourceValues": portion(100, 542.0, 383.0)["values"],
                      "packageQuantity": {"amount": 60, "unit": "g"},
                      "per100g": portion(100, 542.0, 383.0), "per100ml": None,
                      "perPackage": portion(60, 325.2, 229.8),
                      "packageCalculationStatus": "CALCULATED", "packageCalculationReason": None}})
    text = product_facts_text(honey, "칼로리랑 나트륨 알려줘", "nutrition_explain")
    assert "열량: 542kcal (100g 기준), 한 봉지(총 60g) 환산 325.2kcal" in text
    assert "나트륨: 383mg (100g 기준), 한 봉지(총 60g) 환산 229.8mg" in text
    assert "기준량" not in text.replace("기준)", "")


def test_a_diet_named_in_the_message_is_said_to_be_unapplied(client):
    payload = request_payload(statuses=("PASS",))
    payload["message"] = "비건인데 먹어도 돼?"
    response = client.post("/chat", json=payload, headers=headers(payload))
    assert response.status_code == 200
    assert "식단 조건(비건)은 프로필에 없어 이번 판정에 반영되지 않았어요" in response.json()["answer"]


@pytest.mark.parametrize("message, shown, hidden", [
    ("엄마만 따로 알려줘", "엄마:", "아빠:"),
    ("아빠는 먹어도 돼?", "아빠:", "엄마:"),
])
def test_a_named_person_gets_only_their_result(client, message, shown, hidden):
    payload = request_payload(statuses=("DANGER", "PASS"))
    payload["message"] = message
    answer = client.post("/chat", json=payload, headers=headers(payload)).json()["answer"]
    assert shown in answer and hidden not in answer and "결과만 알려 드릴게요" in answer


def test_both_people_named_get_both_results(client):
    payload = request_payload(statuses=("DANGER", "PASS"))
    payload["message"] = "엄마랑 아빠 먹어도 돼?"
    answer = client.post("/chat", json=payload, headers=headers(payload)).json()["answer"]
    assert "엄마:" in answer and "아빠:" in answer and "결과만" not in answer


def test_me_only_is_a_person_not_a_product_search(client):
    payload = request_payload(statuses=("DANGER", "PASS"))
    payload["message"] = "나만 알려줘"
    body = client.post("/chat", json=payload, headers=headers(payload)).json()
    assert body["intent"] == "can_i_eat" and not body["product_candidates"]


def test_an_amount_question_says_amounts_are_not_judged(client):
    payload = request_payload(statuses=("PASS",))
    payload["message"] = "하루에 몇 개까지 먹어도 돼?"
    answer = client.post("/chat", json=payload, headers=headers(payload)).json()["answer"]
    assert "얼마나 먹어도 되는지는 개인 상태에 따라 달라 안내하지 않아요" in answer


@pytest.mark.parametrize("message", ["새우깡 나트륨 얼마야?", "칙촉 계란 들어 있어?"])
def test_a_newly_named_product_is_not_answered_with_the_kept_one(client, message):
    payload = request_payload(statuses=("PASS",))  # the app kept '합성 시험 우유'
    payload["message"] = message
    body = client.post("/chat", json=payload, headers=headers(payload)).json()
    assert (body.get("current_product") or {}).get("product_name") != "합성 시험 우유"
    assert "합성 시험 우유" not in body["answer"]
