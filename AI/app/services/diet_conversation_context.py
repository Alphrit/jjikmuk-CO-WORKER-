"""Conversation-only vegetarian clarification; never updates account profiles."""

import re
import hashlib
import json

from app.services.condition_subject import resolve_condition_subject
from app.services.condition_language import coordinated_mentions, shared_predicate


def profile_key(profile):
    if profile.profile_key is not None:
        return f"backend:{profile.profile_key}"
    return f"id:{profile.profile_id}" if profile.profile_id else f"name:{profile.profile_name}:{profile.target_type}"


def _allowance_statements(message):
    result = {}
    for sentence in re.findall(r"[^.!?;\n]+[.!?;\n]?", message):
        # A question in a later sentence must not erase an earlier assertion.
        for clause in re.split(r"(?:하지만|그런데)\s*", sentence):
            mentions = coordinated_mentions(clause, r"유제품|우유|달걀|계란")
            for index, match in enumerate(mentions):
                predicate = shared_predicate(clause, mentions, index)
                # Commas after a verb separate clauses; noun-only commas were
                # already consumed by shared_predicate.
                predicate = re.split(r",|그리고", predicate, maxsplit=1)[0]
                # Split a definite condition from a subsequent request rather
                # than discarding the whole clause for its final question mark.
                predicate = re.split(
                    r"\s+(?=(?:(?:이|그)\s*제품|이거|그거|레시피|요리|다른\s*레시피))",
                    predicate, maxsplit=1,
                )[0]
                directive_question = bool(re.fullmatch(
                    r"\s*(?:은|는|을|를|도)?\s*"
                    r"(?:(?:빼|제외해)\s*|없이\s*(?:만들어|조리해)\s*)"
                    r"(?:줄래(?:요)?|주시겠어요|주실래요|(?:줄|주실)\s*수\s*있(?:어|나요|어요|을까요))\s*\?\s*",
                    predicate,
                ))
                omit_action = bool(re.fullmatch(
                    r"\s*(?:은|는|을|를|도)?\s*(?:넣|사용하|추가하)지\s*"
                    r"(?:말아\s*(?:줘(?:요)?|주세요|줄래(?:요)?|주시겠어요)|마(?:요|세요)?)\s*[.!?]?\s*",
                    predicate,
                ))
                directive_question |= omit_action
                if predicate.rstrip().endswith("?") and not directive_question:
                    continue
                if re.search(r"알레르기|알러지|먹어도\s*(?:돼|되)|먹을까|허용해도", predicate):
                    continue
                key = "dairy" if match[0] in {"유제품", "우유"} else "egg"
                # Resolve the whole predicate before accepting a positive verb.
                # Double negatives/uncertainty are a clarification, not permission.
                if re.search(r"(?:모르|애매|아닌|아니야|아니에요|않아도|제외하지|빼지)", predicate):
                    result[key] = None
                elif omit_action or re.search(
                    r"(?:안|못)\s*먹|먹지\s*(?:않|못)|먹을\s*수\s*없|"
                    r"(?:넣|사용하|추가하)지\s*(?:않|못)|"
                    r"먹[^.!?]{0,30}(?:안\s*돼|안\s*되|불가능|금지|싫|원하지)|"
                    r"빼(?:줘|\s*주|\s*줄|고|야|자|요)|제외|불허|허용하지|금지|없이(?:\s*(?:만들|조리|해)|\s*$)",
                    predicate,
                ):
                    result[key] = False
                elif re.fullmatch(
                    r"\s*(?:은|는|을|를|도)?\s*(?:다시\s*)?"
                    r"(?:먹(?:어(?:요)?|고|을게(?:요)?|을\s*수\s*있어(?:요)?|는\s*채식(?:이야|이에요|입니다)?)|"
                    r"허용(?:해(?:요)?|하는\s*채식(?:이야|이에요)?)|괜찮아(?:요)?)\s*[.!]?\s*",
                    predicate,
                ):
                    result[key] = True
                elif re.search(r"먹|허용|빼|제외|가능|넣|사용|추가", predicate):
                    result[key] = None
    return result


def stated_vegetarian_allowance(message):
    return {key: value for key, value in _allowance_statements(message).items() if value is not None}


def _profile_signature(profile):
    baseline = [profile.vegetarian_dairy_allowed, profile.vegetarian_egg_allowed,
                sorted(profile.resolved_special_diets())]
    return hashlib.sha256(json.dumps(baseline, ensure_ascii=False).encode()).hexdigest()


def _field_signature(profile, field):
    value = getattr(profile, f"vegetarian_{field}_allowed")
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _effective_allowance(profile, entry, field):
    baseline = getattr(profile, f"vegetarian_{field}_allowed")
    if baseline is False or entry.get(field) is False:
        return False
    if entry.get(f"{field}_pending") is True:
        return None
    return entry.get(field, baseline)


def apply_vegetarian_context(profiles, message, state, known_profiles=None):
    allowances = {item["profile_key"]: dict(item) for item in (state or {}).get("vegetarian_allowances", [])}
    for profile in profiles:
        key = profile_key(profile)
        if key not in allowances:
            continue
        signature = _profile_signature(profile)
        entry = allowances[key]
        for field in ("dairy", "egg"):
            field_signature = _field_signature(profile, field)
            previous = entry.get(f"{field}_profile_signature")
            changed = (previous != field_signature) if previous else entry.get("profile_signature") not in (None, signature)
            # Legacy combined signatures cannot identify which field changed.
            # In either format, uncertainty must never silently lift an exclusion.
            if (changed or getattr(profile, f"vegetarian_{field}_allowed") is False) and entry.get(field) is True:
                entry.pop(field)
            if changed and getattr(profile, f"vegetarian_{field}_allowed") is not None:
                entry.pop(f"{field}_pending", None)
            entry[f"{field}_profile_signature"] = field_signature
        entry["profile_signature"] = signature
    ambiguous = False
    for sentence in re.findall(r"[^.!?;\n]+[.!?;\n]?", message):
        stated = _allowance_statements(sentence)
        if not stated:
            continue
        target = resolve_condition_subject(sentence, profiles, known_profiles)
        # Duplicate fallback identities cannot safely carry individual permissions.
        if target is None or sum(profile_key(p) == profile_key(target) for p in profiles) != 1:
            if profiles:
                ambiguous = True
            continue
        key = profile_key(target)
        entry = allowances.setdefault(key, {"profile_key": key})
        entry["profile_signature"] = _profile_signature(target)
        for field in ("dairy", "egg"):
            entry[f"{field}_profile_signature"] = _field_signature(target, field)
        for field, value in stated.items():
            if value is None:
                entry[f"{field}_pending"] = True
            else:
                entry.pop(f"{field}_pending", None)
                # This entry records the utterance, not the effective profile
                # floor. Otherwise a profile prohibition becomes a fictitious
                # user exclusion that survives lifting the profile restriction.
                entry[field] = value
    effective = []
    for profile in profiles:
        entry = allowances.get(profile_key(profile), {})
        ambiguous |= any(entry.get(f"{field}_pending") is True for field in ("dairy", "egg"))
        evaluated = profile.model_copy(update={
            f"vegetarian_{field}_allowed": _effective_allowance(profile, entry, field)
            for field in ("dairy", "egg")
        })
        # Only explicit conversational exclusions are independent of vegetarian
        # identity. Account allowance flags alone must not create this scope.
        evaluated._conversation_exclusions = tuple(field for field in ("dairy", "egg") if entry.get(field) is False)
        effective.append(evaluated)
    return effective, list(allowances.values())[-20:], ambiguous
