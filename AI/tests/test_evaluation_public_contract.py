"""Run standalone evaluator checks in isolation from suite-wide app state."""

import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent


AI_DIR = Path(__file__).resolve().parents[1]


def _run_isolated(code):
    environment = {
        **os.environ,
        "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": "false",
        "OPENAI_API_KEY": "",
        "FOOD_CHATBOT_DB_PROVIDER": "memory",
        "FOOD_CHATBOT_ENV": "test",
        "FOOD_CHATBOT_ENABLE_CONVERSATIONS": "false",
    }
    completed = subprocess.run(
        [sys.executable, "-c", dedent(code)], cwd=AI_DIR, env=environment,
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_stability_contract_accepts_public_fields_and_rejects_internal_fields():
    _run_isolated("""
        from app.api.response_contract import PUBLIC_CHAT_FIELDS, PublicChatResponse
        from app.core.models import ChatResponse
        from evaluation.run_api_stability_eval import REQUIRED_RESPONSE_FIELDS, _contract_failures

        response = ChatResponse(intent="unknown", answer="제품명을 알려주세요.", risk_level="unknown")
        payload = PublicChatResponse.model_validate(response).model_dump(mode="json")
        assert REQUIRED_RESPONSE_FIELDS <= set(PUBLIC_CHAT_FIELDS)
        assert "pipeline_stage" not in REQUIRED_RESPONSE_FIELDS
        assert _contract_failures(200, payload) == []
        private_fields = set(ChatResponse.model_fields) - set(PUBLIC_CHAT_FIELDS)
        assert "pipeline_stage" in private_fields
        for name in private_fields | {"future_internal_diagnostic"}:
            failures = _contract_failures(200, {**payload, name: None})
            assert any("non-public response fields" in failure and name in failure for failure in failures), name
        missing_answer = {key: value for key, value in payload.items() if key != "answer"}
        assert _contract_failures(200, missing_answer)
        assert _contract_failures(500, payload)
    """)


def test_stability_smoke_uses_current_http_contract_without_live_services():
    _run_isolated("""
        from evaluation.run_api_stability_eval import run_stability_eval
        result = run_stability_eval(request_count=4, workers=2, p95_target_ms=60000)
        summary = result["final_summary"]
        assert summary["functional_passed"], result
        assert summary["passed_requests"] == 4
        assert summary["contract_failures"] == 0
        assert summary["scenario_failures"] == 0
        assert summary["invalid_request_checks_passed"] == summary["invalid_request_checks_total"]
        assert summary["conversation_state_passed"]
    """)


def test_stability_does_not_report_success_when_only_trust_evaluation_fails():
    _run_isolated("""
        from unittest.mock import patch
        from evaluation import run_api_stability_eval as stability

        original = stability.evaluate_case
        def reject_trust(*args, **kwargs):
            result = original(*args, **kwargs)
            result.update(passed=False, trust_passed=False, failures=[], trust_findings=[{
                "type": "unsupported_safety_claim", "severity": "high",
                "message": "injected unsupported safety claim",
            }])
            return result

        with patch.object(stability, "evaluate_case", side_effect=reject_trust):
            result = stability.run_stability_eval(request_count=4, workers=2, p95_target_ms=60000)
        summary = result["final_summary"]
        assert not summary["functional_passed"], result
        assert summary["passed_requests"] == 0
        assert summary["failed_requests"] == summary["scenario_failures"] == 4
        assert all(request["failures"] for request in result["requests"])
    """)


def test_conversation_evaluator_catches_intermediate_failure_and_keeps_request_snapshots():
    _run_isolated("""
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from evaluation.run_conversation_usability_eval import run

        base = {
            "category": "evaluator_control", "priority": "test",
            "turns": [{"message": "안녕"}, {"message": "도움말"}],
            "checks": [{"kind": "max_chars", "value": 10000,
                        "description": "final response is present"}],
        }
        dataset = {"scope": "isolated evaluator regression", "products": {}, "cases": [
            {**base, "id": "control"},
            {**base, "id": "intermediate_failure", "turn_checks": {"0": [
                {"kind": "eq", "field": "intent", "value": "impossible_intent",
                 "description": "injected intermediate failure"},
            ]}},
        ]}
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "cases.json"
            path.write_text(json.dumps(dataset))
            result = run(path)
        assert result["summary"] == {"total": 2, "passed": 1, "failed": 1,
                                      "turns": 4, "http_200": 4}
        control, failed = result["cases"]
        assert control["passed"]
        assert failed["failures"] == ["turn 0: injected intermediate failure"]
        assert failed["turns"][0]["request"]["chat_history"] == []
        assert failed["turns"][1]["request"]["chat_history"] == [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": failed["turns"][0]["response"]["answer"]},
        ]
    """)


def test_mysql_conversation_checks_use_queried_values_and_require_an_actual_assessment():
    from evaluation.run_mysql_readonly_audit import extended_conversation_checks

    cases = {
        "holdout_real_first": {"current_product": {"nutrition": {"sodium": 125, "cholesterol": 0}}},
        "holdout_real_all_off_facts": {"answer": "나트륨 125mg, 콜레스테롤 0mg"},
        "holdout_real_polite_off_subject": {"answer": "엄마를 ON으로 선택해 주세요."},
        "holdout_real_renamed_father": {"current_product": {"barcode": "test"}, "intent": "can_i_eat"},
        "holdout_real_after_polite_reset": {"current_product": None},
        "holdout_real_drinking_emergency": {"intent": "emergency_safety"},
        "holdout_real_negated_emergency": {"intent": "unknown"},
    }
    assert all(extended_conversation_checks(cases).values())
    cases["holdout_real_all_off_facts"]["answer"] = "나트륨 125mg"
    cases["holdout_real_renamed_father"]["intent"] = "unknown"
    failed = extended_conversation_checks(cases)
    assert not failed["all_off_still_returns_mass_facts"]
    assert not failed["renamed_active_profile_evaluates_current_product"]
    cases["holdout_real_first"]["current_product"]["nutrition"]["cholesterol"] = 1.5
    cases["holdout_real_all_off_facts"]["answer"] = "나트륨 125mg, 콜레스테롤 0mg"
    assert not extended_conversation_checks(cases)["all_off_still_returns_mass_facts"]
    cases["holdout_real_all_off_facts"]["answer"] = "나트륨 125mg, 콜레스테롤 1.5mg"
    assert extended_conversation_checks(cases)["all_off_still_returns_mass_facts"]
