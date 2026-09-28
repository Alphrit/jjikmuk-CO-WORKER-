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
