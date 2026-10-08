import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

# Keep this test deterministic and avoid multiplying live LLM calls under concurrency.
os.environ["OPENAI_API_KEY"] = ""
os.environ["FOOD_CHATBOT_ENABLE_EXTERNAL_AI"] = "false"
os.environ["FOOD_CHATBOT_API_TOKEN"] = ""
os.environ["FOOD_CHATBOT_ENABLE_RATE_LIMIT"] = "false"
os.environ["FOOD_CHATBOT_ENV"] = "test"
os.environ["FOOD_CHATBOT_DB_PROVIDER"] = "memory"
os.environ["FOOD_CHATBOT_ENABLE_CONVERSATIONS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.api.response_contract import PUBLIC_CHAT_FIELDS  # noqa: E402
from app.infrastructure.repository import (  # noqa: E402
    InMemoryActivityRepository,
    InMemoryProductRepository,
)
from app.services.activity import get_activity_repository, set_activity_repository  # noqa: E402
from app.services.product import get_product_repository, set_product_repository  # noqa: E402
from evaluation.product_label_fixtures import (  # noqa: E402
    materialize_evaluation_repository_seed,
    synthetic_evaluation_label_source_registry,
)
from evaluation.run_eval import _case_payload, evaluate_case  # noqa: E402


CASES_PATH = Path(__file__).with_name("test_cases.json")
RESULTS_DIR = Path(__file__).with_name("results")
DEFAULT_CASE_IDS = [
    "out_of_domain_0001",
    "missing_product_0001",
    "product_can_i_eat_0001",
    "recommendation_0001",
]
REQUIRED_RESPONSE_FIELDS = {
    "task_type",
    "answer_source",
    "intent",
    "answer",
    "risk_level",
    "assessment_status",
    "conversation_state",
    "recommended_questions",
    "product_candidates",
    "recommended_products",
    "sources",
}


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 2)


def _load_cases() -> List[Dict[str, Any]]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}
    missing_ids = [case_id for case_id in DEFAULT_CASE_IDS if case_id not in by_id]
    if missing_ids:
        raise RuntimeError(f"Missing stability cases: {missing_ids}")
    return [by_id[case_id] for case_id in DEFAULT_CASE_IDS]


def _contract_failures(status_code: int, body: Any) -> List[str]:
    if status_code != 200:
        return [f"expected HTTP 200, got {status_code}"]
    if not isinstance(body, dict):
        return ["response is not a JSON object"]

    failures = []
    missing_fields = sorted(REQUIRED_RESPONSE_FIELDS - set(body))
    if missing_fields:
        failures.append(f"missing response fields: {missing_fields}")
    private_fields = sorted(set(body) - set(PUBLIC_CHAT_FIELDS))
    if private_fields:
        failures.append(f"non-public response fields: {private_fields}")
    if not isinstance(body.get("answer"), str) or not body.get("answer", "").strip():
        failures.append("answer is empty or not a string")
    if not isinstance(body.get("conversation_state"), dict):
        failures.append("conversation_state is not an object")
    for field in ["recommended_questions", "product_candidates", "recommended_products", "sources"]:
        if not isinstance(body.get(field), list):
            failures.append(f"{field} is not a list")
    return failures


def _run_request(client: TestClient, case: Dict[str, Any], sequence: int) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.post("/chat", json=_case_payload(case))
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        contract_failures = _contract_failures(response.status_code, body)
        evaluation = evaluate_case(case, body, response.status_code)
        failures = [*contract_failures, *evaluation.get("failures", [])]
        return {
            "sequence": sequence,
            "case_id": case["id"],
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "passed": not failures,
            "contract_passed": not contract_failures,
            "scenario_passed": evaluation.get("passed", False),
            "failures": failures,
            "actual": {
                "intent": body.get("intent") if isinstance(body, dict) else None,
                "task_type": body.get("task_type") if isinstance(body, dict) else None,
                "answer_source": body.get("answer_source") if isinstance(body, dict) else None,
                "product_name": ((body.get("current_product") or {}).get("product_name"))
                if isinstance(body, dict)
                else None,
            },
        }
    except Exception as exc:
        return {
            "sequence": sequence,
            "case_id": case["id"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "status_code": None,
            "passed": False,
            "contract_passed": False,
            "scenario_passed": False,
            "failures": [f"unhandled client exception: {type(exc).__name__}: {exc}"],
            "actual": {},
        }


def _verify_invalid_requests(client: TestClient) -> List[Dict[str, Any]]:
    payloads = [
        {},
        {"message": None},
        {"message": ["invalid"]},
        {"message": "오늘 날씨가 어때?", "chat_history": "invalid"},
        {"message": "x" * 4001},
        {
            "message": "이거 먹어도 돼?",
            "chat_history": [{"role": "user", "content": "이전 질문"}] * 51,
        },
    ]
    results = []
    for index, payload in enumerate(payloads, start=1):
        response = client.post("/chat", json=payload)
        results.append(
            {
                "id": f"invalid_request_{index}",
                "status_code": response.status_code,
                "passed": response.status_code == 422,
            }
        )
    return results


def _verify_conversation_state(client: TestClient) -> Dict[str, Any]:
    first = client.post(
        "/chat",
        json={
            "message": "이거 먹어도 돼?",
            "barcode": "8801043035989",
            "profile": {"id": 1, "nickname": "테스트"},
        },
    )
    first_body = first.json() if first.status_code == 200 else {}
    second = client.post(
        "/chat",
        json={
            "message": "그럼 나트륨은 어때?",
            "profile": {"id": 1, "nickname": "테스트"},
            "conversation_state": first_body.get("conversation_state") or {},
            "chat_history": [
                {"role": "user", "content": "이거 먹어도 돼?"},
                {"role": "assistant", "content": first_body.get("answer") or ""},
            ],
        },
    )
    second_body = second.json() if second.status_code == 200 else {}
    first_product = (first_body.get("current_product") or {}).get("product_name")
    second_product = (second_body.get("current_product") or {}).get("product_name")
    failures = []
    if first.status_code != 200 or second.status_code != 200:
        failures.append(f"unexpected status codes: first={first.status_code}, second={second.status_code}")
    if not first_product:
        failures.append("first response did not resolve a product")
    if second_product != first_product:
        failures.append(f"product context changed: first={first_product}, second={second_product}")
    if second_body.get("intent") != "nutrition_explain":
        failures.append(f"follow-up intent expected nutrition_explain, got {second_body.get('intent')}")
    return {
        "passed": not failures,
        "failures": failures,
        "first_product": first_product,
        "second_product": second_product,
        "follow_up_intent": second_body.get("intent"),
        "conversation_state_keys": sorted((second_body.get("conversation_state") or {}).keys()),
    }


def run_stability_eval(request_count: int, workers: int, p95_target_ms: float) -> Dict[str, Any]:
    cases = _load_cases()
    scheduled_cases = [cases[index % len(cases)] for index in range(request_count)]
    combined_seed: Dict[str, Any] = {}
    for case in cases:
        repository_seed = case.get("repository_seed")
        if isinstance(repository_seed, dict):
            combined_seed.update(repository_seed)
    evaluation_products = InMemoryProductRepository(
        seed_data=materialize_evaluation_repository_seed(combined_seed)
    )
    started = time.perf_counter()
    with synthetic_evaluation_label_source_registry():
        with TestClient(app) as client:
            original_activity_repository = get_activity_repository()
            original_product_repository = get_product_repository()
            set_activity_repository(InMemoryActivityRepository())
            set_product_repository(evaluation_products)
            try:
                invalid_request_results = _verify_invalid_requests(client)
                conversation_state_result = _verify_conversation_state(client)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [
                        executor.submit(_run_request, client, case, sequence)
                        for sequence, case in enumerate(scheduled_cases, start=1)
                    ]
                    request_results = [
                        future.result()
                        for future in as_completed(futures)
                    ]
            finally:
                set_product_repository(original_product_repository)
                set_activity_repository(original_activity_repository)

    elapsed_seconds = round(time.perf_counter() - started, 3)
    request_results.sort(key=lambda item: item["sequence"])
    latencies = [result["latency_ms"] for result in request_results]
    failed_requests = [result for result in request_results if not result["passed"]]
    invalid_failures = [result for result in invalid_request_results if not result["passed"]]
    p95_ms = _percentile(latencies, 0.95)
    functional_passed = not failed_requests and not invalid_failures and conversation_state_result["passed"]

    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "Concurrent FastAPI /chat contract, routing, context, and latency smoke test",
        "configuration": {
            "request_count": request_count,
            "workers": workers,
            "case_ids": DEFAULT_CASE_IDS,
            "llm_disabled": True,
            "activity_repository": "memory",
            "p95_target_ms": p95_target_ms,
        },
        "latency": {
            "elapsed_seconds": elapsed_seconds,
            "throughput_requests_per_second": round(request_count / elapsed_seconds, 2) if elapsed_seconds else 0.0,
            "min_ms": round(min(latencies), 2) if latencies else 0.0,
            "mean_ms": round(mean(latencies), 2) if latencies else 0.0,
            "median_ms": round(median(latencies), 2) if latencies else 0.0,
            "p95_ms": p95_ms,
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            "p95_target_passed": p95_ms <= p95_target_ms,
        },
        "invalid_requests": invalid_request_results,
        "conversation_state": conversation_state_result,
        "requests": request_results,
        "final_summary": {
            "functional_passed": functional_passed,
            "passed_requests": request_count - len(failed_requests),
            "failed_requests": len(failed_requests),
            "contract_failures": sum(not result["contract_passed"] for result in request_results),
            "scenario_failures": sum(not result["scenario_passed"] for result in request_results),
            "invalid_request_checks_passed": len(invalid_request_results) - len(invalid_failures),
            "invalid_request_checks_total": len(invalid_request_results),
            "conversation_state_passed": conversation_state_result["passed"],
            "performance_target_passed": p95_ms <= p95_target_ms,
            "note": "The performance target is informational; production capacity requires staging load testing with representative traffic."
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run concurrent FastAPI chatbot stability checks.")
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--p95-target-ms", type=float, default=5000.0)
    args = parser.parse_args()
    if args.requests < 1 or args.workers < 1:
        parser.error("--requests and --workers must be positive")

    result = run_stability_eval(args.requests, args.workers, args.p95_target_ms)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"api_stability_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = result["final_summary"]
    print(f"functional_passed: {summary['functional_passed']}")
    print(f"passed_requests: {summary['passed_requests']}/{args.requests}")
    print(f"contract_failures: {summary['contract_failures']}")
    print(f"conversation_state_passed: {summary['conversation_state_passed']}")
    print(f"p95_ms: {result['latency']['p95_ms']}")
    print(f"performance_target_passed: {summary['performance_target_passed']}")
    print(f"saved_result: {output_path}")
    return 0 if summary["functional_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
