"""Resolve condition owners without treating food-name substrings as people."""

import re


def profile_is_named(profile, message):
    for name in {profile.profile_name, profile.nickname} - {None, "", "기본 프로필"}:
        if name == "나" and re.search(r"(?<![\w])(?:내가|내|저는|제가)(?=\s|$)", message):
            return True
        if re.search(
            r"(?<![\w])" + re.escape(name)
            + r"(?=$|[\s.,!?;]|(?:은|는|이|가|도|만|의|에게|한테)(?=\s|$))",
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
