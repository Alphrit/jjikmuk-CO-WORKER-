import math
from typing import Iterable, Optional, Tuple

from app.core.models import Product
from app.core.numeric import parse_positive_food_quantity
from app.core.product_provenance import label_provenance_supports


def nutrition_basis_type(product: Product) -> str:
    basis = getattr(product, "nutrition_basis", None)
    basis_type = getattr(basis, "basis_type", "unknown")
    return basis_type if isinstance(basis_type, str) else "unknown"


def has_explicit_nutrition_basis(product: Product) -> bool:
    return nutrition_basis_type(product) != "unknown"


def nutrition_basis_label(product: Product) -> str:
    basis = product.nutrition_basis
    basis_type = nutrition_basis_type(product)

    if basis_type == "per_100g":
        return "100g 기준"
    if basis_type == "per_100ml":
        return "100ml 기준"
    if basis_type == "per_serving":
        size = _formatted_size(basis.serving_size_value, basis.serving_size_unit)
        return f"1회 제공량({size}) 기준" if size else "1회 제공량 기준"
    if basis_type == "per_package":
        size = _formatted_size(basis.package_size_value, basis.package_size_unit)
        return f"총 내용량({size}) 기준" if size else "총 내용량 기준"
    return "영양 수치 기준 미확인"


def nutrition_comparison_signature(product: Product) -> Optional[Tuple[str, float, str]]:
    """Return a strict denominator signature suitable for numeric comparison.

    Fixed 100 g/ml bases are inherently comparable. Serving/package bases are
    comparable only when their explicitly labelled denominator quantities are
    equal. Product names and bare legacy weight fields never supply a basis.
    """

    basis = product.nutrition_basis
    basis_type = nutrition_basis_type(product)
    if basis_type == "per_100g":
        return "per_100", 100.0, "g"
    if basis_type == "per_100ml":
        return "per_100", 100.0, "ml"
    if basis_type == "per_serving":
        if basis.serving_size_value is None or basis.serving_size_unit is None:
            return None
        return "per_serving", float(basis.serving_size_value), basis.serving_size_unit
    if basis_type == "per_package":
        size_value = basis.package_size_value
        size_unit = basis.package_size_unit
        if size_value is None or size_unit is None:
            parsed = parse_positive_food_quantity(product.total_weight)
            if parsed is not None:
                size_value, size_unit = parsed
        if size_value is None or size_unit is None:
            return None
        return "per_package", float(size_value), size_unit
    return None


def nutrition_values_comparable(left: Product, right: Product) -> bool:
    if not label_provenance_supports(
        left,
        ("nutrition", "nutrition_basis"),
    ) or not label_provenance_supports(
        right,
        ("nutrition", "nutrition_basis"),
    ):
        return False
    left_signature = nutrition_comparison_signature(left)
    right_signature = nutrition_comparison_signature(right)
    if left_signature is None or right_signature is None:
        return False
    return (
        left_signature[0] == right_signature[0]
        and left_signature[2] == right_signature[2]
        and math.isclose(left_signature[1], right_signature[1], rel_tol=0.0, abs_tol=1e-9)
    )


def products_have_comparable_nutrition_basis(products: Iterable[Product]) -> bool:
    product_list = list(products)
    if len(product_list) < 2:
        return False
    first = product_list[0]
    return all(nutrition_values_comparable(first, product) for product in product_list[1:])


def _formatted_size(value: Optional[float], unit: Optional[str]) -> Optional[str]:
    if value is None or unit is None:
        return None
    number = float(value)
    formatted = str(int(number)) if number.is_integer() else f"{number:g}"
    return f"{formatted}{unit}"
