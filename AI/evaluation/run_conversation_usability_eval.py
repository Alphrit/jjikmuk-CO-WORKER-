"""Independent conversation acceptance cases, including known usability gaps.

Uses synthetic catalog data and memory stores. No external calls or real DB
writes. A nonzero exit means an acceptance criterion failed, even with HTTP 200.
The checks measure explicit behavior, not subjective naturalness or clinical
accuracy. Inspect the saved transcripts for qualitative review.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time


AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))


def value_at(payload, path):
    for part in path.split("."):
        if not isinstance(payload, dict):
            return None
        payload = payload.get(part)
    return payload


def check_response(check, response, first):
    actual = value_at(response, check.get("field", "answer"))
    expected = check.get("value")
    kind = check["kind"]
    if kind == "eq":
        return actual == expected
    if kind == "not_eq":
        return actual != expected
    if kind == "contains_items":
        return isinstance(actual, list) and all(item in actual for item in expected)
    if kind == "one_of":
        return actual in expected
    if kind == "contains_all":
        return isinstance(actual, str) and all(term in actual for term in expected)
    if kind == "contains_any":
        return isinstance(actual, str) and any(term in actual for term in expected)
    if kind == "not_contains":
        return isinstance(actual, str) and all(term not in actual for term in expected)
    if kind == "max_chars":
        return isinstance(actual, str) and len(actual) <= expected
    if kind == "max_lines":
        return isinstance(actual, str) and len(actual.splitlines()) <= expected
    if kind == "length":
        return isinstance(actual, list) and len(actual) == expected
    if kind == "list_excludes_barcodes":
        return isinstance(actual, list) and all(item.get("barcode") not in expected for item in actual)
    if kind == "recipe_avoids":
        if not isinstance(actual, dict):
            return False
        ingredients = " ".join(item.get("name", "") for item in actual.get("ingredients", []))
        return bool(ingredients) and all(term not in ingredients for term in expected)
    if kind == "same_as_first":
        return actual is not None and actual == value_at(first, check["field"])
    if kind == "recipe_edit_respected_or_declined":
        recipe = response.get("recipe")
        if recipe is None:
            return any(term in response.get("answer", "") for term in ("지원하지", "새 레시피", "다시 요청"))
        ingredients = " ".join(item.get("name", "") for item in recipe.get("ingredients", []))
        previous_goals = set((first.get("recipe") or {}).get("requested_diets", []))
        return all(term not in ingredients for term in expected) and previous_goals.issubset(recipe.get("requested_diets", []))
    if kind == "candidate_selected":
        candidates = first.get("product_candidates", [])
        if not candidates or not isinstance(actual, dict):
            return False
        candidate = candidates[expected]
        for key in ("barcode", "product_id", "report_number"):
            if candidate.get(key) is not None:
                return actual.get(key) == candidate[key]
        return False
    raise ValueError(f"Unknown acceptance check: {kind}")


def run(cases_path):
    # Isolated process configuration; never load operational credentials here.
    os.environ.update(
        FOOD_CHATBOT_ENV="test", FOOD_CHATBOT_DB_PROVIDER="memory",
        FOOD_CHATBOT_ENABLE_EXTERNAL_AI="false", OPENAI_API_KEY="",
        FOOD_CHATBOT_ENABLE_CONVERSATIONS="false", FOOD_CHATBOT_API_TOKEN="",
        FOOD_CHATBOT_ENABLE_RATE_LIMIT="false",
    )
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.api.response_contract import PUBLIC_CHAT_FIELDS
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services.product import set_product_repository
    from evaluation.product_label_fixtures import (
        materialize_evaluation_repository_seed,
        synthetic_evaluation_label_source_registry,
    )

    dataset = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = []
    with synthetic_evaluation_label_source_registry(), TestClient(app) as client:
        set_product_repository(InMemoryProductRepository(seed_data=
            materialize_evaluation_repository_seed(dataset["products"])))
        for case in dataset["cases"]:
            state, history, turns = {}, [], []
            failures = []
            for index, turn in enumerate(case["turns"]):
                payload = {**turn, "conversation_state": state, "chat_history": list(history)}
                started = time.perf_counter()
                result = client.post("/chat", json=payload)
                response = result.json()
                turns.append({"message": turn["message"], "request": payload,
                              "http": result.status_code, "response": response,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000)})
                if result.status_code != 200:
                    failures.append(f"turn {index}: HTTP {result.status_code}")
                    break
                if set(response) - set(PUBLIC_CHAT_FIELDS):
                    failures.append(f"turn {index}: non-public response fields")
                if not response.get("answer", "").strip():
                    failures.append(f"turn {index}: empty answer")
                if response.get("external_ai_processing", {}).get("used"):
                    failures.append(f"turn {index}: unexpected external processing")
                for check in case.get("turn_checks", {}).get(str(index), []):
                    if not check_response(check, response, turns[0]["response"]):
                        failures.append(f"turn {index}: " + check["description"])
                state = response.get("conversation_state", {})
                history += [{"role": "user", "content": turn["message"]},
                            {"role": "assistant", "content": response["answer"]}]
            if len(turns) == len(case["turns"]) and turns[-1]["http"] == 200:
                for check in case["checks"]:
                    if not check_response(check, turns[-1]["response"], turns[0]["response"]):
                        failures.append(check["description"])
            cases.append({"id": case["id"], "category": case["category"],
                          "priority": case["priority"], "passed": not failures,
                          "failures": failures, "turns": turns})
    return {"scope": dataset["scope"], "cases": cases,
            "summary": {"total": len(cases), "passed": sum(c["passed"] for c in cases),
                        "failed": sum(not c["passed"] for c in cases),
                        "turns": sum(len(c["turns"]) for c in cases),
                        "http_200": sum(t["http"] == 200 for c in cases for t in c["turns"])}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).with_name("conversation_usability_cases.json"))
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("results") / "conversation_usability.json")
    args = parser.parse_args()
    result = run(args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    for case in result["cases"]:
        if not case["passed"]:
            print(case["id"] + ": " + "; ".join(case["failures"]))
    print("saved_result: " + str(args.output))
    return 0 if not result["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
