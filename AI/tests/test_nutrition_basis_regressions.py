"""Nutrition values are displayed only with their known label denominator."""

from app.core.models import ChatRequest, Product, Profile
from app.core.nutrition_basis import (
    nutrition_basis_label,
    nutrition_values_comparable,
)
from app.main import chat


def test_missing_basis_stays_unknown_instead_of_assuming_one_serving():
    product = Product(product_name="라벨 제품", nutrition={"sodium": 500})

    assert product.nutrition["sodium"] == 500
    assert product.nutrition_basis.basis_type == "unknown"
    assert "기준" in nutrition_basis_label(product)


def test_explicit_basis_is_preserved_without_guessing_from_package_weight():
    product = Product(
        product_name="라벨 제품",
        nutrition={"sodium": 120},
        total_content=240,
        nutrition_basis={
            "basis_type": "per_serving",
            "serving_size": "30g",
            "basis_source": "label",
        },
    )

    assert product.nutrition_basis.basis_type == "per_serving"
    assert product.nutrition_basis.serving_size_value == 30
    assert product.nutrition_basis.serving_size_unit == "g"
    assert product.nutrition_basis.basis_source == "label"


def test_products_are_comparable_only_on_matching_explicit_denominators():
    per_serving = Product(
        product_name="A",
        nutrition={"sodium": 100},
        nutrition_basis={
            "basis_type": "per_serving",
            "serving_size": "30g",
            "basis_source": "label",
        },
    )
    same = Product(
        product_name="B",
        nutrition={"sodium": 200},
        nutrition_basis={
            "basis_type": "per_serving",
            "serving_size": "30g",
            "basis_source": "label",
        },
    )
    different = Product(
        product_name="C",
        nutrition={"sodium": 50},
        nutrition_basis={
            "basis_type": "per_100g",
            "basis_source": "label",
        },
    )

    # Matching denominators alone are not enough: both exact label snapshots
    # must also have nutrition provenance before numeric comparison is allowed.
    assert nutrition_values_comparable(per_serving, same) is False
    assert nutrition_values_comparable(per_serving, different) is False


def test_allergy_decision_is_not_weakened_by_missing_nutrition_basis():
    response = chat(ChatRequest(
        message="이 제품 먹어도 돼?",
        barcode="8801043035989",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
