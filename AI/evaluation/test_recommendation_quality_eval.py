import json

from evaluation.run_recommendation_quality_eval import (
    DEFAULT_CASES_PATH,
    evaluate_case,
)


def _cases_by_id():
    cases = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def test_recommendation_quality_contract_has_traced_and_fail_closed_cases():
    cases = _cases_by_id()

    for case_id in (
        "recommend_quality_0001",
        "recommend_quality_0002",
        "recommend_quality_0003",
        "recommend_quality_0004",
    ):
        assert case_id in cases
        assert "evaluation_label_fixture" in cases[case_id]["current_product"]

    untraced = cases["recommend_quality_0005"]
    untraced_candidates = [
        product
        for key, product in untraced["repository_seed"].items()
        if key != untraced["current_product"]["barcode"]
    ]
    assert untraced_candidates
    assert all(
        "evaluation_label_fixture" not in product
        for product in untraced_candidates
    )

def test_untraced_allergy_candidate_returns_no_product_card():
    case = _cases_by_id()["recommend_quality_0005"]

    result = evaluate_case(case)

    assert result["passed"] is True
    assert result["actual"]["recommendation_names"] == []
    assert result["actual"]["top_recommendation"] == {}


def test_all_recommendation_quality_cases_pass_the_declared_contract():
    results = [evaluate_case(case) for case in _cases_by_id().values()]

    assert len(results) == 5
    assert [result["id"] for result in results if not result["passed"]] == []
