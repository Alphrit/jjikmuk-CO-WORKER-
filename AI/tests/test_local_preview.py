"""Developer UI and real pipeline behavior with transport mocked, never paid calls."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app import local_preview as preview
from app.api.main import app as production_app
from app.infrastructure import llm, rag
from app.infrastructure import external_ai_consent as consent
from app.infrastructure.llm_trace import capture_llm_calls, llm_purpose
from app.services.product import get_product_repository
from test_released_knowledge import packaged_sources


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    monkeypatch.setattr(llm, "_override_client", None)
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-local-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm, "_post_json", lambda *_args: pytest.fail("Unexpected transport"))
    monkeypatch.setattr(rag, "get_rag_status", lambda **_kwargs: {
        "index_exists": True, "metadata_complete": True, "index_stale": True,
        "retriever_ready": False, "document_count": 8,
    })


def browser(*, external=False):
    return TestClient(preview.create_preview_app(allow_external=external),
                      base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))


def authorize(client):
    result = client.get("/")
    assert result.status_code == 200
    token = re.search(r"const token = '([^']+)'", result.text).group(1)
    client.headers.update({"x-local-preview-token": token, "origin": "http://127.0.0.1:8765"})


def ask(client, message="레시피 추천해줘", *, profile="none", external=False):
    result = client.post("/ask", json={"message": message, "profile": profile, "allow_external": external})
    assert result.status_code == 200, result.text
    return result.json()


def test_default_is_offline_no_db_or_production_startup(monkeypatch):
    from app.api import main
    original_repo = get_product_repository()
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setenv("FOOD_CHATBOT_DB_PROVIDER", "mysql")
    monkeypatch.setattr(main, "configure_repositories", lambda: pytest.fail("Production startup"))
    monkeypatch.setattr(main, "_log_pipeline_response", lambda *_a, **_k: pytest.fail("Audit write"))
    with browser() as client:
        authorize(client)
        result = ask(client)
        assert result["response"]["recipe"]
        assert result["diagnostics"]["calls"] == []
        assert not result["diagnostics"]["llm_output_adopted"]
        assert not client.get("/status").json()["external_mode"]
        assert get_product_repository() is not original_repo
        assert get_product_repository().get_by_barcode("8801043035989") is None
    assert get_product_repository() is original_repo
    assert llm.external_ai_processing_enabled()  # process settings restored


@pytest.mark.parametrize("path", ["/status", "/ask", "/reset"])
def test_capability_required(path):
    with browser() as client:
        method = client.get if path == "/status" else client.post
        assert method(path).status_code == 403


@pytest.mark.parametrize("headers", [
    {"host": "evil.example:8765"}, {"host": "127.0.0.1:8000"},
    {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
])
def test_cross_origin_and_dns_rebinding_rejected(headers):
    with browser() as client:
        authorize(client)
        assert client.get("/status", headers=headers).status_code == 403
        assert client.get("/", headers=headers).status_code == 403


def test_non_loopback_peer_rejected():
    with TestClient(preview.create_preview_app(), base_url="http://127.0.0.1:8765",
                    client=("192.168.1.3", 4567)) as client:
        assert client.get("/").status_code == 403


def test_no_debug_routes_added_to_production_and_page_has_safe_policy():
    assert not {"/ask", "/reset", "/status"}.intersection({r.path for r in production_app.routes})
    with browser() as client:
        page = client.get("/")
        assert page.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "innerHTML" not in page.text and "localStorage." not in page.text
        assert "dummy-local-test-key" not in page.text
        authorize(client)
        assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize("payload", [
    {"message": " "}, {"message": "a" * 4001}, {"message": "안녕", "profile": "real-user"},
    {"message": "안녕", "allow_external": "true"},
    {"message": "안녕", "conversation_state": {"user_id": 1}},
])
def test_bounded_fixed_inputs(payload):
    with browser() as client:
        authorize(client)
        assert client.post("/ask", json=payload).status_code == 422


def test_oversize_and_invalid_json():
    with browser() as client:
        authorize(client)
        assert client.post("/ask", content=b"a" * 24001).status_code == 413
        assert client.post("/ask", content=b"not-json").status_code == 422


def test_external_checkbox_does_not_enable_disabled_operator():
    with browser() as client:
        authorize(client)
        assert client.post("/ask", json={"message": "레시피", "allow_external": True}).status_code == 409


def test_external_mode_still_requires_each_turn_consent(monkeypatch):
    with browser(external=True) as client:
        authorize(client)
        result = ask(client)
        assert result["diagnostics"]["calls"] == []
        assert result["response"]["external_ai_processing"]["status"] == "consent_denied"


def test_attempt_success_and_adoption_are_separate_and_no_raw_draft_leaks(monkeypatch):
    calls = []
    def invalid_draft(url, payload, headers):
        calls.append(payload)
        assert payload["store"] is False
        assert consent.external_ai_request_allowed()
        return {"output_text": "unsafe private model draft"}
    monkeypatch.setattr(llm, "_post_json", invalid_draft)
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, external=True)
        d = result["diagnostics"]
        assert len(calls) == 1
        assert d["calls"][0]["purpose"] == "recipe_draft"
        assert d["calls"][0]["provider"] == "openai"
        assert d["calls"][0]["text_returned"]
        assert not d["llm_output_adopted"]
        assert d["fallback_reason"] == "recipe_llm_invalid_draft"
        assert "unsafe private model draft" not in json.dumps(result)
        assert "dummy-local-test-key" not in json.dumps(result)
        assert result["response"]["external_ai_processing"]["services"] == ["llm"]
        assert "fallback_reason" not in result["response"]
        after = ask(client, "다른 레시피 추천해줘")
        assert after["diagnostics"]["calls"] == [] and len(calls) == 1


def test_valid_recipe_draft_reports_hybrid_adoption(monkeypatch):
    draft = {"product_name": "기본 식재료", "title": "오이 양배추 볼", "servings": "1인분",
             "ingredients": [{"name": "오이", "amount": "1/3개"}, {"name": "양배추", "amount": "한 줌"}],
             "steps": ["오이와 양배추를 썰어요.", "오이와 양배추를 섞어요."], "substitutions": []}
    monkeypatch.setattr(llm, "_post_json", lambda *_a: {"output_text": json.dumps(draft, ensure_ascii=False)})
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, external=True)
        assert result["diagnostics"]["llm_output_adopted"], result["diagnostics"]
        assert result["response"]["answer_source"] == "hybrid"
        assert result["response"]["recipe"]["title"] == draft["title"]


def test_transport_returns_no_text_is_not_success(monkeypatch):
    monkeypatch.setattr(llm, "_post_json", lambda *_a: None)
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, external=True)
        assert result["diagnostics"]["calls"][0]["text_returned"] is False
        assert not result["diagnostics"]["llm_output_adopted"]


def test_official_retrieval_and_general_llm_final_answer(packaged_sources, monkeypatch):
    answer = "식품을 구입하기 전에는 자신에게 알레르기를 일으키는 식품이 포함되어 있는지 확인하세요."
    documents = [d for d in rag._load_documents() if d.metadata["source"] == "allergy-purchase-check.source.json"]
    class Store:
        def similarity_search(self, _query, k):
            return documents[:k]
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    monkeypatch.setattr(rag, "build_vectorstore", lambda: Store())
    monkeypatch.setattr(llm, "_post_json", lambda *_a: {"output_text": answer})
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, "식품 알레르기 안내", external=True)
        assert result["response"]["answer"] == answer
        assert result["response"]["answer_source"] == "rag"
        assert result["diagnostics"]["llm_output_adopted"]
        assert result["diagnostics"]["calls"][0]["purpose"] == "general_answer"
        assert "kca.go.kr" in result["response"]["sources"][0]
        assert set(result["diagnostics"]["external_services_attempted"]) == {"rag", "llm"}


def test_missing_index_general_answer_is_rule_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROMA_DB_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(rag, "build_vectorstore", lambda: pytest.fail("Implicit index build"))
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, "식품 알레르기 안내", external=True)
        assert result["diagnostics"]["calls"] == []
        assert not result["diagnostics"]["llm_output_adopted"]
        assert result["diagnostics"]["fallback_reason"] == "rag_context_missing_for_general_llm"


def test_intent_only_call_does_not_count_as_final_adoption(monkeypatch):
    from app.core.models import ChatResponse
    from app.intent.llm import llm_fallback_classify_intent
    def pipeline(req, **_kwargs):
        assert llm_fallback_classify_intent(req.message, None, None, []) == "unknown"
        return ChatResponse(intent="unknown", answer="규칙 안내", risk_level="unknown"), None, {}
    monkeypatch.setattr(preview, "_run_chat_pipeline", pipeline)
    monkeypatch.setattr(llm, "_post_json", lambda *_a: {"output_text": '{"intent":"unknown"}'})
    with browser(external=True) as client:
        authorize(client)
        result = ask(client, "애매한 테스트 질문", external=True)
        assert result["diagnostics"]["calls"][0]["purpose"] == "intent"
        assert not result["diagnostics"]["llm_output_adopted"]
        assert "의도 분류" in result["diagnostics"]["explanation"]


def test_provider_configuration_and_consent_failure_fail_closed(monkeypatch):
    with browser(external=True) as client:
        authorize(client)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        assert client.post("/ask", json={"message": "레시피", "allow_external": True}).status_code == 409
        monkeypatch.setenv("OPENAI_API_KEY", "dummy-local-test-key")
        monkeypatch.setattr(preview, "_request_consent", lambda *_a: consent.ExternalAIConsentDecision())
        assert client.post("/ask", json={"message": "레시피", "allow_external": True}).status_code == 409


def test_busy_requests_do_not_duplicate_generation_or_reset_state(monkeypatch):
    started, release = Event(), Event()
    original = preview._run_chat_pipeline
    def slow_pipeline(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(preview, "_run_chat_pipeline", slow_pipeline)
    with browser() as client, ThreadPoolExecutor(max_workers=1) as pool:
        authorize(client)
        future = pool.submit(ask, client)
        try:
            assert started.wait(5)
            assert client.post("/ask", json={"message": "레시피"}).status_code == 409
            assert client.post("/reset").status_code == 409
        finally:
            release.set()
        assert future.result()["diagnostics"]["turn"] == 1


def test_followups_reset_and_profile_change():
    with browser() as client:
        authorize(client)
        first = ask(client, "저염 레시피 추천해줘")
        follow = ask(client, "다른 레시피 추천해줘")
        assert first["response"]["recipe"]["title"] != follow["response"]["recipe"]["title"]
        assert "저염식" in follow["response"]["recipe"]["requested_diets"]
        changed = ask(client, profile="milk")
        assert changed["diagnostics"]["turn"] == 1
        assert "저염식" not in changed["response"]["recipe"]["requested_diets"]
        assert client.post("/reset").status_code == 200
        cleared = ask(client)
        assert cleared["diagnostics"]["turn"] == 1


def test_twenty_turn_limit_and_reset(monkeypatch):
    monkeypatch.setattr(preview, "MAX_TURNS", 2)
    with browser() as client:
        authorize(client)
        ask(client)
        ask(client, "다른 레시피 추천해줘")
        assert client.post("/ask", json={"message": "다른 레시피"}).status_code == 409
        client.post("/reset")
        assert ask(client)["diagnostics"]["turn"] == 1


def test_fictional_product_does_not_claim_real_llm_polishing():
    with browser() as client:
        authorize(client)
        result = ask(client, "가상 새우과자 먹어도 돼?", profile="family")
        assert not result["diagnostics"]["llm_output_adopted"]
        assert result["response"]["allergy_status"] == "conflict"
        assert result["response"]["overall_decision"] == "avoid"


def test_trace_is_nested_and_thread_local(monkeypatch):
    monkeypatch.setattr(llm, "_override_client", lambda _prompt: "hello")
    def run(purpose):
        with capture_llm_calls() as events, llm_purpose(purpose):
            llm.generate_text("private prompt")
            with capture_llm_calls() as nested:
                llm.generate_text("inner private prompt")
            assert len(nested) == 1
        return events
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(run, ["intent", "general_answer"]))
    assert len(one) == len(two) == 1
    assert one[0]["purpose"] == "intent" and two[0]["purpose"] == "general_answer"
    assert one[0]["provider"] == "test"
    assert "private prompt" not in json.dumps([one, two])


@pytest.mark.parametrize("index", ["missing", "stale"])
def test_user_search_never_builds_index(monkeypatch, tmp_path, index):
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("CHROMA_DB_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "true")
    monkeypatch.setattr(rag, "_inspect_index", lambda _path: {
        "index_exists": index == "stale", "metadata_complete": index == "stale",
        "index_stale": index == "stale", "current": False, "input_error": None,
    })
    monkeypatch.setattr(rag, "build_vectorstore", lambda: pytest.fail("User query must not build an index"))
    with consent.external_ai_request_context(consent.ExternalAIConsentDecision(verified=True, consent_granted=True)):
        assert rag.get_rag_context_entries("private question") == []
        assert consent.current_external_ai_processing_summary()["services"] == []
