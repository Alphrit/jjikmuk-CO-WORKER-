import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from app.api.main import configure_repositories  # noqa: E402
from app.services.product import find_product_candidates_from_message, get_product_repository  # noqa: E402
from app.services.product_search import extract_product_search_query, product_display_key  # noqa: E402


DEFAULT_CASES_PATH = Path(__file__).with_name("product_search_cases.json")
DEFAULT_RESULTS_DIR = Path(__file__).with_name("results")


def evaluate_case(case: Dict[str, Any], limit: int) -> Dict[str, Any]:
    repository = get_product_repository()
    mode = case.get("mode", "query")
    if mode == "message":
        search_query = extract_product_search_query(case["query"])
        candidates = find_product_candidates_from_message(
            case["query"],
            limit=limit,
            repository=repository,
        )
    else:
        search_query = case["query"]
        candidates = repository.search_candidates(search_query, limit=limit)
    names = [candidate.product_name for candidate in candidates if candidate.product_name]
    display_keys = [
        product_display_key(candidate)
        for candidate in candidates
        if product_display_key(candidate)
    ]
    duplicate_display_keys = sorted({
        key
        for key in display_keys
        if display_keys.count(key) > 1
    })
    expected_any = set(case.get("expected_any") or [])
    expected_top1 = case.get("expected_top1")
    expected_top1_any = set(case.get("expected_top1_any") or [])
    expected_name_contains_all = case.get("expected_name_contains_all") or []
    every_candidate_name_contains_all = case.get("every_candidate_name_contains_all") or []
    forbidden_name_contains_any = case.get("forbidden_name_contains_any") or []
    expected_search_query = case.get("expected_search_query")
    expected_no_candidates = bool(case.get("expected_no_candidates", False))

    def name_contains_expected_terms(name: str) -> bool:
        return all(term in name for term in expected_name_contains_all)

    if expected_top1:
        top1_ok = bool(names) and names[0] == expected_top1
    elif expected_top1_any:
        top1_ok = bool(names) and names[0] in expected_top1_any
    elif expected_name_contains_all:
        top1_ok = bool(names) and name_contains_expected_terms(names[0])
    else:
        top1_ok = True

    if expected_any:
        topk_ok = bool(expected_any & set(names))
    elif expected_name_contains_all:
        topk_ok = any(name_contains_expected_terms(name) for name in names)
    else:
        topk_ok = True
    no_duplicate_candidates = not duplicate_display_keys
    forbidden_candidate_names = [
        name
        for name in names
        if any(term in name for term in forbidden_name_contains_any)
    ]
    no_forbidden_candidates = not forbidden_candidate_names
    extraction_ok = expected_search_query is None or search_query == expected_search_query
    all_candidates_relevant = (
        bool(names)
        and all(
            all(term in name for term in every_candidate_name_contains_all)
            for name in names
        )
        if every_candidate_name_contains_all
        else True
    )
    no_candidates_ok = not names if expected_no_candidates else True
    return {
        "id": case["id"],
        "mode": mode,
        "query": case["query"],
        "search_query": search_query,
        "expected_top1": expected_top1,
        "expected_top1_any": sorted(expected_top1_any),
        "expected_any": sorted(expected_any),
        "expected_name_contains_all": expected_name_contains_all,
        "every_candidate_name_contains_all": every_candidate_name_contains_all,
        "expected_search_query": expected_search_query,
        "expected_no_candidates": expected_no_candidates,
        "extraction_ok": extraction_ok,
        "top1_ok": top1_ok,
        "topk_ok": topk_ok,
        "all_candidates_relevant": all_candidates_relevant,
        "no_candidates_ok": no_candidates_ok,
        "no_duplicate_candidates": no_duplicate_candidates,
        "no_forbidden_candidates": no_forbidden_candidates,
        "forbidden_candidate_names": forbidden_candidate_names,
        "duplicate_display_keys": duplicate_display_keys,
        "candidates": [
            {
                "product_name": candidate.product_name,
                "barcode": candidate.barcode,
                "display_key": product_display_key(candidate),
                "match_confidence": candidate.match_confidence,
            }
            for candidate in candidates
        ],
    }


def run_eval(cases_path: Path, limit: int = 5) -> tuple[Dict[str, Any], Path]:
    configure_repositories()
    repository = get_product_repository()
    readiness_check = getattr(repository, "readiness_check", None)
    diagnostics = {
        "product_repository": type(repository).__name__,
        "product_repository_health": (
            readiness_check()
            if callable(readiness_check)
            else {"available": False}
        ),
    }
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = [evaluate_case(case, limit=limit) for case in cases]
    total = len(results)
    top1_passed = sum(1 for result in results if result["top1_ok"])
    topk_passed = sum(1 for result in results if result["topk_ok"])
    duplicate_failures = [
        result
        for result in results
        if not result["no_duplicate_candidates"]
    ]
    failed_search_cases = [
        result
        for result in results
        if (
            not result["top1_ok"]
            or not result["topk_ok"]
            or not result["extraction_ok"]
            or not result["all_candidates_relevant"]
            or not result["no_candidates_ok"]
            or not result["no_forbidden_candidates"]
        )
    ]
    extraction_results = [
        result
        for result in results
        if result["expected_search_query"] is not None
    ]
    extraction_passed = sum(1 for result in extraction_results if result["extraction_ok"])
    candidate_integrity_failures = [
        result
        for result in results
        if not result["all_candidates_relevant"] or not result["no_candidates_ok"]
    ]

    output = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases_path": str(cases_path),
        "limit": limit,
        "diagnostics": {
            "product_repository": diagnostics.get("product_repository"),
            "product_repository_health": diagnostics.get("product_repository_health"),
        },
        "cases": results,
        "final_summary": {
            "total_cases": total,
            "top1_passed": top1_passed,
            "top1_accuracy": round((top1_passed / total) * 100, 2) if total else 0.0,
            "topk_passed": topk_passed,
            "topk_accuracy": round((topk_passed / total) * 100, 2) if total else 0.0,
            "extraction_cases": len(extraction_results),
            "extraction_passed": extraction_passed,
            "extraction_accuracy": round(
                (extraction_passed / len(extraction_results)) * 100,
                2,
            ) if extraction_results else 0.0,
            "candidate_integrity_failures": len(candidate_integrity_failures),
            "duplicate_candidate_failures": len(duplicate_failures),
            "failed_case_ids": [
                result["id"]
                for result in failed_search_cases
            ],
            "duplicate_case_ids": [result["id"] for result in duplicate_failures],
            "failed_search_cases": [
                {
                    "id": result["id"],
                    "query": result["query"],
                    "search_query": result["search_query"],
                    "expected_top1": result["expected_top1"],
                    "expected_top1_any": result["expected_top1_any"],
                    "expected_any": result["expected_any"],
                    "expected_name_contains_all": result["expected_name_contains_all"],
                    "expected_search_query": result["expected_search_query"],
                    "extraction_ok": result["extraction_ok"],
                    "all_candidates_relevant": result["all_candidates_relevant"],
                    "no_candidates_ok": result["no_candidates_ok"],
                    "candidate_names": [
                        candidate["product_name"]
                        for candidate in result["candidates"]
                    ],
                    "duplicate_display_keys": result["duplicate_display_keys"],
                    "forbidden_candidate_names": result["forbidden_candidate_names"],
                }
                for result in failed_search_cases[:20]
            ],
        },
    }

    DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_RESULTS_DIR / f"product_search_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run product-name search accuracy evaluation.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    result, output_path = run_eval(args.cases, limit=args.limit)
    summary = result["final_summary"]
    print(f"top1_accuracy: {summary['top1_accuracy']}% ({summary['top1_passed']}/{summary['total_cases']})")
    print(f"top{args.limit}_accuracy: {summary['topk_accuracy']}% ({summary['topk_passed']}/{summary['total_cases']})")
    print(f"extraction_accuracy: {summary['extraction_accuracy']}% ({summary['extraction_passed']}/{summary['extraction_cases']})")
    print(f"candidate_integrity_failures: {summary['candidate_integrity_failures']}")
    if summary["failed_case_ids"]:
        print("failed_case_ids:", ", ".join(summary["failed_case_ids"]))
    if summary["duplicate_case_ids"]:
        print("duplicate_case_ids:", ", ".join(summary["duplicate_case_ids"]))
    print(f"saved_result: {output_path}")
    return 0 if not summary["failed_case_ids"] and not summary["duplicate_case_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
