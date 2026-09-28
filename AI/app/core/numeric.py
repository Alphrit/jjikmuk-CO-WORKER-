import math
import re
from typing import Any, Optional, Tuple


_NUMBER_TOKEN_PATTERN = re.compile(
    r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
)
_VALUE_WITH_OPTIONAL_UNIT_PATTERN = re.compile(
    r"^\s*(?P<number>[+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+))"
    r"\s*(?P<unit>kcal|cal|mg|g|kg|µg|μg|ug|mcg|%|"
    r"킬로칼로리|칼로리|킬로그램|밀리그램|마이크로그램|그램)?\s*$",
    re.IGNORECASE,
)
_FOOD_QUANTITY_PATTERN = re.compile(
    r"^\s*(?P<number>[+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+))"
    r"\s*(?P<unit>g|kg|ml|l|그램|킬로그램|밀리리터|리터)\s*$",
    re.IGNORECASE,
)


_UNIT_ALIASES = {
    "그램": "g",
    "밀리그램": "mg",
    "킬로그램": "kg",
    "마이크로그램": "ug",
    "µg": "ug",
    "μg": "ug",
    "mcg": "ug",
    "킬로칼로리": "kcal",
    # Korean food labels use "칼로리" to mean nutritional Calories (kcal).
    "칼로리": "kcal",
}

_MASS_TO_GRAMS = {
    "ug": 0.000001,
    "mg": 0.001,
    "g": 1.0,
    "kg": 1000.0,
}

_ENERGY_TO_KCAL = {
    # In food-label input, Cal/cal is treated conservatively as a food Calorie.
    # Interpreting it as 0.001 kcal could dangerously understate energy.
    "cal": 1.0,
    "kcal": 1.0,
}

_FOOD_QUANTITY_UNIT_ALIASES = {
    "g": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "그램": "g",
    "킬로그램": "kg",
    "밀리리터": "ml",
    "리터": "l",
}


def _normalize_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    normalized = unit.strip().lower()
    return _UNIT_ALIASES.get(normalized, normalized)


def _convert_unit(number: float, source_unit: str, expected_unit: str) -> Optional[float]:
    if source_unit == expected_unit:
        return number
    if source_unit in _MASS_TO_GRAMS and expected_unit in _MASS_TO_GRAMS:
        return number * _MASS_TO_GRAMS[source_unit] / _MASS_TO_GRAMS[expected_unit]
    if source_unit in _ENERGY_TO_KCAL and expected_unit in _ENERGY_TO_KCAL:
        return number * _ENERGY_TO_KCAL[source_unit] / _ENERGY_TO_KCAL[expected_unit]
    return None


def parse_non_negative_number(
    value: Any,
    *,
    expected_unit: Optional[str] = None,
) -> Optional[float]:
    """Parse one finite, non-negative numeric value from an API field.

    Unit suffixes such as ``mg`` and ``kcal`` are accepted only when an expected
    unit is supplied. Compatible units are converted and incompatible units are
    rejected. Strings containing more than one number are rejected because
    their serving basis is ambiguous (for example, ``30g당 200mg`` or
    ``100mg (5%)``).
    """

    if value is None or value == "" or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    text = str(value).strip()
    if not text:
        return None

    tokens = _NUMBER_TOKEN_PATTERN.findall(text)
    if len(tokens) != 1:
        return None
    matched_value = _VALUE_WITH_OPTIONAL_UNIT_PATTERN.fullmatch(text)
    if matched_value is None:
        return None

    try:
        number = float(matched_value.group("number").replace(",", ""))
    except (OverflowError, ValueError):
        return None

    if not math.isfinite(number) or number < 0:
        return None
    normalized_expected_unit = _normalize_unit(expected_unit)
    normalized_source_unit = _normalize_unit(matched_value.group("unit"))
    if normalized_source_unit is not None and normalized_expected_unit is None:
        return None
    if normalized_expected_unit is not None and normalized_source_unit is not None:
        converted = _convert_unit(number, normalized_source_unit, normalized_expected_unit)
        if converted is None or not math.isfinite(converted) or converted < 0:
            return None
        return converted
    return number


def parse_positive_food_quantity(value: Any) -> Optional[Tuple[float, str]]:
    """Parse an explicitly unit-labelled food quantity into ``g`` or ``ml``.

    Bare numbers are intentionally rejected. A package weight or serving size is
    useful safety evidence only when its mass/volume unit is explicit; the
    function never guesses whether a number means grams or millilitres.
    """

    if value is None or value == "" or isinstance(value, bool):
        return None
    text = str(value).strip()
    match = _FOOD_QUANTITY_PATTERN.fullmatch(text)
    if match is None:
        return None
    try:
        number = float(match.group("number").replace(",", ""))
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None

    unit = _FOOD_QUANTITY_UNIT_ALIASES.get(match.group("unit").lower())
    if unit == "kg":
        return number * 1000.0, "g"
    if unit == "l":
        return number * 1000.0, "ml"
    if unit in {"g", "ml"}:
        return number, unit
    return None
