"""OpenAI may only rephrase backend product answers; facts must survive."""

import pytest

from app.core.models import ChatResponse
from app.infrastructure import llm
from app.services import answer_polish

RULE = ("새우깡 기준으로 확인했어요.\n"
        "웹테스트: 선택한 알레르기 조건과 충돌하는 표시가 확인됐어요. 이 상품은 피해주세요. 충돌 항목: 새우. 지정된 필수 식단 조건은 없어요.\n"
        "질환이나 개인별 섭취 가능량은 이 판정에 포함되지 않아요.")


class _Named:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def _response(answer=RULE):
    return ChatResponse(intent="can_i_eat", answer=answer, answer_source="backend", risk_level="high")


def test_required_terms_read_models_and_dicts():
    response = ChatResponse(intent="can_i_eat", answer=RULE, answer_source="backend", risk_level="high")
    object.__setattr__(response, "per_profile_results", [_Named(profile_name="웹테스트"), {"profile_name": "엄마"}])
    object.__setattr__(response, "current_product", _Named(product_name="새우깡"))
    terms = answer_polish._required_terms(RULE, response)
    assert {"새우깡", "새우"} <= set(terms)
    assert "웹테스트" not in terms and "엄마" not in terms  # one named person may go unnamed
    two = ChatResponse(intent="can_i_eat", answer=RULE + "\n엄마: 확인 필요", answer_source="backend", risk_level="high")
    object.__setattr__(two, "per_profile_results", [_Named(profile_name="웹테스트"), {"profile_name": "엄마"}])
    assert {"웹테스트", "엄마"} <= set(answer_polish._required_terms(two.answer, two))


@pytest.fixture
def polish_on(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS", "true")
    yield lambda reply: llm.set_llm_client(lambda _prompt: reply)
    llm.set_llm_client(None)


def test_faithful_rewrite_is_used(polish_on):
    polish_on("새우깡에는 새우가 들어 있다고 표시돼 있어서 웹테스트 님은 피해 주세요. 따로 지정된 식단 조건은 없어요. "
              "질환이나 개인별 섭취 가능량은 이 판정에 포함되지 않아요.")
    assert answer_polish.polish_product_answer(_response()).answer.startswith("새우깡에는 새우가")


@pytest.mark.parametrize("reply", [
    "웹테스트 님, 새우깡은 피해 주세요. 질환은 판단하지 않았어요.",                     # drops the conflicting allergen
    "새우깡에는 새우가 있지만 조금은 먹어도 괜찮아요. 웹테스트 님 질환은 판단하지 않았어요. 피해도 돼요.",  # reassurance
    "새우깡에는 새우와 땅콩이 있어 웹테스트 님은 피해 주세요. 질환은 판단하지 않았어요.",  # adds an allergen
    "새우깡 100g에 새우가 있어 웹테스트 님은 피해 주세요. 질환은 판단하지 않았어요.",    # adds a number
    "- 새우깡: 새우 포함\n- 웹테스트: 피해 주세요\n- 질환 판단 안 함",                    # list formatting
    "",
])
def test_unfaithful_rewrite_keeps_the_rule_text(polish_on, reply):
    polish_on(reply)
    assert answer_polish.polish_product_answer(_response()).answer == RULE


def test_disabled_or_non_backend_answers_are_untouched(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS", "false")
    llm.set_llm_client(lambda _p: "다른 문장")
    try:
        assert answer_polish.polish_product_answer(_response()).answer == RULE
        monkeypatch.setenv("FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS", "true")
        general = ChatResponse.model_construct(answer="일반 안내", answer_source="rules", per_profile_results=[], current_product=None)
        assert answer_polish.polish_product_answer(general).answer == "일반 안내"
    finally:
        llm.set_llm_client(None)
