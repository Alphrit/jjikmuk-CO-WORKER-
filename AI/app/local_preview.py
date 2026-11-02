"""Single-user loopback preview; intentionally separate from the production API.

Run in its own process: python -m app.local_preview [--allow-external].
No production lifespan, database, persistent chat, automatic index build or API
debug route is enabled by this module. Browser data never supplies profiles or
conversation state. The local adapter signs consent for its own fixed test user.
"""

from __future__ import annotations

import argparse
import hmac
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.main import _run_chat_pipeline
from app.api.response_contract import PublicChatResponse
from app.core.models import ChatHistoryItem, ChatRequest, ExternalAIProcessingSummary, Profile
from app.infrastructure import external_ai_consent as consent
from app.infrastructure import llm, rag
from app.infrastructure.llm_trace import capture_llm_calls
from app.infrastructure.repository import InMemoryProductRepository
from app.services.product import get_product_repository, set_product_repository


PROFILE_LABELS = {
    "none": "가상 사용자 · 등록 알레르기 없음",
    "shrimp": "가상 사용자 · 새우 알레르기",
    "milk": "가상 사용자 · 우유 알레르기",
    "family": "가상 가족 · 나(새우), 아이(우유) ON",
}
MAX_TURNS = 20
MAX_BODY_BYTES = 24_000


def _profiles(preset: str) -> list[Profile]:
    if preset == "family":
        return [Profile(profile_id=1, profile_name="나", allergies=["새우"]),
                Profile(profile_id=2, profile_name="아이", allergies=["우유"], target_type="child")]
    return [Profile(profile_id=1, profile_name="나", allergies={
        "none": [], "shrimp": ["새우"], "milk": ["우유"],
    }[preset])]


def _demo_repository():
    # Do not use the repository's default catalog: it contains real brand names
    # with sample facts. Nothing here is an actual product label or safety proof.
    return InMemoryProductRepository(seed_data={
        "LOCAL-SHRIMP": {"식품명": "가상 새우과자", "바코드": "LOCAL-SHRIMP",
                         "RAWMTRL_NM": "밀가루, 새우, 소금", "allergy": "밀, 새우",
                         "PRDLST_DCNM": "과자"},
        "LOCAL-MILK": {"식품명": "가상 우유", "바코드": "LOCAL-MILK",
                       "RAWMTRL_NM": "원유", "allergy": "우유", "PRDLST_DCNM": "우유"},
    })


@contextmanager
def _local_runtime(allow_external: bool):
    """Process-local settings only. Never write .env or initialize a DB."""
    settings = {
        "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": str(allow_external).lower(),
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET": secrets.token_hex(32),
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_MAX_AGE_SECONDS": "60",
        "REQUIRE_RAG_CONTEXT_FOR_LLM": "true",
        "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
        "ANONYMIZED_TELEMETRY": "false",
    }
    previous = {key: os.environ.get(key) for key in settings}
    repository = get_product_repository()
    os.environ.update(settings)
    set_product_repository(_demo_repository())
    try:
        yield
    finally:
        set_product_repository(repository)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    profile: Literal["none", "shrimp", "milk", "family"] = "none"
    allow_external: StrictBool = False


def _request_consent(req: ChatRequest, granted: bool, enabled: bool):
    # Sign the effective chat input including the bounded server-side history,
    # not just the short browser message. Secret is never returned to the UI.
    body = req.model_dump_json().encode("utf-8")
    timestamp, nonce = int(time.time()), secrets.token_urlsafe(24)
    value = "granted" if granted else "denied"
    signature = consent.sign_external_ai_consent(
        secret=os.environ[consent.CONSENT_SECRET_ENV], timestamp=timestamp,
        method="POST", path="/local-chat", user_id="local-fictional-user",
        consent=value, nonce=nonce, body=body,
    )
    return consent.verify_external_ai_consent(
        headers={consent.CONSENT_HEADER: value, consent.TIMESTAMP_HEADER: str(timestamp),
                 consent.NONCE_HEADER: nonce, consent.SIGNATURE_HEADER: signature,
                 consent.USER_ID_HEADER: "local-fictional-user"},
        method="POST", path="/local-chat", body=body, operator_enabled=enabled,
    )


def _readiness(allow_external: bool):
    status = rag.get_rag_status(verify_retriever=False)  # no embeddings or search
    return {
        "external_mode": allow_external,
        "api_key_present": bool((os.getenv("OPENAI_API_KEY") or "").strip()),
        "model": llm.get_llm_model(),
        "llm_config_ready": llm.is_llm_provider_available(),
        "rag_index_current": bool(status["index_exists"] and status["metadata_complete"]
                                  and not status["index_stale"]),
        "rag_config_ready": bool(status["retriever_ready"]),
        "document_count": status["document_count"],
        "profiles": PROFILE_LABELS,
        "max_turns": MAX_TURNS,
    }


def _explanation(response, events, usage):
    if response.fallback_reason == "rag_generated_answer_rejected":
        return "생성 답변의 근거 검증을 통과하지 못해 검색된 검토 완료 본문을 출처와 함께 표시했습니다. 생성 문장은 채택하지 않았습니다."
    if response.fallback_reason == "rag_reviewed_excerpt_answer":
        return "질문과 관련된 검토 완료 자료의 본문을 출처와 함께 표시했습니다. 답변 문장을 새로 생성하지 않았습니다."
    if response.fallback_reason == "rag_question_outside_evidence_scope":
        return "검색된 자료가 질문에 필요한 설명 범위를 충족하지 않아 규칙 답변을 표시합니다."
    if response.answer_source in {"llm", "rag", "hybrid"}:
        return "LLM 생성 결과가 검증을 통과해 최종 응답에 포함됐습니다. 안전성 보증을 뜻하지는 않습니다."
    reason = response.fallback_reason or ""
    if reason == "rag_context_missing_for_general_llm":
        return "사용 가능한 공식 검색 근거가 없어 일반 설명 생성을 보류하고 규칙 답변을 표시합니다."
    if reason == "product_rule_template_preserved":
        return "현재 제품 판정은 규칙 답변을 유지합니다. 제품 설명의 LLM 다듬기는 아직 연결하지 않았습니다."
    if reason.startswith("recipe_llm_"):
        return "LLM 레시피가 형식·재료·분량·조리 등의 검사에서 제외돼 규칙 레시피를 표시합니다."
    if events and all(event["purpose"] == "intent" for event in events):
        return "LLM은 의도 분류에만 사용했고 최종 답변은 규칙으로 구성했습니다."
    if not usage["allowed_for_request"]:
        return "외부 모드가 꺼져 있거나 이번 질문에 동의하지 않아 규칙 답변을 표시합니다."
    if events and not any(event["text_returned"] for event in events):
        return "LLM 호출을 시도했지만 사용 가능한 텍스트를 받지 못해 규칙 답변을 표시합니다."
    if events:
        return "LLM 결과를 최종 채택하지 않았습니다. 아래 사유 코드와 검증 결과를 확인해 주세요."
    return "이 요청은 규칙 처리 대상이거나 생성 조건이 충족되지 않아 LLM 답변을 사용하지 않았습니다."


def create_preview_app(*, port: int = 8765, allow_external: bool = False) -> FastAPI:
    if not 1024 <= port <= 65535:
        raise ValueError("Local preview port must be between 1024 and 65535")
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{port}"
    lock = threading.Lock()
    session = {"profile": None, "state": {}, "history": [], "turns": 0}

    @asynccontextmanager
    async def lifespan(_app):
        with _local_runtime(allow_external):
            yield
        session.update(profile=None, state={}, history=[], turns=0)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        peer = request.client.host if request.client else ""
        request_origin = request.headers.get("origin")
        authorized = (
            host == f"127.0.0.1:{port}" and peer in {"127.0.0.1", "::1"}
            and request_origin in {None, origin}
            and request.headers.get("sec-fetch-site") not in {"cross-site", "same-site"}
        )
        if request.url.path != "/" or request.method != "GET":
            authorized = authorized and hmac.compare_digest(
                request.headers.get("x-local-preview-token", "").encode(), token.encode(),
            )
        if not authorized:
            response = JSONResponse({"detail": "로컬 확인 화면에서만 접근할 수 있습니다."}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; connect-src 'self'; "
                f"script-src 'nonce-{token}'; style-src 'nonce-{token}'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            ),
        })
        return response

    @app.get("/", response_class=HTMLResponse)
    def page():
        return Path(__file__).with_name("local_preview.html").read_text(encoding="utf-8").replace(
            "__PREVIEW_TOKEN__", token,
        )

    @app.get("/status")
    def status():
        return _readiness(allow_external)

    def clear_session():
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "답변 처리 중입니다. 완료 후 초기화해 주세요.")
        try:
            session.update(profile=None, state={}, history=[], turns=0)
            return {"cleared": True}
        finally:
            lock.release()

    @app.post("/reset")
    def reset():
        return clear_session()

    def process(payload: PreviewInput):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "이미 질문을 처리하고 있습니다.")
        try:
            if payload.allow_external and not allow_external:
                raise HTTPException(409, "외부 모드가 꺼져 있습니다. --allow-external로 별도 실행해 주세요.")
            if payload.allow_external and not llm.is_llm_provider_available():
                raise HTTPException(409, "API 키·제공자·접속 주소 설정을 확인해 주세요.")
            profile_changed = session["profile"] != payload.profile
            if not profile_changed and session["turns"] >= MAX_TURNS:
                raise HTTPException(409, "이 대화는 20회에 도달했습니다. 새 대화를 시작해 주세요.")
            req = ChatRequest(
                profiles=_profiles(payload.profile), message=payload.message,
                conversation_state={} if profile_changed else session["state"],
                chat_history=[] if profile_changed else session["history"],
            )
            decision = _request_consent(req, payload.allow_external, allow_external)
            if payload.allow_external and not decision.allowed:
                raise HTTPException(409, "로컬 동의 검증에 실패했습니다. 다시 실행해 주세요.")
            started = time.perf_counter()
            with consent.external_ai_request_context(decision), capture_llm_calls() as events:
                response, _, _ = _run_chat_pipeline(req, log_activity=False)
                usage = consent.current_external_ai_processing_summary()
            response = response.model_copy(update={
                "external_ai_processing": ExternalAIProcessingSummary.model_validate(usage),
            })
            public = PublicChatResponse.model_validate(response, from_attributes=True).model_dump(mode="json")
            # Serialize/revalidate before updating the session; keep bounded
            # history plus structured state (not a DB or browser localStorage).
            next_history = [*req.chat_history,
                            ChatHistoryItem(role="user", content=payload.message),
                            ChatHistoryItem(role="assistant", content=response.answer[:4000])][-4:]
            session.update(
                profile=payload.profile, history=next_history,
                state=response.internal_conversation_state or response.conversation_state,
                turns=1 if profile_changed else session["turns"] + 1,
            )
            return {
                "response": public,
                "diagnostics": {
                    "calls": events,
                    "llm_output_adopted": (
                        response.answer_source in {"llm", "rag", "hybrid"}
                        and response.fallback_reason not in {"rag_reviewed_excerpt_answer", "rag_generated_answer_rejected"}
                    ),
                    "answer_source": response.answer_source,
                    "explanation": _explanation(response, events, usage),
                    "fallback_reason": response.fallback_reason,
                    "validation_failure_types": response.validation_failure_types,
                    "external_services_attempted": usage["services"],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "turn": session["turns"],
                },
            }
        finally:
            lock.release()

    @app.post("/ask")
    async def ask(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, "입력이 너무 큽니다.")
        try:
            payload = PreviewInput.model_validate_json(bytes(body))
        except ValidationError:
            # Avoid reflecting the user's full input or arbitrary extra fields.
            raise HTTPException(422, "1~4,000자의 질문과 지정된 가상 프로필을 선택해 주세요.") from None
        try:
            return await run_in_threadpool(process, payload)
        except HTTPException:
            raise
        except Exception:
            # Neither provider exception text nor prompts/keys belong in a UI.
            raise HTTPException(500, "처리 중 오류가 발생했습니다. 대화를 초기화하고 다시 확인해 주세요.") from None

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="가상 프로필 전용 로컬 챗봇 확인 화면")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-external", action="store_true",
                        help="화면에서 별도로 동의한 질문의 외부 LLM/검색 호출 허용 (전송·비용 발생 가능)")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port는 1024~65535 사이여야 합니다.")
    import uvicorn
    print(f"로컬 확인 화면: http://127.0.0.1:{args.port}")
    print("가상 프로필·가상 제품만 사용합니다. 운영 DB/채팅 파일 저장 없음. Ctrl+C로 종료합니다.")
    print("외부 호출 가능 (매 질문 동의 필요)" if args.allow_external else "외부 호출 꺼짐 · 규칙 답변 확인")
    uvicorn.run(create_preview_app(port=args.port, allow_external=args.allow_external),
                host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
