"""Wording-only polish of rule and backend answers before they are sent.

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

_PROMPT = """아래는 식품 알레르기 안내 챗봇이 규칙으로 만든 답변이에요. 휴대폰 채팅 화면에서 한눈에 읽히도록 자연스럽고 간결한 해요체로 다듬어 주세요. 같은 말이 반복되면 한 번만 쓰세요.

지킬 것:
- 판정(피해야 함 / 포장지 확인 필요 / 충돌을 찾지 못함), 제품명, 사람 이름, 성분·알레르기 이름, 숫자와 단위는 원문 그대로 쓰세요.
- 원문에 없는 정보, 이유, 건강 효과, 섭취 권유를 덧붙이지 마세요.
- 원문에 없는 숫자를 계산해서 넣지 마세요(환산·합계·비율 포함).
- '안전해요', '먹어도 돼요'처럼 원문보다 안심시키는 표현을 쓰지 마세요.
- 원문에 사람 이름이 여러 명 있으면 사람마다 따로 한 문장 이상 남기고 'OO 님'으로 부르세요. 원문에 이름이 없으면 이름을 쓰지 마세요.
- '판정됐어요' 같은 딱딱한 말 대신 '피하시는 게 좋아요', '포장지를 확인해 주세요'처럼 직접 권하는 말로 쓰세요.
- '확인 필요예요', '포장지 확인 필요해요'처럼 명사로 끝내지 말고 '포장지를 확인해 주세요'처럼 자연스러운 문장으로 끝내세요.
- 원문에 따옴표로 든 예시 문장('…처럼 물어봐 주세요')은 그대로 두세요.

읽기 쉽게 쓰는 법:
- 첫 문단에는 결론(판정이나 질문에 대한 답)을 1~2문장으로 쓰세요.
- 내용이 여러 가지면 문단을 나누고 문단 사이에 빈 줄을 넣으세요. 한 문단은 2문장 이하로 쓰세요.
- 사람이 여러 명이면 사람마다 문단을 나누고 'OO 님,'으로 시작하세요.
- 영양 수치나 제품별 수치처럼 값을 3개 이상 나열할 때는 한 줄에 하나씩 '• 열량: 160kcal'처럼 쓰세요.
- '•' 말고 다른 목록 기호(-, *, 1.), 마크다운(**굵게**, # 제목), 이모지는 쓰지 마세요.
- 확인 안내(포장지를 확인해 주세요 등)는 마지막 문단에 한 번만 쓰세요.

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
    terms += [field(item, "product_name") for item in (response.comparison_products or [])]
    # Names matter only to tell several people apart; one person may go unnamed.
    names = [name for name in (field(item, "profile_name") for item in (response.per_profile_results or []))
             if name and name in original]
    if len(set(names)) >= 2:
        terms += names
    # Quoted example questions ('…'처럼) are sent as typed; other quotes may be reworded.
    terms += re.findall(r"'([^']{2,60})'\s*처럼", original)
    for match in re.finditer(r"충돌 항목:\s*([^.\n]+)", original):
        terms += [part.strip() for part in match[1].split(",") if part.strip()]
    terms += [name for name in ALLERGEN_CANONICAL_ALIASES if len(name) >= 2 and name in original]
    return list(dict.fromkeys(term for term in terms if term))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text)}


def draft_keeps_facts(original: str, draft: str, response: ChatResponse) -> bool:
    return _rejection_reason(original, draft, response) is None


def _rejection_reason(original: str, draft: str, response: ChatResponse) -> str | None:
    """Why a draft must not replace the rule text, or None when it may."""
    from app.services.hybrid import (
        _contains_reassuring_conclusion, _has_unsupported_health_terms, _has_unsupported_ingredient_terms,
    )
    # Line breaks and '•' bullets are layout, not content; they do not count toward length.
    body = re.sub(r"\s+|•", "", draft)
    if not draft or not (0.4 * len(re.sub(r"\s+", "", original)) <= len(body) <= 1.3 * len(original) + 40):
        return "length"
    # Only '•' bullets are allowed; other list markers and markdown are not.
    list_marker = r"^\s*(?:[-*#]|\d+[.)])\s"
    if re.search(list_marker, draft, re.M) and not re.search(list_marker, original, re.M):
        return "list_marker"
    if "**" in draft or "__" in draft:
        return "markdown"
    missing = [term for term in _required_terms(original, response) if term not in draft]
    if missing:
        return f"required_term:{missing[0][:30]}"
    if _numbers(draft) != _numbers(original):
        return "numbers"
    # Each verdict cue of the rule text must survive.
    for cue, pattern in (("피해", r"피(?:해|하)"), ("포장지", r"포장지"), ("정보 없음", r"정보\s*없음"),
                         ("질환", r"질환"), ("주의", r"주의")):
        if cue in original and not re.search(pattern, draft):
            return "verdict_cue"
    lowered_original = original.lower()
    if _has_unsupported_ingredient_terms(draft, lowered_original) or _has_unsupported_health_terms(draft, original):
        return "unsupported_terms"
    if _contains_reassuring_conclusion(draft) and not _contains_reassuring_conclusion(original):
        return "reassuring"
    return None


def _eligible(response: ChatResponse) -> bool:
    # RAG answers are polished too, so every reply reads in the same 해요체.
    # Short rule replies are fixed prompts; a short RAG answer still needs the same tone.
    if response.answer_source not in {"backend", "rules", "rag"}:
        return False
    if len(response.answer or "") < (10 if response.answer_source == "rag" else 40):
        return False
    # Emergency guidance is fixed and must not wait; candidate prompts are UI text.
    if response.task_type == "safety_triage" or response.intent == "emergency_safety":
        return False
    return not response.product_candidates


def polish_answer(response: ChatResponse) -> ChatResponse:
    """Rephrase a rule or backend answer; keep the rule text on any doubt."""
    if not _enabled() or not _eligible(response):
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
        reason = _rejection_reason(original, draft, response)
        if reason is not None:
            logger.warning("Answer polish kept the rule text: reason=%s intent=%s", reason, response.intent)
            return response
        # At most one blank line between paragraphs.
        draft = re.sub(r"\n\s*\n\s*(?:\n\s*)*", "\n\n", draft)
        return response.model_copy(update={"answer": draft})
    except Exception as exc:
        logger.warning("Product answer polish check failed: error_type=%s", type(exc).__name__)
        return response


# Earlier name, kept for callers and tests.
polish_product_answer = polish_answer
