"""Real shipped excerpts, offline loader and fake providers; no private inputs."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from app import knowledge_import as cli
from app.api.response_contract import PublicChatResponse
from app.core.models import ChatResponse
from app.infrastructure import knowledge_review as review, rag
from app.infrastructure.knowledge_sources import ADDITIONAL_PDF_SOURCES, EXPLANATION_WEB_SOURCES, SOURCES, WEB_SOURCES
from app.services import hybrid
from app.services.knowledge_preview import preview_released_sources
from rag_test_helpers import grounding_review


OFFICIAL_DIR = Path(__file__).resolve().parents[1] / "app" / "knowledge" / "official"
CAPTURE_DIR = review.REFERENCE_DIR


@pytest.fixture
def packaged_sources(monkeypatch, tmp_path):
    # Content regression is independent of the wall clock; expiry itself is
    # tested explicitly. Deployment still uses the real review deadline.
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(review, "date", ReviewDate)
    for path in OFFICIAL_DIR.glob("*.source.json"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path))
    rag.reset_rag_cache()
    yield tmp_path
    rag.reset_rag_cache()


def test_shipped_references_are_traceable_and_excerpts_are_released(packaged_sources):
    assert len(SOURCES) == 3  # Offline prepare still requires only the supplied files.
    assert len(ADDITIONAL_PDF_SOURCES) == 4
    assert len(WEB_SOURCES) == 4
    assert len(EXPLANATION_WEB_SOURCES) == 11
    for spec in WEB_SOURCES + EXPLANATION_WEB_SOURCES:
        content = (CAPTURE_DIR / spec.filename).read_bytes()
        capture = json.loads(content)
        assert capture["capture_kind"] == "selected_web_text_not_full_html"
        assert capture["source_id"] == spec.source_id
        assert capture["url"] == spec.url
        assert capture["publisher"] == spec.publisher
        assert capture["title"] == spec.title
        if spec in WEB_SOURCES:
            assert capture["rights"].startswith("KOGL-1")
        else:
            assert capture["rights"] == "facts-only-independent-wording"
            assert "전체" in capture["limitations"]
        assert hashlib.sha256(content).hexdigest() == review.SOURCE_REVIEWS[spec.source_id].sha256
    entries = preview_released_sources()
    assert len(entries) == 40
    assert sum("웹 본문" in e["citation"] for e in entries) == 18
    assert sum(" | PDF " in e["citation"] for e in entries) == 22
    assert all("요약·재구성" in e["citation"] for e in entries)
    assert all(e["runtime_eligible"] for e in entries)


def test_capture_notes_raw_medical_content_and_private_paths_do_not_enter_rag(packaged_sources):
    documents = rag._load_documents()
    assert len(documents) == 40
    text = " ".join(d.page_content for d in documents)
    for excluded in ("rights", "source_sha256", "reference.json", "진료", "치료", "500g", "법령"):
        assert excluded not in text
    assert all(d.metadata["source_kind"] == "official_excerpt" for d in documents)
    assert len(rag._split_rag_documents(documents, {"chunk_size": 10, "chunk_overlap": 0})) == 40


def test_web_source_must_not_claim_a_pdf_page_even_if_digest_approved(packaged_sources, monkeypatch):
    path = packaged_sources / "allergy-purchase-check.source.json"
    payload = json.loads(path.read_text())
    payload["page"] = 3
    excerpt = review.ReviewedExcerpt.model_validate_json(json.dumps(payload))
    monkeypatch.setattr(review, "RELEASED_EXCERPT_HASHES", {excerpt.document_id: review.excerpt_digest(excerpt)})
    with pytest.raises(ValueError, match="must not invent"):
        review.parse_released_excerpt(excerpt.model_dump_json())


@pytest.mark.parametrize("change", ["missing", "tamper", "symlink"])
def test_capture_evidence_must_ship_unchanged(packaged_sources, tmp_path, monkeypatch, change):
    captures = tmp_path / "evidence"
    captures.mkdir()
    monkeypatch.setattr(review, "REFERENCE_DIR", captures)
    for spec in WEB_SOURCES:
        target = captures / spec.filename
        if change == "missing":
            continue
        if change == "symlink":
            target.symlink_to(CAPTURE_DIR / spec.filename)
        else:
            target.write_bytes((CAPTURE_DIR / spec.filename).read_bytes() + b" ")
    with pytest.raises(ValueError):
        preview_released_sources()


def test_actual_release_dates_reject_future_and_expired_use(packaged_sources):
    text = (packaged_sources / "allergy-purchase-check.source.json").read_text()
    for day in (date(2026, 9, 30), date(2027, 4, 2)):
        with pytest.raises(ValueError, match="expired or future"):
            review.parse_released_excerpt(text, today=day)


@pytest.mark.parametrize("query", ["", " ", "x" * 201, 42])
def test_offline_preview_rejects_bad_queries(packaged_sources, query):
    with pytest.raises(ValueError, match="검색어"):
        preview_released_sources(query)


def test_released_preview_does_not_require_private_files_or_call_models(packaged_sources, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("No network, embeddings or LLM in released preview")

    monkeypatch.setattr(rag, "build_vectorstore", forbidden)
    monkeypatch.setattr(hybrid, "generate_text", forbidden)
    monkeypatch.setattr(cli, "load_bundle", forbidden)
    assert cli.main(["released", "--query", "알레르기"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["llm_called"] is False
    assert result["index_built"] is False
    assert len(result["results"]) == 9
    assert any("kca.go.kr" in entry["citation"] for entry in result["results"])
    assert preview_released_sources("아무관련없는검색어") == []


@pytest.mark.parametrize(("filename", "message", "intent", "answer", "host"), [
    ("allergy-purchase-check.source.json", "식품 알레르기 안내", "general_allergy_guide",
     "식품을 구입하기 전에는 자신에게 알레르기를 일으키는 식품이 포함되어 있는지 확인하세요.", "kca.go.kr"),
    ("diet-reduction-direction.source.json", "식생활 영양 안내", "general_nutrition_guide",
     "일반적인 식생활 방향으로 나트륨, 당류, 포화지방산의 섭취를 줄이는 것을 안내합니다.", "korea.kr"),
    ("sodium-practical-choices.source.json", "저염식 조리 방법", "general_diet_guide",
     "양념을 적게 넣고 마지막에 간하세요. 외식할 때는 짜지 않게 조리해 달라고 요청할 수 있습니다.", "korea.kr"),
])
def test_real_excerpt_to_mock_search_to_mock_llm_to_public_sources(
    packaged_sources, monkeypatch, filename, message, intent, answer, host,
):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    documents = [d for d in rag._load_documents() if d.metadata["source"] == filename]

    class LocalStore:
        def similarity_search(self, _query, k, **_kwargs):
            return documents[:k]

    monkeypatch.setattr(rag, "build_vectorstore", lambda: LocalStore())
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    monkeypatch.setattr(rag, "external_ai_processing_enabled", lambda: True)
    monkeypatch.setattr(rag, "external_ai_request_allowed", lambda: True)
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if prompt.startswith("너는 식품 설명의 독립 근거 검토자"):
            return grounding_review(answer, [documents[0].page_content])
        return answer

    monkeypatch.setattr(hybrid, "generate_text", generate)
    base = ChatResponse(intent=intent, answer="기본 규칙 안내", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, message, None, None)
    assert result.answer == answer
    assert result.answer_source == "rag"
    assert host in result.sources[0]
    assert "웹 본문" in result.sources[0]
    assert documents[0].page_content in str(prompts[0])
    public = PublicChatResponse.model_validate(result, from_attributes=True).model_dump(mode="json")
    assert public["sources"] == ChatResponse.model_validate_json(result.model_dump_json()).sources
    assert "source_details" not in public and "source_sha256" not in str(public)


@pytest.fixture
def sodium_entry(packaged_sources):
    document = next(d for d in rag._load_documents()
                    if d.metadata["source"] == "sodium-practical-choices.source.json")
    reference = rag._official_reference_from_file(
        document.metadata["source"], document.metadata["source_sha256"],
    )
    return {
        "source": reference["source"], "content": document.page_content,
        "source_file": document.metadata["source"],
        "source_sha256": document.metadata["source_sha256"],
        "text": f"[{reference['citation']}] {document.page_content}",
    }


@pytest.mark.parametrize("entries", [
    [],
    [{"source": "internal-policy:nutrition/rules.md", "text": "식품 영양 설명"}],
    [{"source": "https://example.invalid/food", "text": "검토하지 않은 정보"}],
    [{"source": "nutrition/rules.md", "text": "과거 형식의 내부 문서"}],
])
@pytest.mark.parametrize("configuration", ["default", "legacy", "general"])
def test_unapproved_or_internal_context_does_not_enable_general_llm(
    monkeypatch, entries, configuration,
):
    monkeypatch.delenv("REQUIRE_RAG_CONTEXT_FOR_LLM", raising=False)
    monkeypatch.delenv("REQUIRE_RAG_CONTEXT_FOR_GENERAL_LLM", raising=False)
    if configuration != "default":
        name = "REQUIRE_RAG_CONTEXT_FOR_LLM" if configuration == "legacy" else "REQUIRE_RAG_CONTEXT_FOR_GENERAL_LLM"
        monkeypatch.setenv(name, "true")
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: entries)

    def forbidden(_prompt):
        pytest.fail("An internal policy document is not reviewed food evidence")

    monkeypatch.setattr(hybrid, "generate_text", forbidden)
    base = ChatResponse(intent="general_nutrition_guide", answer="규칙 안내", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "식품 영양 안내", None, None)
    assert result.answer == base.answer
    assert result.answer_source == "rules"
    assert result.fallback_reason == "rag_context_missing_for_general_llm"
    assert result.sources == []


@pytest.mark.parametrize("answer,accepted", [
    ("양념을 적게 넣고 마지막에 간하세요.", True),
    ("하루에 소금을 5g 드세요.", False),
    ("하루에 소금을 987g 드세요.", False),
])
def test_only_verified_body_not_title_policy_or_provider_text_is_food_evidence(
    sodium_entry, monkeypatch, answer, accepted,
):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    # The official title really contains 5g, but that part was NOT approved.
    assert "5g" in sodium_entry["text"]
    sodium_entry["text"] += " 공급자 추가 문장: 하루 소금 987g"
    entries = [
        {"source": "internal-policy:nutrition/rules.md", "text": "내부 규칙: 하루 소금 987g"},
        sodium_entry,
    ]
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: entries)
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if prompt.startswith("너는 식품 설명의 독립 근거 검토자"):
            return grounding_review(answer, [sodium_entry["content"]])
        return answer

    monkeypatch.setattr(hybrid, "generate_text", generate)
    base = ChatResponse(intent="general_diet_guide", answer="표시를 확인하세요.", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "저염식 방법 알려줘", None, None)
    assert len(prompts) == (2 if accepted else 1)
    assert sodium_entry["content"] in prompts[0]
    for excluded in ("5g", "987", "internal-policy:", "공급자 추가", "2011"):
        assert excluded not in prompts[0]
    if accepted:
        assert result.answer_source == "rag"
        assert result.answer == answer
        assert len(result.sources) == 1 and "korea.kr" in result.sources[0]
        assert result.grounding_context == [sodium_entry["content"]]
    else:
        assert result.answer == sodium_entry["content"]
        assert result.answer_source == "rag"
        assert result.fallback_reason == "rag_generated_answer_rejected"
        assert "unsupported_number" in result.validation_failure_types


def test_explicit_no_rag_opt_out_does_not_relabel_internal_policy_as_evidence(monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    # Preserve the legacy explicit opt-out, but do not call the result RAG.
    monkeypatch.setenv("REQUIRE_RAG_CONTEXT_FOR_LLM", "false")
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: [
        {"source": "internal-policy:rules.md", "text": "내부 전용 지침"},
    ])
    monkeypatch.setattr(hybrid, "generate_text", lambda _prompt: "식품 표시를 확인하세요.")
    base = ChatResponse(intent="general_nutrition_guide", answer="식품 표시를 확인하세요.", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "식품 표시", None, None)
    assert result.answer_source == "llm"
    assert result.sources == result.grounding_context == []


def test_recipe_rights_are_cleared_but_do_not_approve_any_recipe(packaged_sources):
    capture = json.loads((CAPTURE_DIR / "foodsafety-cookrcp01.rights.json").read_text())
    status = review.source_release_status(capture["source_id"], capture["source_sha256"])
    assert capture["url"] == "https://www.data.go.kr/data/15060073/openapi.do"
    assert capture["rights"] == "unrestricted-as-listed"
    assert status["rights"] == "cleared"
    assert status["applicability"] == "recipe_review_required"
    assert status["runtime_allowed"] is False
    assert all("cookrcp01" not in e["source"] for e in preview_released_sources())


def test_sodium_offline_preview_contains_practice_not_dosage(packaged_sources):
    results = preview_released_sources("나트륨")
    assert len(results) >= 2
    practice = next(e for e in results if "sodium-practical-choices" in e["source"])
    assert "영양표시" in practice["text"] and "양념" in practice["text"] and "외식" in practice["text"]
    for excluded in ("5g", "2g", "혈압", "질병", "치료"):
        assert excluded not in practice["text"]
