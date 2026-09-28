"""Minimal deterministic boundary for a current severe allergic reaction.

The chatbot is not a general emergency or medical service. This module exists
only so a clearly described, current food-allergy emergency is never delayed by
product lookup or an external model.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from app.core.models import ChatResponse, Profile, SafetyGuidance
from app.core.policy_manifest import response_provenance
from app.services.response_helpers import aliases_for_allergen


KDCA_ANAPHYLAXIS_URL = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
    "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6684"
)


_CURRENT_PATTERN = re.compile(
    r"(?:지금|현재|방금|아까|갑자기|먹고|먹었는데|"
    r"먹은\s*(?:뒤|후)|섭취\s*후|섭취하고|섭취한\s*(?:뒤|후))"
)
_PAST_OR_HYPOTHETICAL_PATTERN = re.compile(
    r"(?:예전|과거|작년|지난달|지난\s*주|어릴\s*때|"
    r"만약|가정|증상은|증상이\s*뭐|어떻게\s*대처)"
)
_ALLERGY_CONTEXT_PATTERN = re.compile(
    r"(?:알레르기|알러지|아나필락시스|땅콩|견과|우유|달걀|계란|"
    r"새우|게|갑각류|대두|콩|밀|메밀|복숭아|조개)"
)
_SEVERE_PATTERN = re.compile(
    r"(?:숨(?:이|을)?\s*(?:잘\s*)?(?:안|못)\s*쉬|"
    r"숨\s*쉬(?:기|기가)?\s*(?:힘들|어렵)|"
    r"호흡(?:이|가)?\s*(?:힘들|어렵)|호흡곤란|"
    r"목(?:이|가)?\s*(?:붓|부어|조여|막혀)|"
    r"혀(?:가|도)?\s*(?:붓|부어)|의식(?:이|가)?\s*(?:흐리|없)|"
    r"쓰러|실신|기절|입술(?:이|가)?\s*(?:붓|부어|파래|푸르)|"
    r"침(?:을|도)?\s*(?:못|안)\s*삼키)"
)
_EXPOSURE_PATTERN = re.compile(
    r"(?:먹고|먹었는데|먹은\s*(?:뒤|후)|섭취\s*후|"
    r"섭취하고|섭취한\s*(?:뒤|후))"
)
_FOOD_EXPOSURE_PATTERN = re.compile(
    r"(?:음식|식품|간식|요리|제품).{0,12}(?:먹|섭취)"
)


def _profile_allergen_mentioned(message: str, profiles: Sequence[Profile]) -> bool:
    lowered = message.lower()
    for profile in profiles:
        for allergen in profile.resolved_allergies():
            if any(alias.lower() in lowered for alias in aliases_for_allergen(allergen)):
                return True
    return False


def _is_current_severe_allergic_reaction(
    message: str,
    profiles: Sequence[Profile],
) -> bool:
    text = " ".join(message.strip().split())
    if not text or _PAST_OR_HYPOTHETICAL_PATTERN.search(text):
        return False
    if not _CURRENT_PATTERN.search(text) or not _SEVERE_PATTERN.search(text):
        return False
    has_allergy_context = (
        bool(_ALLERGY_CONTEXT_PATTERN.search(text))
        or bool(_FOOD_EXPOSURE_PATTERN.search(text))
        or (
            bool(_EXPOSURE_PATTERN.search(text))
            and _profile_allergen_mentioned(text, profiles)
        )
    )
    return has_allergy_context


def build_emergency_triage_response(
    message: str,
    *,
    profiles: Optional[Sequence[Profile]] = None,
) -> Optional[ChatResponse]:
    """Return fixed guidance only for a clear, current allergic emergency."""

    effective_profiles = list(profiles or [])
    if not _is_current_severe_allergic_reaction(message, effective_profiles):
        return None

    headline = "심한 알레르기 반응일 수 있어 즉시 응급 대응이 필요해요."
    actions = [
        "즉시 119에 연락하고 주변 사람에게 도움을 요청하세요.",
        "처방받은 에피네프린 자동주사기가 있다면 처방받은 방법대로 바로 사용하세요.",
        "혼자 있거나 직접 운전하지 말고, 호흡과 의식 상태가 달라지면 119에 알리세요.",
        "증상이 잠시 좋아져도 응급 평가를 미루지 마세요.",
    ]
    rule_ids = [
        "emergency.anaphylaxis.routing.v1",
        "emergency.anaphylaxis.action.v1",
    ]
    provenance = response_provenance(rule_ids)
    profile_names = list(dict.fromkeys(
        profile.profile_name for profile in effective_profiles
    ))
    return ChatResponse(
        task_type="safety_triage",
        answer_source="rules",
        pipeline_stage="safety_triage",
        intent="emergency_safety",
        answer=f"{headline}\n" + "\n".join(actions),
        risk_level="high",
        overall_decision="avoid",
        allergy_status="conflict",
        assessment_status="incomplete",
        reasons=[
            "현재성이 있는 중증 알레르기 반응 표현 감지",
            "진단이 아닌 고정 응급 안내",
        ],
        profile_name=profile_names[0] if profile_names else None,
        active_profile_names=profile_names,
        safety_guidance=SafetyGuidance(
            urgency="emergency",
            headline=headline,
            actions=actions,
            emergency_contact="119",
        ),
        sources=provenance["sources"],
        source_details=provenance["source_details"],
        policy_version=provenance["policy_version"],
        applied_rule_ids=provenance["applied_rule_ids"],
        evidence=provenance["evidence"],
        provenance_status=provenance["provenance_status"],
    )
