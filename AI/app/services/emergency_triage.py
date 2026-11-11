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
    r"(?:지금|현재|방금|아까|갑자기|먹고|먹었|마시고|마셨|"
    r"(?:먹은|마신)\s*(?:직후|뒤|후)|들이켰|오늘|섭취\s*후|섭취하고|섭취한\s*(?:뒤|후))"
)
_PAST_OR_HYPOTHETICAL_PATTERN = re.compile(
    r"(?:예전|과거|어제|그제|지난밤|지난번|당시|그때|작년|지난달|지난\s*주|어릴\s*때|"
    r"만약|가정|증상은|증상이\s*뭐|어떻게\s*대처)"
)
_ALLERGY_CONTEXT_PATTERN = re.compile(
    r"(?:알레르기|알러지|아나필락시스|땅콩|견과|호두|아몬드|캐슈|피스타치오|잣|우유|달걀|계란|"
    r"새우|꽃게|대게|갑각류|대두|메밀|밀가루|복숭아|토마토|조개|굴|전복|홍합|오징어|고등어|"
    r"닭고기|돼지고기|소고기|쇠고기|참깨|키위|"
    # One-syllable foods only as words: '어떻게'·'완두콩' are not 게·콩.
    r"(?<![가-힣])(?:게|콩|밀)(?=\s|[을를이가도랑와과은는만]|먹|$))"
)
_SEVERE_PATTERN = re.compile(
    r"(?:숨(?:이|을)?\s*(?:잘\s*)?(?:안|못)\s*쉬|숨(?:이)?\s*(?:막혀|막히|막힌|막혔|막힘)|"
    r"숨(?:을)?\s*쉴\s*수(?:가|도)?\s*없|호흡(?:이|을)?\s*(?:안\s*(?:돼|되)|못\s*하)|쌕쌕|"
    r"(?:축|축축)\s*(?:늘어|처지|처져)|늘어져\s*(?:있|서)|반응(?:이|을)?\s*(?:없|안\s*해)|"
    r"숨(?:을)?\s*쉬(?:기|기가|는\s*게|는\s*것이)?\s*(?:힘들|어렵|어려)|숨(?:이)?\s*(?:차|찹|찬)|"
    r"호흡(?:이|가)?\s*(?:힘들|어렵|어려|가빠|가쁘)|호흡곤란|"
    r"목(?:이|가)?\s*(?:(?:꽉|점점|많이|갑자기|너무|엄청|퉁퉁|심하게)\s*)?(?:붓|부어|부었|부은|조여|조이|조인|막혀|막히|막힌)|"
    r"혀(?:가|도)?\s*(?:(?:점점|많이|갑자기|너무|엄청|퉁퉁|심하게)\s*)?(?:붓|부어|부었|부은)|의식(?:이|가)?\s*(?:흐리|없)|"
    r"쓰러|실신|기절|입술(?:이|가)?\s*(?:(?:너무|엄청|퉁퉁|많이|갑자기)\s*)?(?:붓|부어|부었|부은|파래|푸르)|"
    r"침(?:을|도)?\s*(?:못|안)\s*삼키)"
)
# Hives, rash, itching, swelling or stomach symptoms after eating: not routed
# as an emergency, but never left unanswered either.
_MILD_REACTION_PATTERN = re.compile(
    r"(?:두드러기|발진|가려|간지러|붉게|빨갛게|울긋불긋|(?:눈|얼굴|입|손|몸)(?:이|가)?\s*(?:붓|부어|부었|부은)|"
    r"토했|토해|토하|구토|토할|복통|배(?:가)?\s*(?:아파|아프)|설사)"
)
_EXPOSURE_PATTERN = re.compile(
    r"(?:먹고|먹었|마시고|마셨|들이켰|(?:먹은|마신)\s*(?:직후|뒤|후)|섭취\s*후|"
    r"섭취하고|섭취한\s*(?:뒤|후))"
)
_FOOD_EXPOSURE_PATTERN = re.compile(
    r"(?:음식|식품|간식|과자|식사|음료|요리|제품|생선|고기|두부|밥).{0,12}(?:먹|마시|마신|마셨|섭취)|"
    r"(?:먹은|마신)\s*(?:음식|식품|간식|과자|식사|음료|요리|제품)"
)


# Airway or consciousness signs reported now are routed even without a named
# food: in this app the likely cause is something just eaten, and 119 guidance
# is never harmful. '숨이 차' alone (exercise) is excluded.
_AIRWAY_PATTERN = re.compile(
    r"숨(?:이)?\s*(?:막혀|막혔)|숨(?:을)?\s*쉴\s*수(?:가|도)?\s*없|숨(?:이|을)?\s*(?:잘\s*)?(?:안|못)\s*쉬|"
    # '목이 붓고 두드러기가 났어': swelling in the throat or tongue in any form.
    r"호흡곤란|목(?:이|가|구멍이)?\s*(?:(?:꽉|점점|많이|갑자기|너무|엄청|퉁퉁|심하게)\s*)?(?:붓|부어|부었|부은|조여|조이|막혀|막히)|"
    r"혀(?:가|도)?\s*(?:(?:점점|많이|갑자기|너무|엄청|퉁퉁|심하게)\s*)?(?:붓|부어|부었|부은)|의식(?:이|가)?\s*없|쓰러졌|실신|기절"
)
_PRESENT_REPORT_PATTERN = re.compile(r"(?:어요|아요|해요|워요|요|어|아|다|차|라|와|져|나|도와\s*줘|도와\s*주세요|살려\s*줘|살려\s*주세요)\s*[.!?…ㅠㅜ]*\s*$")


def _symptom_clauses(message):
    """Scope time/negation to a symptom clause, not to the whole message.

    Explicit current-time markers can start a new report after past history.
    Conditional questions remain conditional even when they contain '지금'.
    Food exposure can be supplied by the preceding clause.
    """
    for sentence in re.split(r"[.!?;\n]+|(?:하지만|그런데|근데)\s*", message):
        if re.search(r"만약|가정|(?:으면|다면|일\s*때).*(?:어떻게|대처|해야|하면)", sentence):
            continue
        for clause in re.split(r"(?<![가-힣])(?=지금|현재|방금|오늘)", sentence):
            if not _PAST_OR_HYPOTHETICAL_PATTERN.search(clause):
                yield clause


def _affirmed_severe_symptom(text, pattern=_SEVERE_PATTERN):
    for match in pattern.finditer(text):
        # Negation belongs to this symptom only. Another affirmed symptom in
        # the same message must still take the emergency path.
        suffix = text[match.end():]
        # Check each occurrence: hypothetical/quoted symptoms must not hide a
        # separate current report, or become one themselves.
        if re.match(r"^[가-힣]{0,6}(?:면|다면|는다면)(?=\s|[?？])", suffix):
            continue
        if re.match(r"^[가-힣]{0,6}(?:다는|라는)\s*(?:문장|표현|예문|말).*(?:뜻|의미|설명|번역|해석)", suffix):
            continue
        if re.match(r"^[가-힣]{0,6}\s*적(?:은|이|도)?\s*(?:있|없)", suffix):
            continue
        # Negated absence is an affirmed symptom ('없는 건 아니에요').
        if re.match(r"^(?:은|는|이|가|도)?\s*없(?:는|다고)\s*(?:건|것은|것이|말은)?\s*(?:아니|아닌)", suffix):
            return True
        if re.match(r"^[가-힣]{0,6}\s*(?:(?:건|것은|것이|게|것도)\s*(?:아니|아닌)|지\s*않|적(?:은|이|도)?\s*없)", suffix):
            continue
        if re.match(r"^(?:은|는|이|가|도)?\s*(?:(?:전혀|아직|별로|하나도|조금도|거의)\s*)?(?:없|아니|아닌)", suffix):
            continue
        # '두드러기는 안 났어' denies the symptom ('안 없어져' still reports it).
        if re.match(r"^[가-힣]{0,2}\s*(?:(?:전혀|아직|하나도)\s*)?안\s*(?:났|나|생겼|생기|올라|일어)", suffix):
            continue
        return True
    return False


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
    has_food_context: bool = False,
    pattern=_SEVERE_PATTERN,
) -> bool:
    text = " ".join(message.strip().split())
    if not text:
        return False
    # A quotation being translated/explained is not a reported symptom.
    # Remove that quotation only; a separate current report still has priority.
    text = re.sub(r'''["“‘'][^"“”‘’'\n]{0,200}["”’']\s*(?:라는|이란|이라는)?\s*(?:예문|문장|표현)[^.!?\n]{0,80}?(?:번역|해석|설명)[^.!?]*''', "", text)
    if (pattern is _SEVERE_PATTERN and not _PAST_OR_HYPOTHETICAL_PATTERN.search(text)
            and _PRESENT_REPORT_PATTERN.search(text)
            and any(_affirmed_severe_symptom(clause, _AIRWAY_PATTERN) for clause in _symptom_clauses(text))):
        return True
    if not _CURRENT_PATTERN.search(text) or not any(_affirmed_severe_symptom(clause, pattern) for clause in _symptom_clauses(text)):
        return False
    has_allergy_context = (
        bool(_ALLERGY_CONTEXT_PATTERN.search(text))
        or bool(_FOOD_EXPOSURE_PATTERN.search(text))
        or (
            bool(_EXPOSURE_PATTERN.search(text))
            # A reported ingestion immediately followed by a severe symptom
            # does not require the food to occur in a fixed allergen list.
            # This is emergency routing, never an allergy diagnosis.
            and (has_food_context or _profile_allergen_mentioned(text, profiles)
                 or re.search(r"[가-힣A-Za-z]+(?:을|를)?\s*(?:먹|마셨|마신|마시|들이켰|섭취)", text))
        )
    )
    return has_allergy_context


def build_emergency_triage_response(
    message: str,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    has_food_context: bool = False,
) -> Optional[ChatResponse]:
    """Return fixed guidance only for a clear, current allergic emergency."""

    effective_profiles = list(profiles or [])
    if not _is_current_severe_allergic_reaction(message, effective_profiles, has_food_context):
        if _is_current_severe_allergic_reaction(message, effective_profiles, has_food_context, _MILD_REACTION_PATTERN):
            return _allergic_reaction_guidance(effective_profiles)
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


def _allergic_reaction_guidance(profiles: Sequence[Profile]) -> ChatResponse:
    """Fixed guidance for a mild reaction reported right after eating; no diagnosis."""
    headline = "음식을 먹은 뒤 나타난 알레르기 반응일 수 있어요."
    actions = [
        "숨쉬기 어렵거나 입술·혀·목이 붓거나 어지러우면 즉시 119에 연락하세요.",
        "두드러기나 붓기가 온몸으로 퍼지거나 계속되면 바로 진료를 받으세요.",
        "증상이 나타난 음식은 더 먹지 말고, 먹은 제품의 포장지를 진료 때 보여 주세요.",
        "이 챗봇은 증상을 진단하거나 치료를 안내할 수 없어요.",
    ]
    provenance = response_provenance(["safety.allergic_reaction.routing.v1", "emergency.anaphylaxis.action.v1"])
    names = list(dict.fromkeys(profile.profile_name for profile in profiles))
    return ChatResponse(
        task_type="safety_triage", answer_source="rules", pipeline_stage="safety_triage", intent="emergency_safety",
        answer=f"{headline}\n" + "\n".join(actions),
        risk_level="high", overall_decision="avoid", allergy_status="unknown", assessment_status="incomplete",
        reasons=["음식 섭취 직후의 알레르기 증상 표현 감지", "진단이 아닌 고정 안전 안내"],
        profile_name=names[0] if names else None, active_profile_names=names,
        safety_guidance=SafetyGuidance(urgency="urgent", headline=headline, actions=actions, emergency_contact="119"),
        sources=provenance["sources"], source_details=provenance["source_details"],
        policy_version=provenance["policy_version"], applied_rule_ids=provenance["applied_rule_ids"],
        evidence=provenance["evidence"], provenance_status=provenance["provenance_status"],
    )
