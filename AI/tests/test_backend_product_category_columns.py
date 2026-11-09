"""The backend products table stores categories as `food_type` / `food_categories`.

JJIKMUK-Backend migrations V5/V7 and ProductCsvReader fill them; the chatbot must
read them, or category words such as '음료' never match a real catalog row.
"""

import pytest

from app.api.adapters import normalize_product_payload
from app.infrastructure.product_storage.common import PRODUCT_TYPE_COLUMNS, _product_text_score


def backend_row(name, food_type=None, food_categories=None):
    row = {"barcode": "8800000000000", "product_name": name}
    if food_type:
        row["food_type"] = food_type
    if food_categories:
        row["food_categories"] = food_categories
    return normalize_product_payload(row, repository_record=True)


def test_backend_category_columns_are_read():
    product = backend_row("아몬드브리즈 언스위트", "혼합음료", "음료")
    assert product.prdlst_dcnm == "혼합음료"
    assert product.category_large == "음료"
    assert "food_type" in PRODUCT_TYPE_COLUMNS


def test_backend_dto_camel_case_categories_are_read():
    product = normalize_product_payload({"barcode": "1", "productName": "아몬드브리즈 언스위트",
                                         "foodType": "혼합음료", "foodCategories": "음료"})
    assert product.prdlst_dcnm == "혼합음료" and product.category_large == "음료"


@pytest.mark.parametrize("name, food_type, categories, expected", [
    ("아몬드브리즈 언스위트", "혼합음료", "음료", True),
    ("옳곡 피스타치오 스프레드 언스위트", "잼류", "잼/스프레드", False),
    ("제로 콜라", "탄산음료", "음료", False),
])
def test_category_word_in_query_matches_the_catalog_category(name, food_type, categories, expected):
    score = _product_text_score("언스위트 음료", backend_row(name, food_type, categories))
    assert (score >= 0.35) is expected
