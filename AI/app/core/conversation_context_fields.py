"""Bounded task context shared by internal and public state sanitizers."""

from collections.abc import Mapping
import re

from pydantic import BaseModel

DIETS = {"채식", "저염식", "저당식", "글루텐프리", "저칼로리", "저지방", "고단백"}

PERSISTENT_TASK_KEYS = (
    "recipe_requested_diets", "recipe_goals_initialized", "recipe_product",
    "recipe_fingerprints", "vegetarian_allowances", "comparison_products", "comparison_queries",
    "comparison_status",
)

COMPARISON_KEYS = ("comparison_products", "comparison_queries", "comparison_pending_index", "comparison_status")


def json_state(value):
    """Normalize nested state models, without stringifying unsupported values."""
    if isinstance(value, BaseModel):
        return json_state(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, Mapping):
        return {key: json_state(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_state(item) for item in value]
    return value


def carry_task_context(previous, next_state):
    """Carry only bounded task memory, never turn-local allergies or stale picks."""
    previous = json_state(previous or {})
    next_state = json_state(next_state or {})
    result = {key: previous[key] for key in PERSISTENT_TASK_KEYS if key in previous}
    # A new comparison state replaces the whole task, including empty/failed
    # targets. Omitted targets must never restore an older completed comparison.
    if next_state.get("comparison_status"):
        for key in COMPARISON_KEYS:
            result.pop(key, None)
    if next_state.get("last_intent") == "recipe_recommend" and next_state.get("recipe_goals_initialized"):
        result.pop("recipe_product", None)
    if next_state.get("last_intent") == "missing_product" and next_state.get("pending_intent") == "recipe_recommend":
        result.pop("recipe_product", None)
    result.update(next_state)
    if (next_state.get("last_intent") not in (None, "food_compare")
            and next_state.get("pending_intent") != "food_compare"
            and result.get("comparison_status") in {"active", "pending", "unresolved"}):
        # Completed comparison memory is not the current task after a topic switch.
        for key in COMPARISON_KEYS:
            result.pop(key, None)
        result.update(comparison_status="cleared", comparison_products=[], comparison_queries=[])
    return result


def task_context_fields(value, reference_cleaner):
    result = {}
    pending = value.get("pending_allergy")
    if isinstance(pending, Mapping) and isinstance(pending.get("message"), str) and pending["message"].strip():
        from app.data.dictionaries import ALLERGEN_CANONICAL_ALIASES
        clean = {"message": pending["message"].strip()[:4000]}
        for field in ("confirmed", "uncertain"):
            names = pending.get(field)
            if isinstance(names, list):
                clean[field] = list(dict.fromkeys(name for name in names[:32]
                                                 if isinstance(name, str) and name in ALLERGEN_CANONICAL_ALIASES))
        # Unknown condition names are separate from supported ontology entries.
        # They can only hold a request unresolved, never assert product facts.
        for field in ("unsupported_confirmed", "unsupported_uncertain"):
            names = pending.get(field)
            if isinstance(names, list):
                clean[field] = list(dict.fromkeys(name for name in names[:32]
                    if isinstance(name, str) and name not in ALLERGEN_CANONICAL_ALIASES
                    and re.fullmatch(r"[가-힣A-Za-z][가-힣A-Za-z0-9·-]{0,39}", name)))
        if pending.get("unspecified") is True:
            clean["unspecified"] = True
        if isinstance(pending.get("owner_key"), str) and pending["owner_key"].strip():
            clean["owner_key"] = pending["owner_key"].strip()[:256]
        if any(clean.get(field) for field in ("confirmed", "uncertain", "unsupported_confirmed", "unsupported_uncertain", "unspecified")):
            result["pending_allergy"] = clean
    if isinstance(value.get("comparison_status"), str) and value["comparison_status"] in {"active", "pending", "unresolved", "cleared"}:
        result["comparison_status"] = value["comparison_status"]
    if value.get("recipe_goals_initialized") is True:
        result["recipe_goals_initialized"] = True
    diets = value.get("recipe_requested_diets")
    if isinstance(diets, list):
        result["recipe_requested_diets"] = list(dict.fromkeys(item for item in diets[:7] if isinstance(item, str) and item in DIETS))
    reference = reference_cleaner(value.get("recipe_product"))
    if reference:
        result["recipe_product"] = reference
    references = value.get("comparison_products")
    if isinstance(references, list):
        result["comparison_products"] = [reference_cleaner(item) for item in references[:3]]
    queries = value.get("comparison_queries")
    if isinstance(queries, list) and all(isinstance(item, str) and item.strip() for item in queries[:3]):
        result["comparison_queries"] = [item.strip()[:256] for item in queries[:3]]
    index = value.get("comparison_pending_index")
    if type(index) is int and 0 <= index < 3:
        result["comparison_pending_index"] = index
    allowances = value.get("vegetarian_allowances")
    if isinstance(allowances, list):
        clean = []
        for item in allowances[:20]:
            if not isinstance(item, Mapping) or not isinstance(item.get("profile_key"), str) or not item["profile_key"].strip():
                continue
            entry = {"profile_key": item["profile_key"].strip()[:256]}
            for key in ("dairy", "egg", "dairy_pending", "egg_pending"):
                if type(item.get(key)) is bool:
                    entry[key] = item[key]
            for key in ("profile_signature", "dairy_profile_signature", "egg_profile_signature"):
                signature = item.get(key)
                if isinstance(signature, str) and re.fullmatch(r"[0-9a-f]{64}", signature):
                    entry[key] = signature
            if len(entry) > 1:
                clean.append(entry)
        if clean:
            result["vegetarian_allowances"] = clean
    return {key: item for key, item in result.items() if item != [] or (
        key == "recipe_requested_diets" and result.get("recipe_goals_initialized")
    ) or (
        key in {"comparison_products", "comparison_queries"} and result.get("comparison_status")
    )}
