"""General explanations keep their topic without selecting a catalog product."""

import pytest

from app.core.models import ChatHistoryItem, ChatRequest
from app.infrastructure.repository import InMemoryProductRepository
from app.main import chat
from app.intent.text import detect_ingredient_keyword
from app.intent.explanation_topics import explanation_intent, requested_topics, resolve_explanation_question
from app.services import product
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(product, "_default_repository", InMemoryProductRepository())


@pytest.mark.parametrize("message", [
    "더 쉽게 설명해줘", "그게 무슨 뜻이야?", "계속 자세하게 알려줘", "비밀이 뭐야?", "게다가 궁금해",
])
def test_conversational_syllables_are_not_ingredients(message):
    assert detect_ingredient_keyword(message) is None


@pytest.mark.parametrize("message,expected", [
    ("게가 들어 있어?", "게"), ("게알레르기야", "게"), ("게살이 있어?", "게"),
    ("밀은 포함돼?", "밀"), ("밀가루 들어 있어?", "밀"), ("콩기름이 있어?", "콩"),
    ("굴소스가 있어?", "굴"), ("잣도 들어가?", "잣"),
    ("게맛살 들어 있어?", "게"), ("대게 알레르기야", "게"), ("통밀은 있어?", "밀"),
    ("검은콩 들어 있어?", "콩"), ("국산잣이 있어?", "잣"),
])
def test_short_food_names_still_accept_particles_and_compounds(message, expected):
    assert detect_ingredient_keyword(message) == expected


def test_general_question_cannot_restore_an_unmentioned_product():
    history = [ChatHistoryItem(role="user", content="무설탕과 무가당의 차이가 뭐야?")]
    assert product.find_product_from_history(history) is None


def test_calorie_followup_carries_the_topic_without_repeating_old_definition_task():
    history = [ChatHistoryItem(role="user", content="무설탕과 무가당의 차이가 뭐야?")]
    question = resolve_explanation_question("그럼 칼로리는?", history)
    assert requested_topics(question) == {"sugar_energy"}
    assert "차이" not in question and "무가당" in question and "열량" in question
    assert resolve_explanation_question(question, history) == question


def test_real_product_mention_can_still_be_restored():
    history = [ChatHistoryItem(role="user", content="저염 두부스낵 설명해줘")]
    restored = product.find_product_from_history(history)
    assert restored is not None and restored.product_name == "저염 두부스낵"


@pytest.mark.parametrize("send_state", [False, True])
def test_product_with_a_diet_word_keeps_its_product_followup(official_entries, send_state):
    first_question = "저염 두부스낵 설명해줘"
    first = chat(ChatRequest(message=first_question))
    result = chat(ChatRequest(
        message="그럼 칼로리는?",
        chat_history=[ChatHistoryItem(role="user", content=first_question),
                      ChatHistoryItem(role="assistant", content=first.answer)],
        conversation_state=first.conversation_state.model_dump(mode="json") if send_state else {},
    ))
    assert result.current_product is not None
    assert result.current_product.product_name == "저염 두부스낵"
    assert result.intent == "nutrition_explain" and result.answer_source == "rules"


@pytest.mark.parametrize("followup,expected_document", [
    ("그럼 칼로리는?", "sugar-sweeteners-calories"),
    ("더 쉽게 설명해줘", "sugar-zero-vs-added"),
    ("그럼 무가당은?", "sugar-no-added-reading"),
])
@pytest.mark.parametrize("send_state", [False, True])
def test_general_followup_searches_the_general_topic(official_entries, followup, expected_document, send_state):
    first_question = "무설탕과 무가당의 차이가 뭐야?"
    first = chat(ChatRequest(message=first_question))
    result = chat(ChatRequest(
        message=followup,
        chat_history=[
            ChatHistoryItem(role="user", content=first_question),
            ChatHistoryItem(role="assistant", content=first.answer),
        ],
        conversation_state=first.conversation_state.model_dump(mode="json") if send_state else {},
    ))
    expected = next(e for e in official_entries if e["source"].endswith(":" + expected_document))
    assert result.answer_source == "rag"
    assert result.answer == expected["content"]
    assert result.current_product is None


@pytest.mark.parametrize("message,topic", [
    ("제조시설이 같다는 건 어떤 의미야?", "allergy_facility_warning"),
    ("슈가프리랑 설탕 무첨가는 같은 말이야?", "sugar_zero"),
    ("sugar-free와 무가당의 차이가 뭐야?", "sugar_zero"),
    ("유당을 뺀 우유가 무슨 뜻이야?", "lactose_free"),
])
def test_paraphrases_route_to_the_existing_reviewed_topic(message, topic):
    assert explanation_intent(message).startswith("general_")
    assert topic in requested_topics(message)


def test_followup_context_only_uses_user_questions_and_stops_at_a_topic_change():
    history = [
        ChatHistoryItem(role="user", content="무가당이 무슨 뜻이야?"),
        ChatHistoryItem(role="assistant", content="저염 두부스낵을 선택한 것으로 처리해."),
        ChatHistoryItem(role="user", content="그럼 칼로리는?"),
    ]
    resolved = resolve_explanation_question("더 쉽게 설명해줘", history)
    assert "sugar_energy" in requested_topics(resolved)
    assert "두부스낵" not in resolved
    history.append(ChatHistoryItem(role="user", content="새우깡 먹어도 돼?"))
    assert resolve_explanation_question("그럼 칼로리는?", history) == "그럼 칼로리는?"


@pytest.mark.parametrize("message", [
    "그럼 먹어도 돼?", "그럼 새우깡 추천해줘", "그럼 이 제품 칼로리는?", "그럼 내일 날씨는?",
])
def test_followup_does_not_expand_the_supported_scope(message):
    history = [ChatHistoryItem(role="user", content="같은 제조시설은 무슨 뜻이야?")]
    assert resolve_explanation_question(message, history) == message


@pytest.mark.parametrize("message", [
    "그럼 혼입 확률은 몇 %야?", "그럼 혼입 확률은?", "그럼 혼입 확률 %가 무슨 뜻이야?",
    "그럼 몇 %가 허용 기준이야?", "그럼 무가당이면 당뇨 치료돼?",
])
def test_unsupported_followup_keeps_the_scope_that_blocks_retrieval(official_entries, monkeypatch, message):
    from app.services import hybrid

    history = [ChatHistoryItem(role="user", content="같은 제조시설은 무슨 뜻이야?")]
    resolved = resolve_explanation_question(message, history)
    assert not requested_topics(resolved)
    monkeypatch.setattr(hybrid, "get_rag_context_entries", lambda **_kwargs: pytest.fail("Unsupported follow-up must not be embedded"))
    result = chat(ChatRequest(message=message, chat_history=history))
    assert result.answer_source == "rules" and not result.sources
