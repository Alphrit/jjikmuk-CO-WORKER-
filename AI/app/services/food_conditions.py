"""Prepare bounded conversational conditions before lookup or clarification.

Pending allergy statements belong to one unfinished request, not to the account
or the conversation's permanent profile. Only an explicit clarification reply
continues them; an independent new question starts a new turn-local assessment.
"""

from dataclasses import dataclass, replace
import re

from app.core.conversation_context_fields import json_state, task_context_fields
from app.core.models import ChatResponse, Profile
from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
from app.services.condition_subject import profile_is_named, resolve_condition_subject
from app.services.diet_conversation_context import apply_vegetarian_context, profile_key, _allowance_statements
from app.services.message_constraint_guard import (
    _message_allergens, apply_turn_local_allergies,
    build_message_constraint_guard_response, TurnAllergyContext,
)
from app.services.request_language import request_is_cancelled, requests_continuation


@dataclass
class FoodConditions:
    message: str
    allergy_message: str
    profiles: list[Profile]
    state: dict
    allergy_context: TurnAllergyContext
    allergy_guard: ChatResponse | None
    ambiguous_diet: bool
    pending: dict | None
    selecting_product: bool = False
    resuming_request: bool = False


def _statement(confirmed, uncertain, unspecified, owner):
    prefix = f"{owner.profile_name} " if owner is not None else ""
    parts = [f"{prefix}{name} 알레르기는 있어." for name in sorted(confirmed)]
    parts += [f"{prefix}{name} 알레르기가 있는지 모르겠어." for name in sorted(uncertain)]
    if unspecified:
        parts.append(f"{prefix}알레르기가 있어.")
    return " ".join(parts)


def prepare_food_conditions(message, profiles, state=None, known_profiles=None, *, resumed_product_message=None, current_condition_message=None):
    state = json_state(state or {})
    pending = task_context_fields({"pending_allergy": state.pop("pending_allergy", None)}, lambda _: None).get("pending_allergy")
    known_profiles = known_profiles if known_profiles is not None else profiles
    condition_message = current_condition_message if current_condition_message is not None else message
    parsed = _message_allergens(condition_message)
    allergy_message = condition_message
    task_message = resumed_product_message or message
    owner = resolve_condition_subject(condition_message, profiles, known_profiles)
    original = message
    owner_unresolved = False
    selection_reply = False

    if pending:
        old_confirmed = set(pending.get("confirmed", [])) | set(pending.get("unsupported_confirmed", []))
        old_uncertain = set(pending.get("uncertain", [])) | set(pending.get("unsupported_uncertain", []))
        owners = [p for p in profiles if profile_key(p) == pending.get("owner_key")]
        old_owner = owners[0] if len(owners) == 1 else None
        certainty = re.fullmatch(r"\s*(?:네[,\s]*)?(있어(?:요)?|없어(?:요)?)[.!\s]*", message)
        if certainty and old_owner is not None and len(old_uncertain) == 1 and not pending.get("unspecified"):
            names = frozenset(old_uncertain)
            parsed = replace(parsed, **{"confirmed" if certainty[1].startswith("있") else "denied": names})
        old_names = old_confirmed | old_uncertain
        mentioned = parsed.confirmed | parsed.uncertain | parsed.denied
        named = any(profile_is_named(p, condition_message) for p in known_profiles)
        owner_reply = (named and bool(re.search(r"(?:거야|거예요|것|알레르기|프로필|맞아)|^\s*\S+\s*[.!]?\s*$", message))) or bool(re.fullmatch(
            r"\s*[^\s.!?]+\s+(?:거야|거예요|것이야|것이에요)[.!?\s]*", message,
        ))
        allergy_reply = bool(mentioned & old_names) or bool(pending.get("unspecified") and mentioned)
        unsure_reply = bool(re.fullmatch(r"\s*(?:아직\s*)?(?:모르겠어|몰라|모르겠어요|잘 모르겠어)[.!?\s]*", message))
        from app.services.conversation_router import is_pending_candidate_reply
        selection_reply = is_pending_candidate_reply(message, state)
        diet_fields = _allowance_statements(condition_message)
        diet_reply = any(item.get(f"{field}_pending") is True
                         for item in state.get("vegetarian_allowances", []) for field in diet_fields)
        from app.intent.rules import rule_based_classify_intent
        reply_intent = rule_based_classify_intent(message, None, None, [])
        explicit_task = bool(re.search(r"(?<![가-힣])제품|(?:이|그)\s*제품|이거|그거|레시피|요리|추천|비교|나트륨|원재료", message))
        new_request = explicit_task or reply_intent not in {"unknown", "ambiguous"} or bool(mentioned) or bool(re.search(
            r"취소|그만|다른\s*(?:질문|얘기|이야기)|새\s*질문", message,
        ))
        # An unrecognized short answer is not evidence that the condition was
        # withdrawn. Only a separate request/cancellation ends this task.
        waiting_reply = not new_request
        # Lifecycle belongs to the original utterance. Removing a partial
        # cancellation clause for lookup must not turn its continuation into
        # an independent request and discard the pending condition.
        cancelled = request_is_cancelled(condition_message)
        continuation = requests_continuation(condition_message) or bool(resumed_product_message)
        if not cancelled and (owner_reply or allergy_reply or unsure_reply or selection_reply or diet_reply or waiting_reply or continuation):
            if pending.get("owner_key"):
                # OFF/removed owners and a different named person are not an
                # implicit reassignment of the original allergy.
                owner = old_owner if not named or owner is old_owner else None
            elif not named:
                owner = None
            owner_clause = re.match(r"[^.!?]+[.!?]?", message.strip())
            owner_text = owner_clause[0] if owner_clause else message
            if owner_reply and not allergy_reply and (owner_text.rstrip().endswith("?") or re.search(r"아니|모르", owner_text)):
                owner = None
            confirmed = (old_confirmed - parsed.denied - parsed.uncertain) | parsed.confirmed
            uncertain = (old_uncertain - parsed.denied - parsed.confirmed) | parsed.uncertain
            unspecified = bool(pending.get("unspecified") and not mentioned)
            allergy_message = _statement(confirmed, uncertain, unspecified, owner)
            # Resolve only the condition; retain the original task and its
            # parameters for short answers such as "아이 거야".
            original = pending["message"]
            if not resumed_product_message and (waiting_reply or continuation
                    or (not explicit_task and reply_intent in {"unknown", "ambiguous", "general_allergy_guide", "missing_product"})):
                task_message = original
            # No eligible owner must stay unresolved even if only one different
            # ON profile is left; the shared resolver must not fall back to it.
            owner_unresolved = owner is None
            parsed = _message_allergens(allergy_message)

    allergy_context = apply_turn_local_allergies(
        allergy_message, profiles, known_profiles, force_ambiguous_target=owner_unresolved,
    )
    guard = build_message_constraint_guard_response(
        allergy_message, profiles=profiles, known_profiles=known_profiles,
        force_ambiguous_target=owner_unresolved,
    )
    # Collect definite exclusions even when another condition requires an early
    # return. Parse the new utterance, never replay old dietary permissions.
    effective, allowances, ambiguous = apply_vegetarian_context(
        allergy_context.profiles, condition_message, state, known_profiles,
    )
    state["vegetarian_allowances"] = allowances
    # Preserve typed current-utterance goal edits when cancellation or product
    # confirmation reconstructed only the task. No account values are changed.
    from app.services.diet_goal_context import read_diet_goal_update
    goal_update = read_diet_goal_update(condition_message)
    if task_message != condition_message and goal_update.touched:
        task_message = goal_update.restate(task_message)
    if goal_update.touched and state.get("pending_intent") == "recipe_recommend":
        state["recipe_requested_diets"] = goal_update.apply(state.get("recipe_requested_diets", []))
        state["recipe_goals_initialized"] = True
        if state.get("pending_user_message"):
            state["pending_user_message"] = goal_update.restate(state["pending_user_message"])[:4000]
    next_pending = None
    if parsed.confirmed or parsed.uncertain or parsed.unspecified:
        next_pending = {
            "message": task_message[:4000], "unspecified": parsed.unspecified,
        }
        for field in ("confirmed", "uncertain"):
            names = getattr(parsed, field)
            next_pending[field] = sorted(name for name in names if name in ALLERGEN_CANONICAL_ALIASES)
            unsupported = sorted(name for name in names if name not in ALLERGEN_CANONICAL_ALIASES)
            if unsupported:
                next_pending[f"unsupported_{field}"] = unsupported
        if owner is not None and not allergy_context.target_ambiguous:
            next_pending["owner_key"] = profile_key(owner)
        if guard is not None or ambiguous:
            state["pending_allergy"] = next_pending
    return FoodConditions(task_message, allergy_message, effective, state,
                          allergy_context, guard, ambiguous, next_pending, selection_reply,
                          task_message != message)
