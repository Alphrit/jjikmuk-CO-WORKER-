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
    r"침(?:을|도)?\s*(?:못|안)\s*삼키|어지러|어지럽)"
)
# Hives, rash, itching, swelling or stomach symptoms after eating: not routed
# as an emergency, but never left unanswered either.
_MILD_REACTION_PATTERN = re.compile(
    r"(?:두드러기|발진|가려|가렵|간지러|간지럽|붉게|빨갛게|빨개|울긋불긋|(?:눈|얼굴|입|손|몸)(?:이|가)?\s*(?:붓|부어|부었|부은)|"
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
    r"숨(?:이)?\s*(?:막혀|막혔|막히|막힘)|숨막|숨(?:을)?\s*쉴\s*수(?:가|도)?\s*없|숨(?:이|을)?\s*(?:잘\s*)?(?:안|못)\s*쉬|"
    r"숨(?:을)?\s*쉬(?:기|기가|는\s*게)?\s*(?:힘들|어렵|어려)|호흡\s*(?:이\s*)?곤란|"
    # '목이 붓고 두드러기가 났어', '목이 따끔하고 부었어', '목안이 부었어', typos '부엇', '부음'.
    r"목(?:이|가|구멍이|\s*안(?:쪽)?(?:이|에)?)?[^.?!,\n]{0,8}?(?:붓|부어|부었|부엇|부은|부음|조여|조이|막혀|막혔|막히|막힘)|"
    r"혀(?:가|도)?[^.?!,\n]{0,8}?(?:붓|부어|부었|부엇|부은)|의식(?:이|가)?\s*없|쓰러졌|실신|기절|"
    # '아나필락시스 온 것 같아', '에피펜 맞아야 해?', '119 불러야 해?'
    r"아나필락시스\s*(?:가|이)?\s*(?:온|왔|같아|나타|증상이\s*(?:나|있|생))|"
    r"(?:에피펜|에피네프린|119|응급실)[^.?!\n]{0,8}(?:맞아야|놔야|불러야|가야|갈까|가요)"
)
# Colds, exercise and exaggeration ('기절할 만큼 맛있어') are not allergic reactions.
_NOT_A_REACTION = re.compile(r"감기|목감기|편도|인후염|맛있|만큼|정도로|줄\s*알았|매워|매운|노래|운동|뛰었|달리|계단|등산|영화|드라마|장면|분위기|긴장|더위|더워|추천")


def _current_airway_report(clause):
    """'숨이 막혀', '목이 부었는데 어떻게 해?': a sign happening now, not '먹으면 목이 부어요'."""
    if _NOT_A_REACTION.search(clause):
        return False
    match = _AIRWAY_PATTERN.search(clause)
    if not match:
        return False
    if re.search(r"(?:안|않)\s*(?:붓|부|막|조)", match[0]):
        return False  # '목이 안 부었어요' ('숨이 안 쉬어져' stays a symptom)
    if re.search(r"(?:으)?면(?:\s|$)|(?:을|ㄹ)\s*때", clause[:match.start()]):
        return False  # a habit or condition: '땅콩 먹으면 목 부어'
    if re.match(r"[가-힣]{0,6}(?:면|다면|으면)(?=\s|[?？]|$)", clause[match.end():]):
        return False  # '목이 부으면 어떻게 해?'
    return _affirmed_severe_symptom(clause, _AIRWAY_PATTERN)


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
        if re.match(r"^[가-힣]{0,6}?\s*(?:(?:건|것은|것이|게|것도)\s*(?:아니|아닌)|(?:지|진|지는|지도)\s*않|적(?:은|이|도)?\s*없)", suffix):
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
    if pattern is _SEVERE_PATTERN and any(_current_airway_report(clause) for clause in _symptom_clauses(text)):
        return True
    if (pattern is _SEVERE_PATTERN and not _NOT_A_REACTION.search(text)
            and re.search(r"두드러기|발진|가렵|가려|간지러|간지럽", text) and re.search(r"숨|호흡", text)
            and any(_affirmed_severe_symptom(clause) for clause in _symptom_clauses(text))):
        return True  # hives together with breathing trouble
    if not _CURRENT_PATTERN.search(text) or not any(_affirmed_severe_symptom(clause, pattern) for clause in _symptom_clauses(text)):
        return False
    has_allergy_context = (
        # A selected product is the food just eaten ('지금 숨이 차요' on its page).
        (has_food_context and not _NOT_A_REACTION.search(text))
        or bool(_ALLERGY_CONTEXT_PATTERN.search(text))
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


_SYMPTOM_WORD_PATTERN = re.compile(
    r"(?:목|혀|입술|얼굴|눈)(?:이|가|구멍이)?\s*(?:붓|부어|부으|부은|부었)|숨(?:이|을)?\s*(?:막|차|쉬기\s*(?:힘|어려)|안\s*쉬어지|못\s*쉬)|쓰러지|"
    r"호흡\s*곤란|두드러기|아나필락시스|알레르기\s*반응"
)
_SYMPTOM_QUESTION_PATTERN = re.compile(
    r"(?:면|다면|으면|할\s*때|일\s*때|생기면|나면|났을\s*때|왔을\s*때).{0,15}(?:어떻게|어떡|뭘|무엇|해야|대처|대응|응급)|"
    r"아나필락시스(?:가|는|란|이)?\s*(?:뭐|무엇|어떤)"
)


def symptom_question_guidance(message: str, profiles: Optional[Sequence[Profile]] = None) -> Optional[ChatResponse]:
    """'목이 부으면 어떻게 해야 해?': what to do, as fixed first-aid guidance (not a report)."""
    text = " ".join((message or "").split())
    if not (_SYMPTOM_QUESTION_PATTERN.search(text) and _SYMPTOM_WORD_PATTERN.search(text)):
        return None
    if re.search(r"먹어도|마셔도|들어\s*있|무엇이\s*들어|뭘\s*먹어야|대신|추천", text):
        return None  # a food or product question that mentions a symptom keeps its own route
    headline = "목이나 혀가 붓거나 숨쉬기 힘든 증상은 심한 알레르기 반응(아나필락시스)의 신호일 수 있어요."
    actions = [
        "이런 증상이 생기면 바로 119에 연락하세요.",
        "처방받은 에피네프린 자동주사기가 있다면 처방받은 방법대로 바로 사용하세요.",
        "증상이 잠시 좋아져도 응급 진료를 미루지 마세요.",
        "두드러기나 가려움만 있어도 온몸으로 퍼지거나 계속되면 진료를 받으세요.",
    ]
    provenance = response_provenance(["emergency.anaphylaxis.action.v1"])
    names = list(dict.fromkeys(profile.profile_name for profile in profiles or []))
    return ChatResponse(
        task_type="safety_triage", answer_source="rules", pipeline_stage="safety_triage", intent="general_allergy_guide",
        answer=f"{headline}\n\n" + "\n".join(actions),
        risk_level="unknown", overall_decision="check_required", allergy_status="unknown", assessment_status="complete",
        reasons=["알레르기 응급 증상 대처 질문", "진단이 아닌 고정 안전 안내"],
        profile_name=names[0] if names else None, active_profile_names=names,
        safety_guidance=SafetyGuidance(urgency="routine", headline=headline, actions=actions, emergency_contact="119"),
        sources=provenance["sources"], source_details=provenance["source_details"],
        policy_version=provenance["policy_version"], applied_rule_ids=provenance["applied_rule_ids"],
        evidence=provenance["evidence"], provenance_status=provenance["provenance_status"],
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
