"""Grounded general answers: approved wording, relevance, and no text generation."""

import json

import pytest

from app.core.models import ChatResponse
from app.infrastructure import knowledge_review as review, rag
from app.services import hybrid
from app.services.rag_grounding import answer_matches_reviewed_sources, select_reviewed_entries
from test_released_knowledge import packaged_sources


@pytest.fixture
def official_entries(packaged_sources, monkeypatch):
    monkeypatch.delenv("RAG_GENERAL_RESPONSE_MODE", raising=False)
    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(hybrid, "generate_text", lambda _prompt: pytest.fail("Generated facts are disabled"))
    entries = []
    for document in rag._load_documents():
        reference = rag._official_reference_from_file(
            document.metadata["source"], document.metadata["source_sha256"],
        )
        entries.append({
            "source": reference["source"], "source_file": document.metadata["source"],
            "source_sha256": document.metadata["source_sha256"],
            "content": " ".join(document.page_content.split()),
        })
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: entries)
    return entries


def base_response():
    return ChatResponse(intent="general_nutrition_guide", answer="확인할 정보가 부족합니다.", risk_level="unknown")


@pytest.mark.parametrize("message,expected", [
    ("무가당 표시 기준을 알려줘", "general_nutrition_guide"),
    ("무당과 무가당 차이가 뭐야?", "general_nutrition_guide"),
    ("무가당인데 왜 달아?", "general_nutrition_guide"),
    ("이거 무가당 제품인데 먹어도 돼?", "missing_product"),
    ("같은 제조시설은 무슨 뜻이야?", "general_allergy_guide"),
    ("교차접촉이 뭐야?", "general_allergy_guide"),
    ("동일 제조시설 표시와 함유 표시 차이", "general_allergy_guide"),
])
def test_claim_explanation_enters_chat_guide_without_losing_missing_product_guard(message, expected):
    from app.intent.rules import rule_based_classify_intent

    assert rule_based_classify_intent(message, None, None, []) == expected


@pytest.mark.parametrize("message,document_id", [
    ("영양표시를 읽는 방법 알려줘", "nutrition-label-basis"),
    ("100g당 수치와 한 봉지 기준을 어떻게 봐?", "nutrition-label-basis"),
    ("영양성분표 총 내용량 확인 방법", "nutrition-label-basis"),
    ("영양성분 기준치 퍼센트가 뭐야?", "nutrition-label-percentage"),
    ("영양표시의 %를 설명해줘", "nutrition-label-percentage"),
    ("식품 알레르기 표시 확인 방법", "allergy-label-reading"),
    ("같은 제조시설은 무슨 뜻이야?", "allergy-facility-warning"),
    ("우유 함유와 같은 제조시설 문구는 어떻게 달라?", "allergy-facility-warning"),
    ("동일 제조시설 표시와 함유 표시 차이", "allergy-facility-warning"),
    ("교차 접촉이 뭐야?", "allergy-cross-contact-definition"),
    ("알레르기 교차오염 뜻", "allergy-cross-contact-definition"),
    ("알레르기 공용설비는 무슨 뜻이야?", "allergy-cross-contact-definition"),
    ("알레르기 교차접촉을 줄이는 방법", "allergy-cross-contact-controls"),
    ("교차오염 관리를 위한 세척과 도구 분리", "allergy-cross-contact-controls"),
    ("외식할 때 알레르기 정보는 어디서 확인해?", "allergy-ordering-check"),
    ("배달 주문 전 알레르기 표시 확인 방법", "allergy-ordering-check"),
    ("알러지가 있으면 구매 전 무엇을 확인해?", "allergy-purchase-check"),
    ("저염식 조리 방법 알려줘", "sodium-practical-choices"),
    ("나트륨 줄이려고 외식할 때 어떻게 해?", "sodium-practical-choices"),
    ("소금 적게 쓰는 방법", "sodium-practical-choices"),
    ("무가당이면 당류가 없는 거야?", "sugar-no-added-reading"),
    ("무가당 주스의 당류를 확인하는 방법", "sugar-no-added-reading"),
    ("설탕 무첨가 식품을 고를 때 뭘 확인해?", "sugar-no-added-reading"),
    ("저지방 강조 문구는 어떻게 봐?", "nutrition-low-fat"),
    ("저당 기준은 몇 g이야?", "nutrition-low-sugar"),
    ("고단백 기준을 알려줘", "nutrition-high-protein"),
    ("무설탕과 무가당의 차이", "sugar-zero-vs-added"),
    ("제로슈거면 당류가 전혀 없어?", "sugar-zero-vs-added"),
    ("무가당인데 왜 달아?", "sugar-sweeteners-calories"),
    ("무가당이면 칼로리도 없는 거야?", "sugar-sweeteners-calories"),
    ("제로칼로리면 열량이 0이야?", "nutrition-zero-energy"),
    ("포장 앞면 문구만 보고 골라도 돼?", "nutrition-front-claims"),
    ("식생활 영양 안내", "diet-reduction-direction"),
])
def test_answer_uses_only_reviewed_body(official_entries, message, document_id):
    result = hybrid.try_general_llm_fallback(base_response(), message, None, None)
    expected = next(e for e in official_entries if e["source"].endswith(":" + document_id))
    assert result.answer_source == "rag"
    assert result.answer == expected["content"]
    assert result.grounding_context == [expected["content"]]
    assert result.sources == [rag.official_citation_from_entry(expected)]


@pytest.mark.parametrize("message", [
    "하루 권장 소금량", "저염 제품의 개인별 허용량은?",
    "글루텐프리 국내 인증 기준", "채식으로 질병을 치료하는 법",
    "교차오염이 없으면 안전해?", "알레르기 치료 방법", "영양표시 법정 시행일",
    "자료 없이 새로운 식품 지식을 만들어줘",
    "당뇨 환자가 무가당 주스 마셔도 돼?", "무가당이면 살이 빠져?",
    "감미료 부작용을 알려줘", "고단백 제품을 하루에 몇 개 먹으면 돼?",
    "영국 알레르기 혼입 표시 의무를 알려줘", "알레르기 표시 대상은 몇 종이야?",
    "같은 제조시설의 알레르기 혼입 확률은 몇 %야?", "교차접촉과 교차반응 차이",
    "세균 교차오염을 막는 법", "알레르기 공용설비 제품 먹어도 괜찮아?",
    "같은 제조시설 표시가 없으면 안전한가?", "가열하면 교차접촉 알레르기 성분이 없어져?",
])
def test_unsupported_questions_never_gain_a_source_or_generated_facts(official_entries, message):
    base = base_response()
    result = hybrid.try_general_llm_fallback(base, message, None, None)
    if result.fallback_reason == "rag_question_outside_evidence_scope":
        assert "충분히 설명하기 어려워요" in result.answer
    else:
        assert result.answer == base.answer
    assert result.answer_source == "rules"
    assert not result.sources
    assert result.fallback_reason in {None, "rag_question_outside_evidence_scope"}


def test_related_topic_is_not_sufficient_evidence(official_entries, monkeypatch):
    sodium = [e for e in official_entries if e["source"].endswith(":sodium-practical-choices")]
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: sodium)
    result = hybrid.try_general_llm_fallback(base_response(), "무가당이면 당류가 없어?", None, None)
    assert result.answer_source == "rules"
    assert not result.sources


def test_unsupported_allergy_details_do_not_send_a_query_for_embedding(official_entries, monkeypatch):
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: pytest.fail("Unsupported topic must not be embedded"))
    for question in ("영국 알레르기 혼입 표시 의무를 알려줘", "알레르기 혼입 확률은 몇 %야?"):
        result = hybrid.try_general_llm_fallback(base_response(), question, None, None)
        assert result.answer_source == "rules" and not result.sources
        assert result.fallback_reason == "rag_question_outside_evidence_scope"


@pytest.mark.parametrize("message,required,forbidden", [
    ("무당과 무가당의 차이", ["100g", "100ml", "0.5g 미만", "절대적인 0", "원료 자체"], []),
    ("무가당 표시 기준", ["꿀", "식품첨가물", "잼", "농축", "효소", "모두"], []),
    ("저당 기준", ["5g 미만", "2.5g 미만", "제조·가공", "2025년 12월"], ["5g 이하"]),
    ("저지방 기준", ["3g 미만", "1.5g 미만", "축산물", "예외"], []),
    ("고단백 기준", ["네 조건 중 하나", "100g", "100ml", "100kcal", "1회 섭취참고량", "2배", "권장량"], []),
    ("제로칼로리 기준", ["100ml당", "4kcal 미만", "제조·가공", "100g"], ["100g당 4kcal"]),
    ("무가당인데 왜 달아?", ["감미료", "당류", "열량", "원재료명"], ["안전합니다"]),
])
def test_pdf_answers_preserve_relevant_units_conditions_and_exceptions(official_entries, message, required, forbidden):
    result = hybrid.try_general_llm_fallback(base_response(), message, None, None)
    assert result.answer_source == "rag"
    assert all(term in result.answer for term in required)
    assert not any(term in result.answer for term in forbidden)
    assert all("mfds.go.kr" in source and "PDF" in source for source in result.sources)
    assert answer_matches_reviewed_sources(result.answer, result.sources)


def test_multi_topic_question_requires_both_topics(official_entries):
    basis = [e for e in official_entries if e["source"].endswith(":nutrition-label-basis")]
    assert not select_reviewed_entries("100g 기준과 %를 알려줘", basis)
    selected = select_reviewed_entries("100g 기준과 %를 알려줘", official_entries)
    assert {e["source"].split(":")[-1] for e in selected} == {
        "nutrition-label-basis", "nutrition-label-percentage",
    }


def test_provider_cannot_supply_its_own_topic_scope(official_entries):
    allergy = next(e for e in official_entries if e["source"].endswith(":allergy-purchase-check"))
    assert not select_reviewed_entries("100g 기준 설명", [{**allergy, "topics": "nutrition_basis"}])


def test_contact_definition_alone_does_not_support_controls_or_both_topics(official_entries):
    definition = [entry for entry in official_entries if entry["source"].endswith(":allergy-cross-contact-definition")]
    assert not select_reviewed_entries("알레르기 교차접촉 관리 방법", definition)
    question = "알레르기 교차접촉이 뭐고 어떻게 줄여?"
    assert not select_reviewed_entries(question, definition)
    selected = select_reviewed_entries(question, official_entries)
    assert {entry["source"].split(":")[-1] for entry in selected} == {
        "allergy-cross-contact-definition", "allergy-cross-contact-controls",
    }


def test_allergy_pdf_answers_keep_country_and_page_boundaries(official_entries):
    for document_id, page, required in [
        ("allergy-label-reading", 8, ["원재료명", "별도", "혼입 가능성"]),
        ("allergy-facility-warning", 8, ["원재료", "의미가 다릅니다", "개인별 판정은 아닙니다"]),
        ("allergy-cross-contact-definition", 57, ["2023년", "영국", "국내 기준으로 적용한 설명은 아닙니다"]),
        ("allergy-cross-contact-controls", 59, ["전용 도구", "세척", "영국", "단정하지 않습니다"]),
        ("allergy-ordering-check", 9, ["포스터", "홈페이지·앱", "확인"]),
    ]:
        entry = next(entry for entry in official_entries if entry["source"].endswith(":" + document_id))
        assert all(term in entry["content"] for term in required)
        assert f"PDF {page}쪽" in rag.official_citation_from_entry(entry)
        assert answer_matches_reviewed_sources(entry["content"], [rag.official_citation_from_entry(entry)])
        for excluded in ("14종", "19종", "22종", "ppm", "약물", "치료", "의무입니다"):
            assert excluded not in entry["content"]


def test_topic_change_requires_new_content_review(official_entries, packaged_sources):
    path = packaged_sources / "nutrition-label-basis.source.json"
    payload = json.loads(path.read_text())
    payload["topics"] = ["sugar_claims"]
    with pytest.raises(ValueError, match="approved"):
        review.parse_released_excerpt(json.dumps(payload))


def test_official_only_search_filters_before_top_k(official_entries, monkeypatch):
    documents = rag._load_documents()
    calls = []
    class Store:
        def similarity_search(self, query, k, **kwargs):
            calls.append(kwargs)
            return documents[:k]
    monkeypatch.setattr(rag, "external_ai_processing_enabled", lambda: True)
    monkeypatch.setattr(rag, "external_ai_request_allowed", lambda: True)
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    monkeypatch.setattr(rag, "build_vectorstore", lambda: Store())
    assert rag.get_rag_context_entries("영양표시", official_only=True)
    assert calls == [{"filter": {"source_kind": "official_excerpt"}}]


def test_topic_filter_uses_approved_paths_and_skips_uncovered_topics(official_entries, monkeypatch):
    documents = [doc for doc in rag._load_documents() if doc.metadata["source"] == "nutrition-label-basis.source.json"]
    calls = []
    class Store:
        def similarity_search(self, query, k, **kwargs):
            calls.append(kwargs)
            return documents[:k]
    monkeypatch.setattr(rag, "external_ai_processing_enabled", lambda: True)
    monkeypatch.setattr(rag, "external_ai_request_allowed", lambda: True)
    monkeypatch.setattr(rag, "_require_current_index", lambda _path: {})
    monkeypatch.setattr(rag, "build_vectorstore", lambda: Store())
    assert rag.get_rag_context_entries("영양표시", official_only=True, source_topics={"nutrition_basis"})
    assert calls == [{"filter": {"$and": [
        {"source_kind": "official_excerpt"},
        {"source": {"$in": ["nutrition-label-basis.source.json"]}},
    ]}}]
    assert not rag.get_rag_context_entries("알 수 없는 주제", official_only=True, source_topics={"uncovered"})
    assert len(calls) == 1


def test_real_citation_does_not_validate_an_invented_statement(official_entries):
    from evaluation.run_eval import evaluate_case

    entry = next(e for e in official_entries if e["source"].endswith(":sodium-practical-choices"))
    sources = [rag.official_citation_from_entry(entry)]
    assert answer_matches_reviewed_sources(entry["content"], sources)
    fabricated = "모든 가공식품은 동일한 원료로 만들어집니다."
    assert not answer_matches_reviewed_sources(fabricated, sources)
    result = evaluate_case(
        {"id": "invented-statement-with-real-citation", "category": "rag_grounding"},
        {"answer_source": "rag", "answer": fabricated, "sources": sources}, 200,
    )
    assert "unsupported_rag_statement" in {finding["type"] for finding in result["trust_findings"]}
    assert result["checks"]["answer_grounding_score"] == 0.0


@pytest.mark.parametrize("fabricate", [False, True])
def test_live_evaluator_checks_official_citation_and_body_without_requiring_generation(
    official_entries, monkeypatch, fabricate,
):
    from types import SimpleNamespace
    from evaluation.verify_llm_rag import _evaluate_scenario

    monkeypatch.setenv("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET", "evaluation-secret-" * 3)
    entry = next(e for e in official_entries if e["source"].endswith(":nutrition-label-basis"))
    scenario = {
        "id": "official-citation-check", "description": "공식 출처와 본문 검사",
        "payload": {"message": "영양표시 기준량 확인 방법"},
        "expected_intent": "general_nutrition_guide", "expect_rag": True,
        "expected_document": "nutrition-label-basis",
    }
    response = {
        "intent": "general_nutrition_guide", "answer_source": "rag",
        "answer": "모든 식품의 표시 기준량은 같습니다." if fabricate else entry["content"],
        "sources": [rag.official_citation_from_entry(entry)],
        "external_ai_processing": {"used": True, "services": ["rag"]},
    }
    client = SimpleNamespace(post=lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: response))
    result = _evaluate_scenario(client, scenario, nonce_suffix="offline-fixture")
    assert result["passed"] is not fabricate
