"""Resolve condition owners without treating food-name substrings as people."""

import re


# Recognize case/connective particles and their common topic/additive forms
# (엄마를, 엄마랑, 엄마한테는), while retaining the word boundary so that
# product names such as 엄마손파이 and 엄마와플 are not interpreted as people.
_PROFILE_PARTICLE = r"(?:에게서|한테서|께서|에게|한테|께|하고|이랑|랑|와|과|을|를|은|는|이|가|도|만|의)(?:은|는|도|만)?(?:요)?"


def profile_is_named(profile, message):
    for name in {profile.profile_name, profile.nickname} - {None, "", "기본 프로필"}:
        if name == "나" and re.search(r"(?<![\w])(?:내가|내|저는|제가)(?=\s|$)", message):
            return True
        if re.search(
            r"(?<![\w])" + re.escape(name)
            + r"(?=$|[^\w]|" + _PROFILE_PARTICLE + r"(?=[^\w]|$))",
            message,
        ):
            return True
    return False


def resolve_condition_subject(message, active_profiles, known_profiles=None):
    """An explicit other/OFF owner must never fall back to the sole ON owner."""
    active_profiles = list(active_profiles)
    known_profiles = list(known_profiles or active_profiles)
    # Include OFF profiles only for recognizing a reference, never evaluation.
    if any(not p.is_active and profile_is_named(p, message) for p in known_profiles):
        return None
    matches = [p for p in active_profiles if profile_is_named(p, message)]
    if matches:
        return matches[0] if len(matches) == 1 else None
    if any(profile_is_named(p, message) for p in known_profiles):
        return None
    # A named subject not present in the request still isn't the current user.
    # Require a condition noun after the subject, not just any Korean particle.
    if re.search(
        r"(?:^|[.!?;\n]\s*)\s*[^\s.!?;]+(?:은|는|이|가|도)\s+"
        r"(?:유제품|우유|달걀|계란|\S+\s*(?:알레르기|알러지))",
        message,
    ):
        return None
    return active_profiles[0] if len(active_profiles) == 1 else None


def _ends_with_topic_particle(message):
    """'엄마는?', '아빠도요!!': the last word ends in 은/는/도(요), punctuation aside.

    Checked on the last word only; a regex anchored with '$' but searched from
    every start ('\\S+(?:은|는|도)…$') is quadratic on long unspaced text.
    """
    end = len(message)
    while end and not (message[end - 1].isalnum() or message[end - 1] == "_"):
        end -= 1
    words = message[:end].split()
    return bool(words and re.fullmatch(r"\S+(?:은|는|도)(?:요)?", words[-1]))


def unresolved_profile_target_response(message, active_profiles, known_profiles=None, state=None):
    """Ask for selection instead of evaluating an explicitly unselected person.

    Names recognize the target only; OFF and historical allergy facts never
    become assessment inputs. Independent label/definition questions continue
    through the ordinary factual and condition-clarification paths.
    """
    from app.core.models import ChatResponse, Profile
    from app.services.profile_context import active_profile_names
    from app.core.public_product import public_conversation_state

    if re.search(r"다른\s*프로필\s*기준", message):
        selected = active_profile_names(active_profiles)
        return ChatResponse(
            intent="unknown", risk_level="unknown", assessment_status="incomplete",
            overall_decision="check_required", active_profile_names=selected,
            answer="비교할 대상을 먼저 선택해 주세요. 프로필 목록에서 원하는 사람을 ON으로 바꾼 뒤 다시 질문하면 그때 선택된 조건으로 확인해요.",
            conversation_state=public_conversation_state(state), internal_conversation_state=state or {},
        )

    from app.intent.request_semantics import personal_assessment_request
    personal_request = personal_assessment_request(message)
    # A named-person fragment may end in repeated punctuation or emoji. The
    # name matcher below still requires a boundary (엄마손파이 is a food).
    short_followup = _ends_with_topic_particle(message)
    if not (personal_request or short_followup):
        return None
    profiles = list(active_profiles)
    known = list(known_profiles or profiles)
    # Recognize common family references even when the client sends ON rows only.
    known += [Profile(profile_name=name) for name in ("엄마", "아빠", "어머니", "아버지")]
    names = []
    active_names = {name for p in profiles for name in (p.profile_name, p.nickname) if name}
    for candidate in known:
        if not profile_is_named(candidate, message):
            continue
        candidate_names = {candidate.profile_name, candidate.nickname} - {None, "", "기본 프로필"}
        if any(re.search(
            r"(?<![\w])" + re.escape(name)
            + r"(?:은|는|을|를)?\s*(?:말고|빼고|빼면|제외|끄고|OFF)|"
            + r"(?<![\w])" + re.escape(name) + r"가\s*(?:산|준|만든|추천한)\s",
            message, re.IGNORECASE,
        ) for name in candidate_names):
            continue
        if candidate.is_active and candidate_names & active_names:
            continue
        name = candidate.nickname or candidate.profile_name
        if name and name not in names:
            names.append(name)
    if not names:
        return None
    target = ", ".join(names)
    selected = active_profile_names(profiles)
    selection = f"현재 선택된 대상은 {', '.join(selected)}예요. " if selected else "현재 선택된 대상이 없어요. "
    return ChatResponse(
        intent="unknown",
        answer=f"{target} 프로필이 현재 선택되어 있지 않아 해당 대상의 조건으로 판단할 수 없어요. "
               + selection + f"{target} 프로필을 ON으로 선택한 뒤 다시 질문해 주세요.",
        risk_level="unknown", assessment_status="incomplete",
        overall_decision="check_required", allergy_status="unknown",
        active_profile_names=selected,
        missing_information=[f"질문 대상({target})의 활성 프로필이 필요해요."],
        conversation_state=public_conversation_state(state),
        internal_conversation_state=state or {},
    )
