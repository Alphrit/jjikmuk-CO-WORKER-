"""Opt-in real MySQL reads with fresh conversation transitions.

No schema/data changes: product reads use MySQL; audit writes use memory.
Search observations do not claim relevant results merely because SQL succeeds.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--transitions", action="store_true", help="Add independent wording and state-transition probes")
    parser.add_argument("--barcode", default="8801121763964")
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/mysql_exploratory.json"))
    args = parser.parse_args()
    if not args.read_only:
        parser.error("--read-only is required")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    os.environ["FOOD_CHATBOT_ENABLE_EXTERNAL_AI"] = "false"
    from app.api.main import chat
    from app.core.models import ChatRequest
    from app.infrastructure.repository import MySQLProductRepository, InMemoryActivityRepository, dispose_mysql_connection_pools
    from app.services.product import set_product_repository
    from app.services.activity import set_activity_repository

    repository = MySQLProductRepository(
        host=os.getenv("FOOD_CHATBOT_MYSQL_HOST", "localhost"),
        port=int(os.getenv("FOOD_CHATBOT_MYSQL_PORT", "3306")),
        database=os.getenv("FOOD_CHATBOT_MYSQL_DATABASE", "jjikmuk"),
        user=os.getenv("FOOD_CHATBOT_MYSQL_USER", "root"),
        password=os.getenv("FOOD_CHATBOT_MYSQL_PASSWORD", ""),
        table_name=os.getenv("FOOD_CHATBOT_PRODUCT_TABLE", "products"),
    )
    set_product_repository(repository)
    set_activity_repository(InMemoryActivityRepository())
    report = {"scope": "Real MySQL product reads; in-memory audit writes; no external AI or real conversation writes",
              "search_observations": [], "cases": []}
    mom = {"profile_id": 841, "nickname": "어머니", "allergies": ["우유"], "is_active": True}
    dad = {"profile_id": 842, "nickname": "아빠", "allergies": ["땅콩"], "is_active": True}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def request(name, message, predicate, state=None, profiles=None, **extra):
        payload = {"message": message, "profiles": [mom] if profiles is None else profiles,
                   "conversation_state": state or {}, **extra}
        started = time.perf_counter()
        response = {}
        try:
            response = chat(ChatRequest.model_validate(payload)).model_dump(mode="json")
            row = {"id": name, "passed": bool(predicate(response)), "request": payload, "response": response}
        except Exception as exc:
            row = {"id": name, "passed": False, "error_type": type(exc).__name__}
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        report["cases"].append(row)
        print(json.dumps({k: row[k] for k in ("id", "passed", "elapsed_ms")}), flush=True)
        save()
        return response

    try:
        actual = repository.get_by_barcode(args.barcode)
        if actual is None:
            raise RuntimeError("reference_product_missing")
        report["reference_product"] = actual.model_dump(mode="json")
        for query in ("매일우유 200ml", "서울 우유", "서울우유 흰우유", "삼다수 0.5L",
                      "오레오 오즈 250g", "아몬드브리즈 언스위트", "포카칩 오리지널 137g",
                      "코카콜라 제로 355ml", "새우 깡", "햇반 210g", "매일 우유", "없는제품검사ZX843"):
            started = time.perf_counter()
            products = repository.search_candidates(query)
            report["search_observations"].append({"query": query,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "products": [{"name": p.product_name, "barcode": p.barcode, "score": p.match_confidence} for p in products]})
            save()
        initial = request("real_initial", "이 제품 먹어도 돼?", lambda d: (d.get("current_product") or {}).get("barcode") == args.barcode, barcode=args.barcode)
        state = initial.get("conversation_state", {})
        expected_sodium, expected_cholesterol = actual.nutrition.get("sodium"), actual.nutrition.get("cholesterol")
        request("real_compound_polite", "나트륨, 콜레스테롤 수치 둘 다 부탁해", lambda d:
                expected_sodium is not None and expected_cholesterol is not None and all(t in d.get("answer", "") for t in (
                    "나트륨", "콜레스테롤", f"{expected_sodium:g}mg", f"{expected_cholesterol:g}mg")), state, [])
        request("real_off_honorific", "어머니한테 줄 수 있어?", lambda d: d.get("overall_decision") == "check_required"
                and any(t in d.get("answer", "") for t in ("ON", "선택")), state, [{**mom, "is_active": False}, dad])
        request("real_on_condition_request", "이번에는 아빠 조건으로 부탁해", lambda d: d.get("intent") == "can_i_eat"
                and (d.get("current_product") or {}).get("barcode") == args.barcode, state, [{**mom, "is_active": False}, dad])
        unknown = request("real_unknown_new_name", "등록안된새상품ZZZ 먹어도 돼?", lambda d: d.get("current_product") is None, state)
        request("real_unknown_name_followup", "그 상품 나트륨은?", lambda d: d.get("current_product") is None,
                unknown.get("conversation_state", {}))
        invalid = request("real_unknown_barcode", "새 제품 먹어도 돼?", lambda d: d.get("current_product") is None,
                          state, barcode="9999999999999")
        request("real_unknown_barcode_followup", "그 상품 나트륨은?", lambda d: d.get("current_product") is None,
                invalid.get("conversation_state", {}))
        reset = request("real_reset_new_phrase", "대화 초기화 부탁해", lambda d: d.get("current_product") is None,
                        state)
        request("real_reset_new_phrase_followup", "그 상품 콜레스테롤은?", lambda d: d.get("current_product") is None,
                reset.get("conversation_state", {}))
        request("real_reset_control", "대화 초기화해줘", lambda d: d.get("current_product") is None, state)
        request("real_fresh_current_symptom", "우유를 먹었는데 목이 부었습니다", lambda d: d.get("intent") == "emergency_safety", state)
        request("real_fresh_negative_symptom", "우유 먹고 숨이 안 쉬어진 적은 없어요", lambda d: d.get("intent") != "emergency_safety", state)
        if args.transitions:
            for i, wording in enumerate(("미등록음료QQQ의 나트륨 알려줘", "새 제품인 미등록음료QQQ는 어때?")):
                fresh = request(f"transition_unknown_{i}", wording, lambda d: d.get("current_product") is None, state)
                request(f"transition_unknown_followup_{i}", "그 새 음료는 먹어도 돼?", lambda d: d.get("current_product") is None,
                        fresh.get("conversation_state", {}))
            reset = request("transition_reset_content", "대화 내용을 초기화해 줄래?", lambda d: d.get("current_product") is None, state)
            request("transition_reset_content_followup", "아까 그 제품의 나트륨은?", lambda d: d.get("current_product") is None,
                    reset.get("conversation_state", {}))
            request("transition_symptom_current", "새우를 먹었는데 지금 숨이 찬 상태예요", lambda d: d.get("intent") == "emergency_safety", state, [])
            request("transition_symptom_hypothetical", "새우 먹고 숨이 차면 무조건 알레르기야?", lambda d: d.get("intent") != "emergency_safety", state, [])
            request("transition_profile_changed", "이 제품은 아빠만 먹어도 괜찮아?", lambda d:
                    d.get("active_profile_names") == ["아빠"] and (d.get("current_product") or {}).get("barcode") == args.barcode,
                    state, [{**mom, "is_active": False}, dad])
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        dispose_mysql_connection_pools()
        report["summary"] = {"searches": len(report["search_observations"]), "conversation_checks": len(report["cases"]),
                             "passed": sum(c["passed"] for c in report["cases"]),
                             "failed": [c["id"] for c in report["cases"] if not c["passed"]]}
        save()
        print(json.dumps(report["summary"]), flush=True)
    return int(bool(report.get("error_type") or report["summary"]["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
