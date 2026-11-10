"""Wording-only polish of backend product answers.

The verdict, product, people, allergens and numbers are fixed by the rule
answer. OpenAI may only rephrase it; a draft that drops or adds any of those,
or sounds more reassuring than the rule text, is discarded and the rule text
is sent unchanged.
"""

from __future__ import annotations

import logging
import os
import re

from app.core.models import ChatResponse

logger = logging.getLogger(__name__)

POLISH_ENV = "FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS"

_PROMPT = """아래는 식품 알레르기 안내 챗봇이 규칙으로 만든 답변이에요. 사용자에게 보여 줄 수 있게 자연스럽고 간결한 해요체로 다듬어 주세요.

지킬 것:
- 판정(피해야 함 / 포장지 확인 필요 / 충돌을 찾지 못함), 제품명, 사람 이름, 성분·알레르기 이름, 숫자와 단위는 원문 그대로 쓰세요.
- 원문에 없는 정보, 이유, 건강 효과, 섭취 권유를 덧붙이지 마세요.
- '안전해요', '먹어도 돼요'처럼 원문보다 안심시키는 표현을 쓰지 마세요.
- 사람이 여러 명이면 사람마다 따로 한 문장 이상 남기세요.
- 사람 이름은 '웹테스트 님'처럼 부르고, '판정됐어요' 같은 딱딱한 말 대신 '피하시는 게 좋아요', '포장지를 확인해 주세요'처럼 직접 권하는 말로 쓰세요.
- 마크다운, 목록 기호, 이모지, 제목 없이 문장으로만 쓰세요.

원문:
\"\"\"
{answer}
\"\"\"

다듬은 답변만 출력하세요."""


def _enabled() -> bool:
    return (os.getenv(POLISH_ENV) or "true").strip().lower() not in {"0", "false", "no", "off"}


def _required_terms(original: str, response: ChatResponse) -> list[str]:
    from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
    def field(obj, name):
        # Response parts are models in the API and dicts in some callers.
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    terms = []
    if response.current_product is not None and field(response.current_product, "product_name"):
        terms.append(field(response.current_product, "product_name"))
    terms += [field(item, "profile_name") for item in (response.per_profile_results or [])]
    for match in re.finditer(r"충돌 항목:\s*([^.\n]+)", original):
        terms += [part.strip() for part in match[1].split(",") if part.strip()]
    terms += [name for name in ALLERGEN_CANONICAL_ALIASES if len(name) >= 2 and name in original]
    return list(dict.fromkeys(term for term in terms if term))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text)}


def draft_keeps_facts(original: str, draft: str, response: ChatResponse) -> bool:
    from app.services.hybrid import (
        _contains_reassuring_conclusion, _has_unsupported_health_terms, _has_unsupported_ingredient_terms,
    )
    if not draft or not (0.4 * len(original) <= len(draft) <= 1.3 * len(original) + 40):
        return False
    if re.search(r"^\s*(?:[-*#•]|\d+[.)])\s", draft, re.M):
        return False
    if any(term not in draft for term in _required_terms(original, response)):
        return False
    if _numbers(draft) != _numbers(original):
        return False
    # Each verdict cue of the rule text must survive.
    for cue, pattern in (("피해", r"피(?:해|하)"), ("포장지", r"포장지"), ("정보 없음", r"정보\s*없음"),
                         ("질환", r"질환"), ("주의", r"주의")):
        if cue in original and not re.search(pattern, draft):
            return False
    lowered_original = original.lower()
    if _has_unsupported_ingredient_terms(draft, lowered_original) or _has_unsupported_health_terms(draft, original):
        return False
    if _contains_reassuring_conclusion(draft) and not _contains_reassuring_conclusion(original):
        return False
    return True


def polish_product_answer(response: ChatResponse) -> ChatResponse:
    """Rephrase a backend product answer; keep the rule text on any doubt."""
    if not _enabled() or response.answer_source != "backend" or not response.answer:
        return response
    from app.infrastructure.llm import generate_text, is_llm_enabled
    from app.infrastructure.llm_trace import llm_purpose
    if not is_llm_enabled():
        return response
    original = response.answer
    try:
        with llm_purpose("product_answer_polish"):
            draft = generate_text(_PROMPT.format(answer=original), reasoning_effort="low")
    except Exception as exc:  # the rule answer must never be lost
        logger.warning("Product answer polish failed: error_type=%s", type(exc).__name__)
        return response
    draft = (draft or "").strip()
    try:
        if not draft_keeps_facts(original, draft, response):
            return response
        return response.model_copy(update={"answer": draft})
    except Exception as exc:
        logger.warning("Product answer polish check failed: error_type=%s", type(exc).__name__)
        return response
