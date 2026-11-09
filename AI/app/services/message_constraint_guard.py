"""Turn-local allergy handling for the allergy-first food chatbot.

An allergen stated in the current question may affect that answer without
silently changing a saved profile. With several active profiles, the chatbot
asks whose allergy it is unless exactly one profile is named.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.models import ChatResponse, Profile
from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
from app.intent.common import TARGET_REFERENCE_KEYWORDS
from app.services.response_helpers import build_response, canonical_allergen_name
from app.services.condition_subject import resolve_condition_subject
from app.services.condition_language import NOUN_LIST_SEPARATOR, listed_food_names


_UNSPECIFIED_ALLERGY_STATEMENT = re.compile(
    r"(?:알레르기|알러지)(?:은|는|이|가|도)?\s*"
    r"(?:(?:좀|약간|여러\s*개)\s*)?"
    r"(?:있(?:어|고|는데|다|는지|을지도|습니다)|모르|불분명|의심|때문|인데)"
)
_SAVED_ALLERGY_REFERENCE_PATTERN = re.compile(
    r"(?:내|등록(?:된|한)?|저장(?:된|한)?|프로필(?:의)?)?\s*"
    r"(?:알레르기|알러지)(?:\s*(?:정보|목록))?\s*기준"
)
_HYPOTHETICAL_PERSON_PATTERN = re.compile(
    r"(?:알레르기|알러지)(?:가)?\s*있는\s*사람"
)


def _normalize(value: object) -> str:
    return "".join(str(value).strip().lower().split())


@dataclass(frozen=True)
class MessageAllergies:
    confirmed: frozenset[str] = frozenset()
    uncertain: frozenset[str] = frozenset()
    denied: frozenset[str] = frozenset()
    unspecified: bool = False
    references_saved: bool = False


def _message_allergens(message: str) -> MessageAllergies:
    if not re.search(r"알레르기|알러지", message):
        return MessageAllergies()
    aliases = {alias.lower(): canonical for canonical, values in ALLERGEN_CANONICAL_ALIASES.items()
               for alias in (canonical, *values)}
    # Preserve newly named, unsupported foods too. They are unresolved input,
    # not permission to fall back to the already registered allergies.
    # Known single words and unknown nouns use the same lexical branch. Having
    # both alternatives inside a repeated list causes exponential backtracking
    # on a long list that is not followed by an allergy assertion.
    multiword = [re.escape(a) for a in sorted(aliases, key=len, reverse=True) if " " in a]
    term = ("(?:" + "|".join(multiword)
            + r"|(?!(?:있|없|아니(?:야|에요|고|지만)|모르|먹어|먹고|심해|심한|고생)[가-힣]*(?![가-힣]))"
            r"[가-힣A-Za-z][가-힣A-Za-z0-9·-]{0,39})")
    pattern = rf"(?<![\w])({term}(?:{NOUN_LIST_SEPARATOR}{term})*)\s*(?:에\s*)?(?:알레르기|알러지)"
    mentions = list(re.finditer(pattern, message, flags=re.IGNORECASE))
    canonical_names: set[str] = set()
    uncertain: set[str] = set()
    denied: set[str] = set()
    concept_spans = []
    for index, match in enumerate(mentions):
        from app.intent.request_semantics import concept_allergy_mention
        if concept_allergy_mention(message, match.start(), match.end()):
            concept_spans.append((match.start(), match.end()))
            continue
        names = set()
        for name in listed_food_names(match[1], aliases):
            if name not in aliases:
                name = name.removesuffix("에")
            if any(re.fullmatch(re.escape(subject) + r"(?:은|는|이|가|도|의)?", name)
                   for subject in TARGET_REFERENCE_KEYWORDS):
                continue
            if name in {"내", "제", "나는", "저는", "나", "저", "아이는", "아이가",
                        "등록된", "등록한", "저장된", "저장한", "프로필", "프로필의",
                        "어떤", "무슨", "다른", "새로운", "새", "음식", "식품", "그리고", "제품"}:
                continue
            if re.fullmatch(r"(?:성분|원재료|원료|재료|영양\s*성분|영양\s*정보|영양)(?:과|와|이랑|랑|하고|및|의)?", name):
                # '성분과 알레르기 주의사항' lists label fields, not an allergen.
                continue
            names.add(aliases.get(name, name))
        if not names:
            continue
        end = mentions[index + 1].start() if index + 1 < len(mentions) else len(message)
        predicate = re.split(r"[.!?;,\n]", message[match.end():end], maxsplit=1)[0]
        person = re.match(
            r"\s*(?:(?:가|이)?\s*있는|(?:로|때문에)\s*고생(?:하는|하던)|"
            r"(?:를)?\s*(?:가지고|갖고|앓고)\s*있는)\s*(?:사람|환자)(.*)", predicate,
        )
        if person:
            if re.match(r"(?:인지|일지|인\s*것\s*같|(?:은|이)?\s*아닌지)", person[1]):
                uncertain.update(names)
                continue
            # A copula asserts the speaker/identified subject's condition;
            # '사람은/사람도/사람이 먹어도' describes a general group instead.
            if re.match(r"(?:이야|이에요|입니다|인데|이거든)", person[1]):
                punctuation = re.search(r"[.!?;,\n]", message[match.end():end])
                if punctuation and punctuation[0] == "?":
                    uncertain.update(names)
                    continue
                predicate = " 있어" + person[1]
            elif re.match(r"(?:은|이)?\s*아니", person[1]):
                predicate = " 아니야 " + person[1]
            else:
                continue
        assertion = re.match(
            r"\s*(?:은|는|이|가|도)?\s*(?:(?:좀|약간|매우|아주|정말)\s*)?"
            r"(?:있|없|아니|모르|의심|불분명|때문|인데|심[해하한]|야|예요|이에요|입니다)",
            predicate,
        )
        if not assertion:
            # Unknown personal wording is unresolved, never 'no allergy'.
            # Information requests without a subject are not personal claims.
            prefix = re.split(r"[.!?;,\n]", message[:match.start()])[-1]
            information = re.match(
                r"\s*(?:(?:의\s*)?(?:정보|증상|원인|뜻|설명|표시|문구|성분|차이|(?:주의|유의)\s*사항)|(?:가|는|란)?\s*(?:뭔지|무엇인지|뭐야|어떤\s*뜻)|에\s*대해|란(?:\s|$)|라는\s*(?:게|것)|"
                # '허니버터칩 알레르기 알려줘' asks for the label, not about the speaker.
                r"(?:을|를|좀)?\s*(?:알려|확인해|보여|설명해)|"
                r"유발\s*(?:물질|성분)\s*(?:의\s*)?(?:부재|없음|표시|정보)|"
                r"(?:유발\s*물질\s*)?(?:혼입|교차\s*(?:접촉|오염))\s*"
                r"(?:표시|가능성|확률|주의\s*문구|경로|관리|뜻|개념|의미|여부|과정|원인))",
                predicate,
            )
            # Comparing concepts or asking what a label implies does not assert
            # that the speaker has that allergy. Work per mention so an actual
            # personal statement elsewhere in the question is still retained.
            concept_tail = re.split(r"[.!?;,\n]", message[match.end():], maxsplit=1)[0]
            conceptual_comparison = re.match(
                r"\s*(?:와|과|하고)\s*.+(?:같은\s*뜻|같은\s*의미|같은\s*거|차이|다른\s*뜻|다르|달라)",
                concept_tail,
            ) and not re.search(r"(?:알레르기|알러지)(?:가|는|도)?\s*(?:있|없|모르|의심)", concept_tail)
            label_implication = re.match(
                r"\s*(?:에도|에|도|는|란|라는)\s*.*(?:뜻|의미)(?:이|야|인|에요|인가|일까|을|를|\s|$)",
                predicate,
            )
            information = information or conceptual_comparison or label_implication
            symptom = re.match(r"\s*(?:의\s*)?증상(?:이|은|도|을)?\s*(.*)", predicate)
            if symptom and re.match(r"(?:있|나타|생[기겼]|느껴|겪|보여|없)", symptom[1]):
                # A symptom report is a personal possibility, not a diagnosis
                # and not the general request '증상 알려줘'.
                uncertain.update(names)
                continue
            if information:
                # Supported and unsupported names follow the same rule. Merely
                # asking about an allergen must not create a personal allergy.
                continue
            personal_predicate = re.match(
                r"\s*(?:로|때문에)\s*(?:고생|고통|힘들|힘든|앓)|\s*(?:를)?\s*(?:앓|가지고|갖고)", predicate,
            )
            unsupported = names - ALLERGEN_CANONICAL_ALIASES.keys()
            if not information and (unsupported or personal_predicate or re.search(r"(?:^|\s)(?:나는|저는|내가|제가|난|전)\s*$", prefix)):
                # An unfamiliar predicate on a named unsupported allergy must
                # remain unresolved. A finite list of personal verbs cannot be
                # used as evidence that the user's condition is absent.
                uncertain.update(names)
                continue
            # Explicit information queries are not personal allergy claims.
            names.intersection_update(ALLERGEN_CANONICAL_ALIASES)
            if not names:
                continue
        if re.match(r"\s*(?:이|가)?\s*없는\s*(?:과자|제품|음식|간식|식품|메뉴|레시피)", predicate):
            # A request for allergen-free food is an exclusion, not a denial
            # of the user's condition ("새우 알레르기는 없어").
            canonical_names.update(names)
        elif re.search(
            r"모르|있는지|있을지도|의심|확실하지|"
            r"(?:없|아니)지(?:는|도)?\s*(?:않|못)|"
            r"없다고[^.!?;,]{0,24}(?:못|없|않)|"
            r"없는\s*(?:건|것은)\s*아니", predicate,
        ):
            uncertain.update(names)
        elif re.match(r"\s*(?:은|는|이|가|도)?\s*(?:전혀\s*)?(?:없|아니)", predicate):
            denied.update(names)
        elif re.match(r"\s*(?:이|가)?\s*있(?:어|나요)\s*\?", message[match.end():end]):
            uncertain.update(names)
        else:
            canonical_names.update(names)

    references_saved = bool(_SAVED_ALLERGY_REFERENCE_PATTERN.search(message))
    unspecified_text = _HYPOTHETICAL_PERSON_PATTERN.sub("", message)
    if concept_spans:
        unspecified_text = message
        for start, end in reversed(concept_spans):
            unspecified_text = unspecified_text[:start] + " " + unspecified_text[end:]
        unspecified_text = _HYPOTHETICAL_PERSON_PATTERN.sub("", unspecified_text)
    has_unspecified = bool(_UNSPECIFIED_ALLERGY_STATEMENT.search(unspecified_text)
                           and not (canonical_names or uncertain or denied) and not references_saved)
    # Contradictory statements about one allergen need clarification, too.
    # Certainty about a different allergen must never cancel this uncertainty.
    uncertain.update(canonical_names & denied)
    return MessageAllergies(
        confirmed=frozenset(canonical_names - uncertain),
        uncertain=frozenset(uncertain), denied=frozenset(denied - uncertain),
        unspecified=has_unspecified, references_saved=references_saved,
    )


@dataclass(frozen=True)
class TurnAllergyContext:
    profiles: list[Profile]
    added_allergens: tuple[str, ...] = ()
    target_ambiguous: bool = False


def apply_turn_local_allergies(
    message: str,
    profiles: Optional[Sequence[Profile]],
    known_profiles: Optional[Sequence[Profile]] = None,
    *, force_ambiguous_target: bool = False,
) -> TurnAllergyContext:
    """Apply explicit allergy text to exactly one temporary profile."""

    active_profiles = list(profiles or [])
    parsed = _message_allergens(message)
    allergens = parsed.confirmed
    if not allergens:
        return TurnAllergyContext(
            profiles=active_profiles,
            target_ambiguous=bool((parsed.unspecified or parsed.uncertain) and len(active_profiles) > 1),
        )

    target = resolve_condition_subject(message, active_profiles, known_profiles)
    if force_ambiguous_target or (target is None and (active_profiles or known_profiles)):
        return TurnAllergyContext(
            profiles=active_profiles, added_allergens=tuple(sorted(allergens)), target_ambiguous=True,
        )
    target_index = next((i for i, p in enumerate(active_profiles) if p is target), None)

    if target_index is None:
        return TurnAllergyContext(
            profiles=[Profile(profile_name="이번 질문", allergies=sorted(allergens & ALLERGEN_CANONICAL_ALIASES.keys()))],
            added_allergens=tuple(sorted(allergens)),
        )

    target = active_profiles[target_index]
    existing = target.resolved_allergies()
    existing_canonical = {
        canonical_allergen_name(value) or _normalize(value)
        for value in existing
    }
    added = [
        allergen
        for allergen in sorted(allergens)
        if allergen not in existing_canonical
    ]
    if not added:
        return TurnAllergyContext(profiles=active_profiles)

    # Unsupported names are carried as unresolved task conditions, not inserted
    # into the finite supported-allergen list or presented as a completed check.
    supported_added = [name for name in added if name in ALLERGEN_CANONICAL_ALIASES]
    if not supported_added:
        return TurnAllergyContext(profiles=active_profiles, added_allergens=tuple(added))
    payload = target.model_dump(mode="python")
    payload["allergies"] = [*existing, *supported_added]
    payload["user_allergies"] = []
    active_profiles[target_index] = Profile.model_validate(payload)
    return TurnAllergyContext(
        profiles=active_profiles,
        added_allergens=tuple(added),
    )


def build_message_constraint_guard_response(
    message: str,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    known_profiles: Optional[Sequence[Profile]] = None,
    force_ambiguous_target: bool = False,
) -> Optional[ChatResponse]:
    """Ask when an allergy's certainty or its active-profile owner is unknown."""

    active_profiles = list(profiles or [])
    parsed = _message_allergens(message)
    turn_context = apply_turn_local_allergies(
        message, active_profiles, known_profiles, force_ambiguous_target=force_ambiguous_target,
    )
    ambiguous_target = bool(parsed.confirmed and turn_context.target_ambiguous)
    unsupported = sorted(name for name in parsed.confirmed if name not in ALLERGEN_CANONICAL_ALIASES)
    unknown_allergy = bool(parsed.uncertain) or bool(
        parsed.unspecified
        and not parsed.references_saved
        and not any(profile.resolved_allergies() for profile in active_profiles)
    )
    if not ambiguous_target and not unknown_allergy and not unsupported:
        return None

    details, reasons = [], []
    if ambiguous_target:
        details.append("질문에 새로 적은 알레르기가 어느 프로필의 것인지 불분명해요.")
        reasons.append("새 알레르기의 대상 프로필 확인 필요")
    if unsupported:
        names = ', '.join(unsupported)
        details.append(f"말씀하신 {names} 알레르기는 현재 지원하는 성분 대조 범위 밖이라 자동으로 적합성을 확인할 수 없어요.")
        reasons.append(f"미지원 알레르기 성분 확인 필요: {names}")
    if parsed.uncertain:
        names = ', '.join(sorted(parsed.uncertain))
        details.append(f"{names} 알레르기가 있는지는 아직 확인이 필요해요. 확정된 다른 알레르기와 별도로 확인해야 해요.")
        reasons.append(f"{names} 알레르기 유무 확인 필요")
    elif unknown_allergy:
        details.append("어떤 알레르기인지 질문과 선택된 프로필에서 확인할 수 없어요.")
        reasons.append("알레르기 항목 확인 필요")

    clarification = (
        "제품의 실제 원재료와 해당 알레르기 성분을 별도로 확인해 주세요."
        if unsupported and not ambiguous_target and not parsed.uncertain
        else "누구의 어떤 알레르기인지 확인한 내용을 적어 다시 물어봐 주세요."
    )
    return build_response(
        intent="can_i_eat",
        answer=(
            f"{' '.join(details)} {clarification} "
            "확인 전에는 개인 적합성 판단과 레시피·대체 제품 추천을 보류할게요. "
            "계속 적용하려면 설정에서 해당 프로필의 알레르기 정보를 변경해 주세요."
        ),
        risk_level="unknown",
        assessment_status="incomplete",
        reasons=reasons,
        profile=None,
        task_type="product_chat",
        recommended_questions=[],
    ).model_copy(update={"missing_information": reasons})
