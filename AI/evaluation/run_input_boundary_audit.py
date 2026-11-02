"""Fresh malformed-input checks. No external calls or persistent DB writes.

Accepted Unicode input or a clear 4xx rejection is valid for edge encodings;
500s and leaking internal tracebacks are never treated as valid rejection.
"""

import json
import os
from pathlib import Path
import sys


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.update(FOOD_CHATBOT_ENV="test", FOOD_CHATBOT_DB_PROVIDER="memory",
                      FOOD_CHATBOT_ENABLE_EXTERNAL_AI="false", OPENAI_API_KEY="",
                      FOOD_CHATBOT_ENABLE_CONVERSATIONS="false", FOOD_CHATBOT_API_TOKEN="",
                      FOOD_CHATBOT_ENABLE_RATE_LIMIT="false")
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.core.models import MAX_CHAT_MESSAGE_LENGTH
    cases = [
        ("null_message", {"message": None}, {422}),
        ("object_message", {"message": {"value": "안녕"}}, {422}),
        ("array_message", {"message": ["안녕"]}, {422}),
        ("boolean_message", {"message": True}, {422}),
        ("number_message", {"message": 123}, {422}),
        ("null_profile_item", {"message": "안녕", "profiles": [None]}, {422}),
        ("object_instead_of_profiles", {"message": "안녕", "profiles": {"nickname": "검사"}}, {422}),
        ("too_many_profiles", {"message": "안녕", "profiles": [{"profile_id": n + 100, "nickname": "검사"} for n in range(21)]}, {422}),
        ("overlong_unicode_message", {"message": "가" * (MAX_CHAT_MESSAGE_LENGTH + 1)}, {422}),
        ("overlong_profile_name", {"message": "안녕", "profiles": [{"nickname": "가" * 129}]}, {422}),
        ("null_byte", {"message": "무가당\x00표시가 뭐야?"}, {200, 400, 422}),
        ("surrogate_message", {"message": "\ud800"}, {200, 400, 422}),
        ("surrogate_nickname", {"message": "안녕", "profiles": [{"nickname": "\ud800"}]}, {200, 400, 422}),
        ("bidi_message", {"message": "\u202e무가당 표시가 뭐야?"}, {200, 400, 422}),
        ("nul_barcode", {"message": "이 제품 먹어도 돼?", "barcode": "880\x00000"}, {200, 400, 422}),
    ]
    encoded = [(name, json.dumps(body, ensure_ascii=True), codes) for name, body, codes in cases]
    encoded += [
        ("overflow_product_id", '{"message":"test","product_id":1e309}', {400, 422}),
        ("overflow_profile_id", '{"message":"test","profiles":[{"profile_id":1e309,"nickname":"test"}]}', {400, 422}),
        ("truncated_json", '{"message":', {400, 422}),
        ("wrong_top_level", '[]', {400, 422}),
    ]
    results = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for name, body, expected in encoded:
            response = client.post("/chat", content=body.encode("ascii"), headers={"Content-Type": "application/json"})
            try:
                parsed = response.json()
            except ValueError:
                parsed = {"non_json_body": response.text[:300]}
            leaked = any(term in response.text for term in ("Traceback (most recent call last)", "OPENAI_API_KEY=", "FOOD_CHATBOT_MYSQL_PASSWORD="))
            result = {"id": name, "http": response.status_code, "expected_http": sorted(expected),
                      "passed": response.status_code in expected and not leaked,
                      "internal_trace_leaked": leaked, "response": parsed}
            results.append(result)
            print(json.dumps({k: result[k] for k in ("id", "http", "passed")}), flush=True)
    return {"scope": "In-process HTTP, memory repositories, no external AI", "cases": results,
            "summary": {"total": len(results), "passed": sum(c["passed"] for c in results),
                        "failed": [c["id"] for c in results if not c["passed"]]}}


if __name__ == "__main__":
    report = run()
    target = Path(__file__).with_name("results") / "reaudit4_input_boundaries.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2))
    print(json.dumps(report["summary"]))
    raise SystemExit(0 if not report["summary"]["failed"] else 1)
