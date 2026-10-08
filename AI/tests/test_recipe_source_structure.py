import pytest

from app.services.recipe_source_structure import structure_ingredients


def test_parses_groups_parenthetic_measures_and_explicit_servings():
    raw = "[1인분]●주재료 : 연두부 75g(3/4모), 새우 20g(5마리)\n●고명 : 시금치 10g(3줄기)"
    result = structure_ingredients(raw)
    assert result["original"] == raw
    assert result["servings_explicit"] == 1
    assert result["parse_status"] == "parsed"
    assert [item["name"] for item in result["ingredients"]] == ["연두부", "새우", "시금치"]
    assert [item["group"] for item in result["ingredients"]] == ["주재료", "주재료", "고명"]
    assert result["ingredients"][0]["amount_text"] == "75g(3/4모)"
    assert result["ingredients"][0]["quantity_text"] == "75"
    assert result["recommendation_eligible"] is False
    assert result["content_review"] == "pending"


def test_unknown_heading_and_ingredient_without_amount_are_not_discarded():
    result = structure_ingredients("처음보는제목\n두부 100g, 양파, 마늘 2g")
    assert result["unparsed"] == ["처음보는제목", "양파"]
    assert "unparsed_ingredient_text" in result["issues"]
    assert "servings_unconfirmed" in result["issues"]
    assert [item["name"] for item in result["ingredients"]] == ["두부", "마늘"]


def test_recipe_name_header_is_preserved_as_heading():
    result = structure_ingredients("새우두부찜\n두부100g, 새우20g\n고명\n시금치10g", "새우 두부 찜")
    assert result["unparsed"] == []
    assert [item["group"] for item in result["ingredients"]] == ["새우두부찜", "새우두부찜", "고명"]
    assert result["servings_explicit"] is None


@pytest.mark.parametrize("amount", ["약간", "조금", "적당량", "소량", "취향껏"])
def test_vague_amount_is_not_invented(amount):
    result = structure_ingredients(f"[1인분]소금 {amount}")
    assert result["ingredients"][0]["amount_status"] == "vague"
    assert result["ingredients"][0]["quantity_text"] is None
    assert result["ingredients"][0]["amount_text"] == amount
    assert result["parse_status"] == "needs_review"


@pytest.mark.parametrize("text", [
    "소금 -1g", "소금 0g", "소금 1/0g", "소금 1~2g", "소금 약 2g",
    "소금 2g 또는 3g", "양파/마늘 각 10g", "소금 99999999g",
    "달걀 1개, 마늘", "물 1컵(200ml", "재료 " + "1" * 1000,
])
def test_ambiguous_or_invalid_tokens_are_retained_for_review(text):
    result = structure_ingredients(text)
    assert result["unparsed"]
    assert result["recommendation_eligible"] is False


def test_commas_inside_parentheses_do_not_create_extra_ingredients():
    result = structure_ingredients("[2인분]두부 100g(물기 제거, 으깬 것), 당근 1/2개")
    assert len(result["ingredients"]) == 2
    assert result["ingredients"][1]["quantity_text"] == "1/2"
    assert result["servings_explicit"] == 2


def test_parentheses_in_ingredient_name_and_dimensions_remain_original():
    result = structure_ingredients("황태(채) 15g(10개), 무 30g(5×3×2cm)")
    assert result["ingredients"][0]["name"] == "황태(채)"
    assert result["ingredients"][1]["measure_note"] == "(5×3×2cm)"


def test_more_than_response_ingredient_limit_is_not_silently_truncated():
    text = ", ".join(f"재료{chr(65 + i // 26)}{chr(65 + i % 26)} 1g" for i in range(31))
    result = structure_ingredients(text)
    assert len(result["ingredients"]) == 31
    assert "too_many_ingredients_for_response" in result["issues"]


@pytest.mark.parametrize("text", ["", " ", "가" * 20_001, None])
def test_input_bounds(text):
    with pytest.raises(ValueError):
        structure_ingredients(text)
