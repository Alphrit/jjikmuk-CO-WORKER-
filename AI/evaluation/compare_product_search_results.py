import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_RESULTS_DIR = Path(__file__).with_name("results")
DEFAULT_WATCH_QUERIES = {
    "두유바",
    "두유 바",
    "동원 참치 라이트",
    "나100우유",
    "닥터유 단백질바",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        case["id"]: case
        for case in result.get("cases", [])
        if case.get("id")
    }


def _candidate_names(case: dict[str, Any], limit: int = 5) -> list[str]:
    return [
        candidate.get("product_name") or ""
        for candidate in case.get("candidates", [])[:limit]
    ]


def _candidate_barcodes(case: dict[str, Any], limit: int = 5) -> list[str]:
    return [
        candidate.get("barcode") or ""
        for candidate in case.get("candidates", [])[:limit]
    ]


def _top_name(case: dict[str, Any]) -> str | None:
    names = _candidate_names(case, limit=1)
    return names[0] if names else None


def _changed_case(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_names = _candidate_names(before)
    after_names = _candidate_names(after)
    before_barcodes = _candidate_barcodes(before)
    after_barcodes = _candidate_barcodes(after)
    return {
        "id": before.get("id") or after.get("id"),
        "query": before.get("query") or after.get("query"),
        "before_top1": _top_name(before),
        "after_top1": _top_name(after),
        "before_candidates": before_names,
        "after_candidates": after_names,
        "before_barcodes": before_barcodes,
        "after_barcodes": after_barcodes,
        "top1_changed": _top_name(before) != _top_name(after),
        "top5_changed": before_names != after_names,
        "barcode_order_changed": before_barcodes != after_barcodes,
        "before_top1_ok": before.get("top1_ok"),
        "after_top1_ok": after.get("top1_ok"),
        "before_topk_ok": before.get("topk_ok"),
        "after_topk_ok": after.get("topk_ok"),
        "before_duplicate_failure": not before.get("no_duplicate_candidates", True),
        "after_duplicate_failure": not after.get("no_duplicate_candidates", True),
    }


def compare_results(
    before_path: Path,
    after_path: Path,
    watch_queries: set[str] | None = None,
) -> dict[str, Any]:
    before = _load(before_path)
    after = _load(after_path)
    watch_queries = watch_queries or DEFAULT_WATCH_QUERIES
    before_cases = _case_map(before)
    after_cases = _case_map(after)
    common_ids = sorted(set(before_cases) & set(after_cases))
    missing_in_after = sorted(set(before_cases) - set(after_cases))
    new_in_after = sorted(set(after_cases) - set(before_cases))

    changed = [
        _changed_case(before_cases[case_id], after_cases[case_id])
        for case_id in common_ids
    ]
    top1_changed = [case for case in changed if case["top1_changed"]]
    top5_changed = [case for case in changed if case["top5_changed"]]
    regressions = [
        case
        for case in changed
        if (
            (case["before_top1_ok"] and not case["after_top1_ok"])
            or (case["before_topk_ok"] and not case["after_topk_ok"])
            or (not case["before_duplicate_failure"] and case["after_duplicate_failure"])
        )
    ]
    improvements = [
        case
        for case in changed
        if (
            (not case["before_top1_ok"] and case["after_top1_ok"])
            or (not case["before_topk_ok"] and case["after_topk_ok"])
            or (case["before_duplicate_failure"] and not case["after_duplicate_failure"])
        )
    ]
    watched_cases = [
        case
        for case in changed
        if case.get("query") in watch_queries
    ]

    before_summary = before.get("final_summary", {})
    after_summary = after.get("final_summary", {})
    return {
        "compared_at": datetime.now().isoformat(timespec="seconds"),
        "before_result": str(before_path),
        "after_result": str(after_path),
        "before_summary": before_summary,
        "after_summary": after_summary,
        "summary_delta": {
            "top1_accuracy": round(
                float(after_summary.get("top1_accuracy", 0)) - float(before_summary.get("top1_accuracy", 0)),
                2,
            ),
            "topk_accuracy": round(
                float(after_summary.get("topk_accuracy", 0)) - float(before_summary.get("topk_accuracy", 0)),
                2,
            ),
            "duplicate_candidate_failures": int(after_summary.get("duplicate_candidate_failures", 0))
            - int(before_summary.get("duplicate_candidate_failures", 0)),
        },
        "case_counts": {
            "common": len(common_ids),
            "missing_in_after": len(missing_in_after),
            "new_in_after": len(new_in_after),
            "top1_changed": len(top1_changed),
            "top5_changed": len(top5_changed),
            "regressions": len(regressions),
            "improvements": len(improvements),
            "watched": len(watched_cases),
        },
        "missing_in_after": missing_in_after,
        "new_in_after": new_in_after,
        "regressions": regressions,
        "improvements": improvements,
        "watched_cases": watched_cases,
        "top1_changed_cases": top1_changed,
        "top5_changed_cases": top5_changed,
    }


def save_report(report: dict[str, Any]) -> Path:
    DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_RESULTS_DIR / f"product_search_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report["saved_result"] = str(output_path)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two product search evaluation results.")
    parser.add_argument("before", type=Path, help="Baseline product_search_eval_*.json result.")
    parser.add_argument("after", type=Path, help="New product_search_eval_*.json result.")
    parser.add_argument(
        "--watch-query",
        action="append",
        default=[],
        help="Additional query to include in watched_cases. Can be used multiple times.",
    )
    args = parser.parse_args()

    watch_queries = set(DEFAULT_WATCH_QUERIES)
    watch_queries.update(args.watch_query)
    report = compare_results(args.before, args.after, watch_queries=watch_queries)
    output_path = save_report(report)
    counts = report["case_counts"]
    delta = report["summary_delta"]
    print(f"top1_delta: {delta['top1_accuracy']}%")
    print(f"top5_delta: {delta['topk_accuracy']}%")
    print(f"duplicate_failure_delta: {delta['duplicate_candidate_failures']}")
    print(f"regressions: {counts['regressions']}")
    print(f"improvements: {counts['improvements']}")
    print(f"watched_cases: {counts['watched']}")
    print(f"saved_result: {output_path}")
    return 0 if counts["regressions"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
