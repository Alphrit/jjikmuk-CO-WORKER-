import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest
from langchain_core.documents import Document

from app.core.models import ChatResponse
from app.api.response_contract import PublicChatResponse
from app.infrastructure import knowledge_review as review, rag
from app.infrastructure.knowledge_sources import SourceSpec
from app.services import hybrid
from rag_test_helpers import grounding_review


@pytest.fixture
def released_source(monkeypatch, tmp_path):
    spec = SourceSpec("fixture-official", "fixture.pdf", "pdf", "합성 검증 자료", "테스트 기관",
                      "https://example.invalid/fixture", "테스트 전용")
    monkeypatch.setattr(review, "SOURCES", (spec,))
    monkeypatch.setattr(review, "SOURCE_REVIEWS", {
        spec.source_id: review.SourceReview("a" * 64, "cleared", "reviewed", "합성 테스트"),
    })
    monkeypatch.setattr(review, "RELEASED_EXCERPT_HASHES", {})
    payload = {
        "format": "food-rag-excerpt-v1", "document_id": "fixture-label-reading",
        "source_id": spec.source_id, "source_sha256": "a" * 64, "page": 3,
        "section": "식품 표시 확인", "text": "식품을 선택할 때 원재료 표시를 확인합니다.",
        "topics": ["allergy_label"],
        "content_review": "approved", "usage_scope": "general_food_explanation",
        "reviewed_at": date.today().isoformat(),
        "review_due": (date.today() + timedelta(days=30)).isoformat(),
    }
    excerpt = review.ReviewedExcerpt.model_validate_json(json.dumps(payload))
    review.RELEASED_EXCERPT_HASHES[excerpt.document_id] = review.excerpt_digest(excerpt)
    path = tmp_path / "label.source.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path))
    monkeypatch.setattr(rag, "external_ai_processing_enabled", lambda: True)
    monkeypatch.setattr(rag, "external_ai_request_allowed", lambda: True)
    rag.reset_rag_cache()
    yield payload, path
    rag.reset_rag_cache()


def install_retrieval(monkeypatch):
    documents = rag._split_rag_documents(rag._load_documents())

    class LocalFixtureStore:
        def similarity_search(self, _query, k, **_kwargs):
            return documents[:k]

    monkeypatch.setattr(rag, "build_vectorstore", lambda: LocalFixtureStore())
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    return documents


def test_current_downloads_cannot_be_enabled_merely_by_marking_approved():
    for spec in review.IMPORT_SOURCES:
        source_id = spec.source_id
        decision = review.SOURCE_REVIEWS[source_id]
        assert not review.source_release_status(source_id, decision.sha256)["runtime_allowed"]
    assert review.SOURCE_REVIEWS["kdca-food-allergy-management"].rights == "permission_required"
    assert review.SOURCE_REVIEWS["mfds-label-2026-37"].applicability == "clause_review_required"
    assert not any(key.startswith(("mfds-label-", "kdca-food-", "foodsafety-cook")) for key in review.RELEASED_EXCERPT_HASHES)


def test_source_revision_must_match_reviewed_file():
    result = review.source_release_status("kdca-food-allergy-management", "b" * 64)
    assert not result["runtime_allowed"]
    assert result["rights"] == "unreviewed"


def test_approved_json_loads_as_text_and_keeps_semantic_unit(released_source):
    payload, path = released_source
    manifest = rag._build_document_manifest()
    docs = rag._load_documents(expected_manifest=manifest)
    assert manifest[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert docs[0].page_content == payload["text"]
    assert docs[0].metadata["source_kind"] == "official_excerpt"
    assert len(rag._split_rag_documents(docs, {"chunk_size": 5, "chunk_overlap": 0})) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("page", 4), ("section", "다른 단락"), ("text", "변조된 본문"),
    ("source_sha256", "b" * 64), ("source_id", "unregistered"),
    ("document_id", "new-unapproved-id"), ("content_review", "pending"),
    ("usage_scope", "medical_treatment"), ("url", "https://attacker.invalid"),
    ("page", True), ("page", 0), ("page", 501), ("text", "x" * 3001),
    ("page", None),
    ("topics", ["nutrition_basis"]),
])
def test_altered_or_unapproved_excerpt_cannot_enter_manifest(released_source, field, value):
    payload, path = released_source
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        rag._build_document_manifest()


def test_withdrawn_permission_disables_existing_file(released_source):
    payload, _ = released_source
    review.SOURCE_REVIEWS[payload["source_id"]] = replace(
        review.SOURCE_REVIEWS[payload["source_id"]], rights="permission_required",
    )
    with pytest.raises(ValueError, match="rights/applicability"):
        rag._load_documents()


def test_expired_review_is_not_accepted_even_with_approved_digest(released_source):
    payload, path = released_source
    payload["reviewed_at"] = (date.today() - timedelta(days=2)).isoformat()
    payload["review_due"] = (date.today() - timedelta(days=1)).isoformat()
    excerpt = review.ReviewedExcerpt.model_validate_json(json.dumps(payload))
    review.RELEASED_EXCERPT_HASHES[excerpt.document_id] = review.excerpt_digest(excerpt)
    path.write_text(excerpt.model_dump_json())
    with pytest.raises(ValueError, match="expired"):
        rag._build_document_manifest()


def test_provider_cannot_replace_citation_page_or_url(released_source, monkeypatch):
    payload, _ = released_source
    docs = install_retrieval(monkeypatch)
    docs[0].metadata.update(url="https://attacker.invalid", page=999, citation="조작된 제목")
    entries = rag.get_rag_context_entries("표시 확인")
    assert len(entries) == 1
    assert "PDF 3쪽" in entries[0]["text"]
    assert "attacker" not in str(entries)
    citations = hybrid._sources_from_entries(entries)
    assert len(citations) == 1
    assert "PDF 3쪽" in citations[0]
    assert "https://example.invalid/fixture" in citations[0]
    assert "internal-policy:" not in citations[0]


def test_modified_retrieved_body_is_rejected(released_source, monkeypatch):
    docs = install_retrieval(monkeypatch)
    docs[0].page_content = "근거 없는 안전 표현"
    assert rag.get_rag_context_entries("표시 확인") == []


def test_final_citation_rechecks_content_and_local_file(released_source, monkeypatch):
    _, path = released_source
    install_retrieval(monkeypatch)
    entries = rag.get_rag_context_entries("표시 확인")
    assert hybrid._sources_from_entries(entries)
    assert not hybrid._sources_from_entries([{**entries[0], "content": "다른 본문"}])
    path.write_text("{}")
    assert hybrid._sources_from_entries(entries) == []


def test_citations_survive_public_response_and_storage_json_roundtrip(released_source, monkeypatch):
    install_retrieval(monkeypatch)
    citations = hybrid._sources_from_entries(rag.get_rag_context_entries("표시 확인"))
    internal = ChatResponse(intent="general_allergy_guide", answer="표시를 확인해 주세요.",
                            risk_level="unknown", sources=citations)
    public = PublicChatResponse.model_validate(internal, from_attributes=True).model_dump(mode="json")
    restored = ChatResponse.model_validate_json(internal.model_dump_json())
    assert public["sources"] == restored.sources == citations
    assert "source_details" not in public
    assert "source_sha256" not in json.dumps(public)


def test_public_source_helper_rejects_unverified_external_entries():
    assert hybrid._sources_from_entries([
        {"source": "official-source:fake", "citation": "공식이라는 주장"},
        {"source": "https://example.invalid/fake"},
    ]) == []


def test_general_llm_adopts_citation_without_reintroducing_internal_fields(released_source, monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    install_retrieval(monkeypatch)
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    answer = "식품을 선택할 때 원재료 표시를 확인해 주세요."
    body = released_source[0]["text"]
    monkeypatch.setattr(hybrid, "generate_text", lambda prompt:
                        grounding_review(answer, [body]) if prompt.startswith("너는 식품 설명의 독립 근거 검토자") else answer)
    base = ChatResponse(intent="general_allergy_guide", answer="기본 안내", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "알레르기 표시 보는 법", None, None)
    assert result.answer_source == "rag"
    assert "PDF 3쪽" in result.sources[0]
    assert not result.source_details


def test_permission_changed_during_generation_falls_back(released_source, monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_RESPONSE_MODE", "generated")
    payload, _ = released_source
    install_retrieval(monkeypatch)
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)

    def generate(_prompt):
        review.SOURCE_REVIEWS[payload["source_id"]] = replace(
            review.SOURCE_REVIEWS[payload["source_id"]], rights="permission_required",
        )
        return "식품을 선택할 때 원재료 표시를 확인해 주세요."

    monkeypatch.setattr(hybrid, "generate_text", generate)
    base = ChatResponse(intent="general_allergy_guide", answer="기본 안내", risk_level="unknown")
    result = hybrid.try_general_llm_fallback(base, "알레르기 표시 보는 법", None, None)
    assert result.answer == "기본 안내"
    assert result.answer_source == "rules"
    assert result.sources == []


def test_reviewed_documents_cannot_escape_knowledge_directory(released_source):
    _, path = released_source
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("../label.source.json", str(path), "raw/../label.source.json"):
        entry = {"source": "official-source:fixture", "source_file": name,
                 "source_sha256": content_hash, "content": "fake"}
        assert rag.official_citation_from_entry(entry) is None
