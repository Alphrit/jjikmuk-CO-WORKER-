import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from app.api.adapters import normalize_product_payload, normalize_profile_payload
from app.infrastructure.repository import InMemoryProductRepository
from app.services.product import recommend_alternative_products
from evaluation.product_label_fixtures import (
    materialize_evaluation_product_label,
    materialize_evaluation_repository_seed,
    synthetic_evaluation_label_source_registry,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("recommendation_quality_cases.json")
DEFAULT_RESULTS_DIR = Path(__file__).with_name("results")


def _recommendation_names(recommendations: List[Dict[str, Any]]) -> List[str]:
    return [
        str(item.get("product_name") or "")
        for item in recommendations
        if item.get("product_name")
    ]


def _contains_all(text: str, terms: List[str]) -> bool:
    return all(term in text for term in terms)


def _has_grounded_recommendation_reason(item: Dict[str, Any]) -> bool:
    reason_text = " ".join([
        str(item.get("reason") or ""),
        " ".join(str(reason) for reason in item.get("reasons") or []),
    ])
    comparison = item.get("nutrition_comparison") or {}
    basis = item.get("recommendation_basis") or {}
    return bool(
        reason_text.strip()
        and (
            comparison
            or basis.get("comparison_basis")
            or "같은 제품 유형" in reason_text
            or "대체 카테고리" in reason_text
            or "알레르기" in reason_text
        )
    )


def _recommend_for_scenario(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    with synthetic_evaluation_label_source_registry():
        repository = InMemoryProductRepository(
            seed_data=materialize_evaluation_repository_seed(
                scenario["repository_seed"]
            )
        )
        product = normalize_product_payload(
            materialize_evaluation_product_label(scenario["current_product"])
        )
        profile = normalize_profile_payload(scenario.get("profile") or {})
        return recommend_alternative_products(
            product=product,
            profile=profile,
            repository=repository,
        )


def _recommendation_explanation_text(
    recommendations: List[Dict[str, Any]],
) -> str:
    parts: List[str] = []
    for item in recommendations:
        parts.extend([
            str(item.get("reason") or ""),
            str(item.get("caution") or ""),
            " ".join(str(reason) for reason in item.get("reasons") or []),
            " ".join(str(caution) for caution in item.get("cautions") or []),
        ])
    return " ".join(parts)


def evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    recommendations = _recommend_for_scenario(case)

    names = _recommendation_names(recommendations)
    first = recommendations[0] if recommendations else {}
    first_reason = str(first.get("reason") or "")
    all_reason_text = " ".join(
        [
            str(item.get("reason") or "")
            for item in recommendations
        ]
    )
    all_names_text = " ".join(names)
    failures: List[str] = []
    actual_recommendations = [
        item
        for item in recommendations
        if item.get("product_name")
    ]

    min_recommendations = int(case.get("min_recommendations") or 0)
    if len(names) < min_recommendations:
        failures.append(f"expected at least {min_recommendations} recommendations, got {len(names)}")

    expected_top1 = case.get("expected_top1")
    if expected_top1 and (not names or names[0] != expected_top1):
        failures.append(f"expected top1 {expected_top1}, got {names[0] if names else None}")

    expected_terms = case.get("expected_recommendation_terms") or []
    if expected_terms and not any(term in all_names_text for term in expected_terms):
        failures.append(f"recommendations missing expected terms {expected_terms}")

    forbidden_terms = case.get("forbidden_recommendation_terms") or []
    forbidden_hits = [term for term in forbidden_terms if term in all_names_text]
    if forbidden_hits:
        failures.append(f"forbidden recommendation terms present {forbidden_hits}")

    required_reason_terms = case.get("required_reason_terms") or []
    if required_reason_terms and not _contains_all(f"{first_reason} {all_reason_text}", required_reason_terms):
        failures.append(f"recommendation reason missing terms {required_reason_terms}")

    comparison = first.get("nutrition_comparison") or {}
    required_comparison_keys = case.get("required_comparison_keys") or []
    missing_comparison_keys = [
        key
        for key in required_comparison_keys
        if key not in comparison
    ]
    if missing_comparison_keys:
        failures.append(f"missing comparison keys {missing_comparison_keys}")

    expect_unavailable = bool(case.get("expect_unavailable"))
    recommendation_available = first.get("recommendation_basis", {}).get("recommendation_available", bool(names))
    if expect_unavailable and recommendation_available is not False:
        failures.append("expected recommendation_available=false")
    if expect_unavailable and first.get("product_name") is not None:
        failures.append(f"expected no product recommendation, got {first.get('product_name')}")

    explanation_text = _recommendation_explanation_text(recommendations)
    required_explanation_terms = case.get("required_explanation_terms") or []
    explanation_terms_ok = (
        not required_explanation_terms
        or _contains_all(explanation_text, required_explanation_terms)
    )
    if not explanation_terms_ok:
        failures.append(
            "recommendation explanation missing terms "
            f"{required_explanation_terms}"
        )

    reasonless_items = [
        str(item.get("product_name"))
        for item in actual_recommendations
        if not _has_grounded_recommendation_reason(item)
    ]
    if reasonless_items:
        failures.append(f"recommendations missing DB-grounded reasons {reasonless_items}")

    missing_basis_items = [
        str(item.get("product_name"))
        for item in actual_recommendations
        if not item.get("recommendation_basis")
    ]
    if missing_basis_items:
        failures.append(f"recommendations missing recommendation_basis {missing_basis_items}")

    checks = {
        "recommendation_count": len(names) >= min_recommendations,
        "top1_match": not expected_top1 or (bool(names) and names[0] == expected_top1),
        "expected_terms_present": not expected_terms or any(term in all_names_text for term in expected_terms),
        "forbidden_terms_absent": not forbidden_hits,
        "reason_grounded": not required_reason_terms or _contains_all(f"{first_reason} {all_reason_text}", required_reason_terms),
        "comparison_keys_present": not missing_comparison_keys,
        "unavailable_handled": not expect_unavailable or (recommendation_available is False and first.get("product_name") is None),
        "each_recommendation_has_db_grounded_reason": not reasonless_items,
        "basis_metadata_present": not missing_basis_items,
        "required_explanation_terms_present": explanation_terms_ok,
    }

    return {
        "id": case["id"],
        "category": case.get("category", "recommendation_quality"),
        "description": case.get("description"),
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "actual": {
            "recommendation_names": names,
            "top_recommendation": first,
        },
    }


def run_eval(cases_path: Path) -> tuple[Dict[str, Any], Path]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = [evaluate_case(case) for case in cases]
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    category_totals: Dict[str, List[bool]] = {}
    for result in results:
        category_totals.setdefault(result["category"], []).append(result["passed"])

    output = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases_path": str(cases_path),
        "cases": results,
        "final_summary": {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": total - passed,
            "pass_rate": round((passed / total) * 100, 2) if total else 0.0,
            "categories": {
                category: {
                    "passed": sum(1 for item in values if item),
                    "total": len(values),
                    "pass_rate": round((sum(1 for item in values if item) / len(values)) * 100, 2),
                }
                for category, values in sorted(category_totals.items())
            },
            "failed_case_ids": [
                result["id"]
                for result in results
                if not result["passed"]
            ],
        },
    }

    DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_RESULTS_DIR / f"recommendation_quality_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output["saved_result"] = str(output_path)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run recommendation quality evaluation cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args()

    result, output_path = run_eval(args.cases)
    summary = result["final_summary"]
    print(f"pass_rate: {summary['pass_rate']}% ({summary['passed_cases']}/{summary['total_cases']})")
    if summary["failed_case_ids"]:
        print("failed_case_ids:", ", ".join(summary["failed_case_ids"]))
    print(f"saved_result: {output_path}")
    return 0 if not summary["failed_case_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
