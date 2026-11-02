import json

import pytest

from evaluation.generate_cases import (
    OUTPUT_PATH,
    REQUIRED_CATEGORIES,
    build_cases,
    build_cases_with_report,
    case_fingerprint,
    deduplicate_cases,
    main,
    validate_case_contract,
)


def _case(case_id: str, message: str = "이거 먹어도 돼?"):
    return {
        "id": case_id,
        "category": "product_can_i_eat",
        "message": message,
    }


def test_fingerprint_ignores_only_case_id():
    first = _case("a")
    assert case_fingerprint(first) == case_fingerprint({**first, "id": "b"})
    assert case_fingerprint(first) != case_fingerprint(_case("b", "섭취해도 돼?"))


def test_deduplicate_cases_is_deterministic():
    first = _case("a")
    unique, duplicates = deduplicate_cases([first, {**first, "id": "b"}])
    assert unique == [first]
    assert duplicates[0]["kept_id"] == "a"
    assert duplicates[0]["dropped_id"] == "b"


def test_generated_cases_are_unique_and_cover_current_scope():
    cases, report = build_cases_with_report()
    assert cases == build_cases()
    assert report["seed_count"] == report["unique_count"] == len(cases)
    assert report["duplicates_removed"] == 0
    assert set(report["category_counts"]) == REQUIRED_CATEGORIES


def test_emergency_cases_are_allergy_reaction_only():
    cases = [case for case in build_cases() if case["category"] == "allergy_emergency"]
    assert cases
    assert all(case["expected_intent"] == "emergency_safety" for case in cases)
    assert all("119" in case["must_contain"] for case in cases)
    assert not any(
        term in case["message"]
        for case in cases
        for term in ["뇌졸중", "흉통", "건전지", "약"]
    )


def test_general_diet_cases_match_the_seven_supported_conditions():
    messages = {
        case["message"].split()[0]
        for case in build_cases()
        if case["category"] == "general_diet"
    }
    assert messages == {"채식", "저당", "저염", "글루텐프리", "저칼로리", "저지방", "고단백"}


def test_contract_rejects_duplicate_behavior():
    first = _case("a")
    with pytest.raises(ValueError, match="duplicate behavioral payloads"):
        validate_case_contract([first, {**first, "id": "b"}])


def test_checked_in_case_file_matches_generator():
    assert json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) == build_cases()


def test_cli_write_and_check_share_contract(tmp_path):
    output = tmp_path / "cases.json"
    assert main(["--output", str(output)]) == 0
    assert main(["--output", str(output), "--check"]) == 0
