import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    model_serializer,
    model_validator,
)

from app.core.backend_analysis import (
    BackendAnalysisContext, PublicBackendAnalysis, ProfileKey, ProfileSource,
    MAX_V2_PROFILES, MAX_V2_HISTORY_ITEMS, MAX_V2_HISTORY_CONTENT,
    MAX_V2_HISTORY_TOTAL, validate_request_context, backend_analysis_active,
)

from app.core.numeric import parse_non_negative_number, parse_positive_food_quantity
from app.core.conversation_context_fields import task_context_fields
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN,
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    product_label_content_revision_sha256,
)
from app.core.public_product_schema import (
    MAX_PUBLIC_PRODUCT_CANDIDATES,
    MAX_PUBLIC_RECOMMENDED_PRODUCTS,
    PublicProduct,
    PublicProductCandidateCard,
    PublicNutritionFacts,
    PublicRecommendedProductCard,
)
from app.core.public_api_schema import (
    MAX_PUBLIC_PROFILE_RESULTS,
    PublicConversationState,
    PublicPerProfileResult,
    PublicUiGuidance,
)


MAX_CHAT_MESSAGE_LENGTH = 4000
MAX_CHAT_ANSWER_LENGTH = 16000
MAX_CHAT_HISTORY_ITEMS = 50
MAX_CHAT_HISTORY_CONTENT_LENGTH = 4000
MAX_CHAT_HISTORY_TOTAL_LENGTH = 20000
MAX_CHAT_CONTEXT_TOTAL_LENGTH = 24000
MAX_ACTIVE_PROFILES = 20
MAX_PROFILE_NAME_LENGTH = 128
MAX_PROFILE_LIST_ITEMS = 32
MAX_PROFILE_ITEM_LENGTH = 256
MAX_PROFILE_TOTAL_TEXT_LENGTH = 4096
MAX_RAW_PROFILE_PAYLOAD_LENGTH = 16384
MAX_COMBINED_RAW_PROFILE_PAYLOAD_LENGTH = 65536
MAX_COMBINED_PROFILE_TEXT_LENGTH = 32768
MAX_PENDING_PRODUCT_CANDIDATES = 3
MAX_STATE_TEXT_LENGTH = 256
MAX_PENDING_USER_MESSAGE_LENGTH = 4000
MAX_CONVERSATION_TITLE_LENGTH = 32
MAX_CHAT_RESPONSE_IDENTIFIER_LENGTH = 128
MAX_CHAT_RESPONSE_TIMESTAMP_LENGTH = 64
MAX_CHAT_RESPONSE_CODE_LENGTH = 256
MAX_CHAT_RESPONSE_REASON_LENGTH = 1000
MAX_CHAT_RESPONSE_REASONS = 100
MAX_CHAT_RESPONSE_AUXILIARY_REASONS = 50
MAX_CHAT_RESPONSE_RECOMMENDED_QUESTIONS = 3
MAX_CHAT_RESPONSE_RECOMMENDED_QUESTION_LENGTH = 1000
MAX_CHAT_RESPONSE_SOURCES = 10
MAX_CHAT_RESPONSE_SOURCE_LENGTH = 2048
MAX_CHAT_RESPONSE_GROUNDING_ENTRIES = 5
MAX_CHAT_RESPONSE_GROUNDING_ENTRY_LENGTH = 5000
MAX_CHAT_RESPONSE_VALIDATION_FAILURE_TYPES = 50
MAX_CHAT_RESPONSE_RULE_IDS = 50
MAX_PUBLIC_CONVERSATION_LIST_ITEMS = 100
MAX_PUBLIC_CONVERSATION_MESSAGE_ITEMS = 200
MAX_PUBLIC_CONVERSATION_SEQUENCE = 400
MAX_PUBLIC_CONVERSATION_CURSOR_LENGTH = 512


ProfileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PROFILE_NAME_LENGTH,
    ),
]
ProfileListItem = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PROFILE_ITEM_LENGTH,
    ),
]
ProfileEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=320),
]
ProfileTargetType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
]
ChatHistoryContent = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_HISTORY_CONTENT_LENGTH,
    ),
]
ChatResponseIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_IDENTIFIER_LENGTH,
    ),
]
ChatResponseTimestamp = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_TIMESTAMP_LENGTH,
    ),
]
ChatResponseCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_CODE_LENGTH,
    ),
]
ChatResponseReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_REASON_LENGTH,
    ),
]
ChatResponseRecommendedQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_RECOMMENDED_QUESTION_LENGTH,
    ),
]
ChatResponseSource = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_SOURCE_LENGTH,
    ),
]
ChatResponseGroundingEntry = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CHAT_RESPONSE_GROUNDING_ENTRY_LENGTH,
    ),
]


def _ensure_list(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    if not isinstance(value, str):
        return []

    text = str(value).strip()
    if not text:
        return []

    normalized = text.replace("/", ",").replace("|", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _is_valid_list_payload(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, (list, tuple, set)):
        return not value or all(
            isinstance(item, str) and bool(item.strip())
            for item in value
        )
    return False


def _has_only_valid_list_payloads(payload: Dict[str, Any], keys: List[str]) -> bool:
    supplied_values = [payload[key] for key in keys if key in payload]
    return bool(supplied_values) and all(
        _is_valid_list_payload(value)
        for value in supplied_values
    )


def _pick_valid_list_value(*values: Any) -> Any:
    empty_value: Any = None
    for value in values:
        if not _is_valid_list_payload(value):
            continue
        if isinstance(value, str) and not value.strip():
            empty_value = value
            continue
        if isinstance(value, (list, tuple, set)) and not value:
            empty_value = value
            continue
        return value
    return empty_value


def _pick_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
    return None


def _to_float(value: Any, *, expected_unit: Optional[str] = None) -> Optional[float]:
    return parse_non_negative_number(value, expected_unit=expected_unit)


def _normalize_numeric_mapping(
    value: Any,
    *,
    expected_units: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}

    normalized: Dict[str, float] = {}
    for key, raw_value in value.items():
        normalized_key = str(key)
        parsed = _to_float(
            raw_value,
            expected_unit=(expected_units or {}).get(normalized_key),
        )
        if parsed is not None:
            normalized[normalized_key] = parsed
    return normalized


def _bounded_state_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:MAX_STATE_TEXT_LENGTH]


def _sanitize_product_reference(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None

    reference: Dict[str, Any] = {}
    for key in ["barcode", "report_number", "product_name"]:
        text = _bounded_state_text(value.get(key))
        if text is not None:
            reference[key] = text

    product_id = value.get("product_id")
    if isinstance(product_id, int) and not isinstance(product_id, bool) and product_id >= 0:
        reference["product_id"] = product_id

    # A display name alone is not an authoritative product identity.
    if not any(reference.get(key) for key in ["barcode", "report_number", "product_id"]):
        return None
    return reference


def _sanitize_conversation_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    state: Dict[str, Any] = {}
    for key in ["last_intent", "last_task_type", "pending_intent"]:
        text = _bounded_state_text(value.get(key))
        if text is not None:
            state[key] = text

    pending_user_message = value.get("pending_user_message")
    if isinstance(pending_user_message, str):
        pending_user_message = pending_user_message.strip()
        if pending_user_message:
            state["pending_user_message"] = pending_user_message[
                :MAX_PENDING_USER_MESSAGE_LENGTH
            ]

    last_product = _sanitize_product_reference(value.get("last_confirmed_product"))
    if last_product is not None:
        state["last_confirmed_product"] = last_product

    raw_candidates = value.get("pending_product_candidates")
    if isinstance(raw_candidates, list):
        candidates = [
            reference
            for item in raw_candidates[:MAX_PENDING_PRODUCT_CANDIDATES]
            if (reference := _sanitize_product_reference(item)) is not None
        ]
        if candidates:
            state["pending_product_candidates"] = candidates

    raw_fingerprints = value.get("recipe_fingerprints")
    if isinstance(raw_fingerprints, list):
        fingerprints = [
            item.lower()
            for item in raw_fingerprints[:20]
            if isinstance(item, str) and re.fullmatch(r"[0-9a-fA-F]{64}", item)
        ]
        if fingerprints:
            state["recipe_fingerprints"] = list(dict.fromkeys(fingerprints))

    state.update(task_context_fields(value, _sanitize_product_reference))
    if state:
        state["version"] = 1
    return state


def _to_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "f", "no", "n", "0", "off"}:
        return False
    return None


PROFILE_DATA_ALIAS_GROUPS = {
    "profile_id": ("profile_id", "profileId", "id"),
    "profile_name": ("profile_name", "nickname", "name"),
    "user_id": ("user_id", "userId", "account_id", "accountId"),
    "allergies": ("allergies", "알레르기리스트"),
    "user_allergies": ("user_allergies", "userAllergies"),
    "conditions": ("conditions", "diseases", "질환정보"),
    "special_diets": ("special_diets", "specialDiet", "special_diet", "식이습관"),
    "disliked_ingredients": (
        "disliked_ingredients",
        "dislikedIngredients",
        "비선호성분",
    ),
    "target_type": ("target_type", "targetType"),
    "is_active": ("is_active", "isActive", "active"),
}

PROFILE_LIST_INPUT_KEYS = tuple(dict.fromkeys([
    *PROFILE_DATA_ALIAS_GROUPS["allergies"],
    *PROFILE_DATA_ALIAS_GROUPS["user_allergies"],
    *PROFILE_DATA_ALIAS_GROUPS["conditions"],
    *PROFILE_DATA_ALIAS_GROUPS["special_diets"],
    *PROFILE_DATA_ALIAS_GROUPS["disliked_ingredients"],
    "special_diet",
]))


def _validate_raw_profile_collection_bounds(payload: Dict[str, Any]) -> None:
    """Reject oversized profile collections before comma-splitting allocates them."""
    for key in PROFILE_LIST_INPUT_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            if len(value) > MAX_PROFILE_TOTAL_TEXT_LENGTH:
                raise ValueError(f"profile field '{key}' is too long")
            continue
        if not isinstance(value, (list, tuple, set)):
            continue
        if len(value) > MAX_PROFILE_LIST_ITEMS:
            raise ValueError(
                f"profile field '{key}' has more than "
                f"{MAX_PROFILE_LIST_ITEMS} items"
            )
        if any(
            isinstance(item, str) and len(item.strip()) > MAX_PROFILE_ITEM_LENGTH
            for item in value
        ):
            raise ValueError(
                f"profile field '{key}' contains an oversized text item"
            )


def _json_text_length(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise ValueError("raw profile payload must be JSON-compatible") from exc


def _first_present_value(
    payload: Dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[bool, Any]:
    for key in keys:
        if key in payload:
            return True, payload[key]
    return False, None


def _merge_profile_data_wrapper(value: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a backend ``data`` envelope without allowing it to override outer values."""
    outer_payload = dict(value)
    inner_payload = outer_payload.pop("data", None)
    if not isinstance(inner_payload, dict):
        return outer_payload

    payload = {**inner_payload, **outer_payload}
    # An outer alias is from the higher-trust layer. Promote it to the canonical
    # key and remove every inner alias in that semantic group, including when
    # the outer value is explicitly null/blank.
    for canonical_key, aliases in PROFILE_DATA_ALIAS_GROUPS.items():
        supplied, outer_value = _first_present_value(outer_payload, aliases)
        if not supplied:
            continue
        for alias in aliases:
            payload.pop(alias, None)
        payload[canonical_key] = outer_value

    payload.pop("data", None)
    return payload


def _parse_explicit_profile_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return False
    if not isinstance(value, str):
        return False

    text = value.strip().lower()
    if text in {"true", "t", "yes", "y", "1", "on", "active", "enabled"}:
        return True
    if text in {
        "false",
        "f",
        "no",
        "n",
        "0",
        "off",
        "inactive",
        "disabled",
    }:
        return False
    return False


class Profile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    profile_id: int = Field(default=0, ge=0, le=9223372036854775807)
    profile_name: ProfileName = "기본 프로필"
    user_id: Optional[int] = Field(default=None, ge=0, le=9223372036854775807)
    email: Optional[ProfileEmail] = None
    nickname: Optional[ProfileName] = None
    allergies: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    user_allergies: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    conditions: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    special_diets: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    special_diet: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    disliked_ingredients: List[ProfileListItem] = Field(
        default_factory=list,
        max_length=MAX_PROFILE_LIST_ITEMS,
    )
    target_type: ProfileTargetType = "self"
    profile_key: Optional[ProfileKey] = Field(default=None, validation_alias=AliasChoices("profileKey", "profile_key"))
    profile_source: Optional[ProfileSource] = Field(default=None, validation_alias=AliasChoices("profileSource", "profile_source"))
    is_active: bool = True
    allergy_context_provided: bool = Field(default=False, exclude=True)
    condition_context_provided: bool = Field(default=False, exclude=True)
    diet_context_provided: bool = Field(default=False, exclude=True)
    # Conversation evaluation uses copies and never writes to account profiles.
    # These are also accepted current-profile constraints. Preserve them when
    # saved-chat requests are serialized/revalidated and fingerprinted.
    vegetarian_dairy_allowed: Optional[bool] = None
    vegetarian_egg_allowed: Optional[bool] = None
    _conversation_exclusions: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="before")
    @classmethod
    def normalize_profile_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = _merge_profile_data_wrapper(value)
        _validate_raw_profile_collection_bounds(payload)

        allergy_context_provided = _has_only_valid_list_payloads(
            payload,
            [
                "allergies",
                "user_allergies",
                "userAllergies",
                "알레르기리스트",
            ],
        )
        condition_context_provided = _has_only_valid_list_payloads(
            payload,
            ["conditions", "diseases", "질환정보"],
        )
        diet_context_provided = _has_only_valid_list_payloads(
            payload,
            ["special_diets", "specialDiet", "special_diet", "식이습관"],
        )

        profile_name = _pick_non_empty(
            payload.get("profile_name"),
            payload.get("nickname"),
            payload.get("name"),
            "기본 프로필",
        )
        profile_id = _pick_non_empty(
            payload.get("profile_id"),
            payload.get("profileId"),
            payload.get("id"),
            0,
        )
        user_id_supplied, user_id = _first_present_value(
            payload,
            PROFILE_DATA_ALIAS_GROUPS["user_id"],
        )
        diseases = _pick_valid_list_value(
            payload.get("conditions"),
            payload.get("diseases"),
            payload.get("질환정보"),
        )
        special_diets = _pick_valid_list_value(
            payload.get("special_diets"),
            payload.get("specialDiet"),
            payload.get("special_diet"),
            payload.get("식이습관"),
        )
        disliked_ingredients = _pick_valid_list_value(
            payload.get("disliked_ingredients"),
            payload.get("dislikedIngredients"),
            payload.get("비선호성분"),
        )
        allergies = _pick_valid_list_value(
            payload.get("allergies"),
            payload.get("알레르기리스트"),
        )
        user_allergies = _pick_valid_list_value(
            payload.get("user_allergies"),
            payload.get("userAllergies"),
            allergies,
        )
        target_type = _pick_non_empty(payload.get("target_type"), payload.get("targetType"), "self")
        is_active_supplied, is_active = _first_present_value(
            payload,
            PROFILE_DATA_ALIAS_GROUPS["is_active"],
        )

        # Let the bounded integer field validate aliases too. Eager int()
        # both truncated fractional IDs and raised OverflowError for infinity.
        payload["profile_id"] = profile_id if profile_id not in (None, "") else 0
        payload["profile_name"] = str(profile_name).strip() or "기본 프로필"
        payload["user_id"] = (
            user_id
            if user_id_supplied and user_id not in (None, "")
            else None
        )
        payload["nickname"] = payload.get("nickname") or payload["profile_name"]
        payload["conditions"] = _ensure_list(diseases)
        payload["special_diets"] = _ensure_list(special_diets)
        payload["special_diet"] = _ensure_list(special_diets)
        payload["disliked_ingredients"] = _ensure_list(disliked_ingredients)
        payload["allergies"] = _ensure_list(allergies)
        payload["user_allergies"] = _ensure_list(user_allergies)
        payload["target_type"] = str(target_type).strip() if target_type is not None else "self"
        payload["is_active"] = (
            _parse_explicit_profile_active(is_active)
            if is_active_supplied
            else True
        )
        payload["allergy_context_provided"] = allergy_context_provided
        payload["condition_context_provided"] = condition_context_provided
        payload["diet_context_provided"] = diet_context_provided
        return payload

    @model_validator(mode="after")
    def validate_profile_text_budget(self):
        text_values = [
            self.profile_name,
            self.email or "",
            self.nickname or "",
            self.target_type,
            *self.resolved_allergies(),
            *self.conditions,
            *self.resolved_special_diets(),
            *self.disliked_ingredients,
        ]
        if sum(len(value) for value in text_values) > MAX_PROFILE_TOTAL_TEXT_LENGTH:
            raise ValueError(
                f"profile text exceeds {MAX_PROFILE_TOTAL_TEXT_LENGTH} characters"
            )
        return self

    @model_serializer(mode="wrap")
    def preserve_context_provenance(self, serializer):
        payload = serializer(self)
        if not isinstance(payload, dict):
            return payload
        # Keep legacy requests without these optional flags fingerprint-stable.
        for field in ("vegetarian_dairy_allowed", "vegetarian_egg_allowed", "profile_key", "profile_source"):
            if getattr(self, field) is None:
                payload.pop(field, None)
        if not self.allergy_context_provided:
            payload.pop("allergies", None)
            payload.pop("user_allergies", None)
        if not self.condition_context_provided:
            payload.pop("conditions", None)
        if not self.diet_context_provided:
            payload.pop("special_diets", None)
            payload.pop("special_diet", None)
        return payload

    def resolved_allergies(self) -> List[str]:
        return list(dict.fromkeys([
            *self.allergies,
            *self.user_allergies,
        ]))

    def resolved_special_diets(self) -> List[str]:
        return list(dict.fromkeys([
            *self.special_diets,
            *self.special_diet,
        ]))


_NUTRITION_BASIS_TYPE_ALIASES = {
    "unknown": "unknown",
    "미확인": "unknown",
    "per_serving": "per_serving",
    "per-serving": "per_serving",
    "per serving": "per_serving",
    "serving": "per_serving",
    "1회제공량": "per_serving",
    "1회 제공량": "per_serving",
    "per_100g": "per_100g",
    "per-100g": "per_100g",
    "per 100g": "per_100g",
    "100g": "per_100g",
    "100g당": "per_100g",
    "per_100ml": "per_100ml",
    "per-100ml": "per_100ml",
    "per 100ml": "per_100ml",
    "100ml": "per_100ml",
    "100ml당": "per_100ml",
    "per_package": "per_package",
    "per-package": "per_package",
    "per package": "per_package",
    "package": "per_package",
    "총내용량": "per_package",
    "총 내용량": "per_package",
}


def _normalize_nutrition_basis_type(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    return _NUTRITION_BASIS_TYPE_ALIASES.get(normalized, "unknown")


def _explicit_quantity(
    payload: Dict[str, Any],
    *,
    combined_keys: List[str],
    value_keys: List[str],
    unit_keys: List[str],
) -> Optional[tuple[float, str]]:
    combined = _pick_non_empty(*(payload.get(key) for key in combined_keys))
    parsed = parse_positive_food_quantity(combined)
    if parsed is not None:
        return parsed

    amount = _pick_non_empty(*(payload.get(key) for key in value_keys))
    unit = _pick_non_empty(*(payload.get(key) for key in unit_keys))
    if amount is None or unit is None:
        return None
    return parse_positive_food_quantity(f"{amount}{unit}")


class NutritionBasis(BaseModel):
    """Explicit denominator metadata for all values in ``Product.nutrition``.

    ``unknown`` is deliberately the backward-compatible default. Package names,
    weights, and nutrient magnitudes are never used to infer a denominator.
    """

    model_config = ConfigDict(extra="forbid")

    basis_type: Literal[
        "unknown",
        "per_serving",
        "per_100g",
        "per_100ml",
        "per_package",
    ] = "unknown"
    serving_size_value: Optional[float] = Field(default=None, gt=0)
    serving_size_unit: Optional[Literal["g", "ml"]] = None
    package_size_value: Optional[float] = Field(default=None, gt=0)
    package_size_unit: Optional[Literal["g", "ml"]] = None
    servings_per_package: Optional[float] = Field(default=None, gt=0)
    basis_source: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def normalize_explicit_basis(cls, value: Any) -> Any:
        if value is None:
            return {"basis_type": "unknown"}
        if isinstance(value, str):
            return {"basis_type": _normalize_nutrition_basis_type(value)}
        if not isinstance(value, dict):
            return {"basis_type": "unknown"}

        raw = dict(value)
        basis_type = _normalize_nutrition_basis_type(
            _pick_non_empty(
                raw.get("basis_type"),
                raw.get("type"),
                raw.get("kind"),
                raw.get("measurement_basis"),
            )
        )
        normalized: Dict[str, Any] = {"basis_type": basis_type}

        serving_size = _explicit_quantity(
            raw,
            combined_keys=["serving_size", "servingSize", "SERVING_SIZE"],
            value_keys=["serving_size_value", "servingSizeValue"],
            unit_keys=["serving_size_unit", "servingSizeUnit"],
        )
        package_size = _explicit_quantity(
            raw,
            combined_keys=["package_size", "packageSize"],
            value_keys=["package_size_value", "packageSizeValue"],
            unit_keys=["package_size_unit", "packageSizeUnit"],
        )

        # ``amount``/``unit`` describe the explicitly declared denominator.
        denominator = _explicit_quantity(
            raw,
            combined_keys=["basis_amount_text"],
            value_keys=["amount", "basis_amount", "basisAmount"],
            unit_keys=["unit", "basis_unit", "basisUnit"],
        )
        if denominator is not None and basis_type == "per_serving" and serving_size is None:
            serving_size = denominator
        if denominator is not None and basis_type == "per_package" and package_size is None:
            package_size = denominator

        if serving_size is not None:
            normalized["serving_size_value"], normalized["serving_size_unit"] = serving_size
        if package_size is not None:
            normalized["package_size_value"], normalized["package_size_unit"] = package_size

        servings = parse_non_negative_number(
            _pick_non_empty(
                raw.get("servings_per_package"),
                raw.get("servingsPerPackage"),
                raw.get("servings_per_container"),
                raw.get("servingsPerContainer"),
            )
        )
        if servings is not None and servings > 0:
            normalized["servings_per_package"] = servings

        basis_source = _pick_non_empty(
            raw.get("basis_source"),
            raw.get("basisSource"),
            raw.get("source"),
        )
        if basis_source is not None:
            normalized["basis_source"] = str(basis_source).strip()[:128]
        return normalized


class ProductLabelProvenance(BaseModel):
    """Trace for the label facts stored in one product-catalog record.

    This does not assert that the product is the newest version on the market.
    ``label_observed_at`` is only when this exact label content was observed by
    the named source. The content hash identifies equal normalized snapshots;
    it is not evidence of source authority or freshness. Legacy rows
    intentionally remain ``unavailable``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Optional[Literal[
        "manufacturer_label",
        "regulatory_database",
        "retailer_catalog",
        "internal_catalog",
        "user_supplied_label",
        "other",
    ]] = None
    source_id: Optional[Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN,
        ),
    ]] = None
    source_record_id: Optional[Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=256,
            pattern=PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN,
        ),
    ]] = None
    label_observed_at: Optional[datetime] = None
    upstream_updated_at: Optional[datetime] = None
    upstream_revision: Optional[Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=256,
            pattern=PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN,
        ),
    ]] = None
    ingested_content_revision_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    content_revision_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Runtime-computed canonical snapshot identifier; not source or "
            "freshness proof."
        ),
    )
    content_revision_binding_status: Literal[
        "matched_ingested_record",
        "ingested_revision_missing",
        "ingested_revision_mismatch",
    ] = "ingested_revision_missing"
    covered_fact_groups: tuple[Literal[
        "identity",
        "ingredients",
        "allergens",
        "nutrition",
        "nutrition_basis",
    ], ...] = Field(default_factory=tuple, max_length=5)
    lineage_status: Literal["complete", "partial", "unavailable"] = "unavailable"

    @model_validator(mode="before")
    @classmethod
    def normalize_provenance(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return {}

        payload = dict(value)
        alias_groups = {
            "source_type": ("sourceType", "label_source_type"),
            "source_id": ("sourceId", "label_source_id"),
            "source_record_id": (
                "sourceRecordId",
                "label_source_record_id",
            ),
            "label_observed_at": ("labelObservedAt", "observed_at"),
            "upstream_updated_at": ("upstreamUpdatedAt",),
            "upstream_revision": ("upstreamRevision", "source_revision"),
            "content_revision_sha256": (
                "contentRevisionSha256",
                "label_content_revision_sha256",
            ),
            "ingested_content_revision_sha256": (
                "ingestedContentRevisionSha256",
                "label_ingested_content_revision_sha256",
            ),
            "covered_fact_groups": (
                "coveredFactGroups",
                "label_covered_fact_groups",
            ),
            "lineage_status": ("lineageStatus", "label_lineage_status"),
        }
        for canonical, aliases in alias_groups.items():
            if payload.get(canonical) not in (None, ""):
                pass
            else:
                for alias in aliases:
                    if payload.get(alias) not in (None, ""):
                        payload[canonical] = payload[alias]
                        break
            for alias in aliases:
                payload.pop(alias, None)

        groups = payload.get("covered_fact_groups")
        if isinstance(groups, str):
            text = groups.strip()
            if text.startswith("["):
                try:
                    decoded = json.loads(text)
                except (TypeError, ValueError):
                    decoded = []
                groups = decoded if isinstance(decoded, list) else []
            else:
                groups = _ensure_list(text)
        if isinstance(groups, (list, tuple, set)):
            payload["covered_fact_groups"] = tuple(dict.fromkeys(
                str(group).strip()
                for group in groups
                if str(group).strip() in PRODUCT_LABEL_FACT_GROUPS
            ))
        else:
            payload["covered_fact_groups"] = ()

        revision = payload.get("content_revision_sha256")
        if isinstance(revision, str):
            payload["content_revision_sha256"] = revision.strip().lower() or None
        ingested_revision = payload.get("ingested_content_revision_sha256")
        if isinstance(ingested_revision, str):
            payload["ingested_content_revision_sha256"] = (
                ingested_revision.strip().lower() or None
            )

        if payload.get("lineage_status") in (None, ""):
            # Completeness is an explicit ingestion assertion. A partially
            # backfilled row must never gain authority merely because some
            # trace columns happen to be non-NULL.
            payload["lineage_status"] = "unavailable"
        return payload

    @model_validator(mode="after")
    def require_unambiguous_observation_times(self) -> "ProductLabelProvenance":
        for field_name in ("label_observed_at", "upstream_updated_at"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    f"{field_name} must include an explicit UTC offset"
                )
            object.__setattr__(self, field_name, value.astimezone(timezone.utc))
        return self


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    product_id: Optional[int] = None
    product_name: Optional[str] = None
    food_code: Optional[str] = None
    report_number: Optional[str] = None
    manufacturer_name: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    nutrient_text: Optional[str] = None
    allergy_warning: Optional[str] = None
    weight: Optional[str] = None
    image_url: Optional[str] = None
    clean_product_name: Optional[str] = None
    total_weight: Optional[str] = None
    category_large: Optional[str] = None
    category_medium: Optional[str] = None
    category_small: Optional[str] = None
    category_detail: Optional[str] = None
    prdlst_dcnm: Optional[str] = None
    barcode: Optional[str] = None
    rawmtrl_nm: Optional[str] = None
    allergy: List[str] = Field(default_factory=list)
    ingredients: List[str] = Field(default_factory=list)
    allergy_info: List[str] = Field(default_factory=list)
    nutrition: Dict[str, float] = Field(default_factory=dict)
    nutrition_basis: NutritionBasis = Field(default_factory=NutritionBasis)
    label_provenance: ProductLabelProvenance = Field(
        default_factory=ProductLabelProvenance
    )
    nutrition_percent: Dict[str, float] = Field(default_factory=dict)
    macro_percent: Dict[str, float] = Field(default_factory=dict)
    match_confidence: Optional[float] = None
    product_found: bool = True
    facts_verified: bool = Field(default=True, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_product_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        product_envelopes = [payload]
        if isinstance(payload.get("data"), dict):
            payload = dict(payload["data"])
            product_envelopes.append(payload)
        if isinstance(payload.get("product"), dict):
            payload = dict(payload["product"])
            product_envelopes.append(payload)

        allergy_value = payload.get("allergy", payload.get("allergy_info"))
        payload["product_name"] = _pick_non_empty(
            payload.get("product_name"),
            payload.get("productName"),
            payload.get("식품명"),
            payload.get("PRDLST_NM"),
        )
        payload["report_number"] = _pick_non_empty(
            payload.get("report_number"),
            payload.get("report_no"),
            payload.get("reportNo"),
            payload.get("품목제조보고번호"),
            payload.get("PRDLST_REPORT_NO"),
        )
        payload["food_code"] = _pick_non_empty(
            payload.get("food_code"),
            payload.get("foodCode"),
            payload.get("식품코드"),
        )
        payload["rawmtrl_nm"] = _pick_non_empty(
            payload.get("rawmtrl_nm"),
            payload.get("raw_materials"),
            payload.get("rawMaterials"),
            payload.get("rawMaterialName"),
            payload.get("RAWMTRL_NM"),
            payload.get("원재료명"),
        )
        payload["manufacturer_name"] = _pick_non_empty(
            payload.get("manufacturer_name"),
            payload.get("manufacturer"),
            payload.get("manufacturerName"),
            payload.get("제조사명"),
            payload.get("BSSH_NM"),
        )
        payload["source"] = _pick_non_empty(payload.get("source"), payload.get("데이터출처"))
        payload["nutrient_text"] = _pick_non_empty(payload.get("nutrient_text"), payload.get("nutrientText"), payload.get("영양성분"))
        payload["allergy_warning"] = _pick_non_empty(payload.get("allergy_warning"), payload.get("allergyWarning"), payload.get("알레르기경고"))
        payload["image_url"] = _pick_non_empty(payload.get("image_url"), payload.get("imageUrl"), payload.get("img_url"), payload.get("이미지URL"))
        payload["clean_product_name"] = _pick_non_empty(payload.get("clean_product_name"), payload.get("cleanProductName"))
        payload["total_weight"] = _pick_non_empty(
            payload.get("total_weight"),
            payload.get("totalWeight"),
            payload.get("식품중량"),
        )
        # SERVING_SIZE is not a generic product/package weight. Preserve it only
        # inside the explicit nutrition-basis contract below.
        payload["weight"] = _pick_non_empty(
            payload.get("weight"),
            payload.get("total_weight"),
            payload.get("totalWeight"),
            payload.get("식품중량"),
        )

        raw_basis = _pick_non_empty(
            payload.get("nutrition_basis"),
            payload.get("nutritionBasis"),
        )
        if isinstance(raw_basis, NutritionBasis):
            basis_payload: Any = raw_basis
        else:
            basis_payload = dict(raw_basis) if isinstance(raw_basis, dict) else {}
            flat_basis_fields = {
                "basis_type": _pick_non_empty(
                    payload.get("nutrition_basis_type"),
                    payload.get("nutritionBasisType"),
                    payload.get("measurement_basis"),
                    payload.get("measurementBasis"),
                    payload.get("NUTRITION_BASIS_TYPE"),
                ),
                "serving_size": _pick_non_empty(
                    payload.get("serving_size"),
                    payload.get("servingSize"),
                    payload.get("SERVING_SIZE"),
                ),
                "serving_size_value": _pick_non_empty(
                    payload.get("serving_size_value"),
                    payload.get("servingSizeValue"),
                ),
                "serving_size_unit": _pick_non_empty(
                    payload.get("serving_size_unit"),
                    payload.get("servingSizeUnit"),
                ),
                "package_size": _pick_non_empty(
                    payload.get("package_size"),
                    payload.get("packageSize"),
                ),
                "package_size_value": _pick_non_empty(
                    payload.get("package_size_value"),
                    payload.get("packageSizeValue"),
                ),
                "package_size_unit": _pick_non_empty(
                    payload.get("package_size_unit"),
                    payload.get("packageSizeUnit"),
                ),
                "servings_per_package": _pick_non_empty(
                    payload.get("servings_per_package"),
                    payload.get("servingsPerPackage"),
                    payload.get("servings_per_container"),
                    payload.get("servingsPerContainer"),
                ),
                "basis_source": _pick_non_empty(
                    payload.get("nutrition_basis_source"),
                    payload.get("nutritionBasisSource"),
                ),
            }
            for key, field_value in flat_basis_fields.items():
                if field_value is not None and key not in basis_payload:
                    basis_payload[key] = field_value
        payload["nutrition_basis"] = basis_payload
        raw_label_provenance = _pick_non_empty(
            payload.get("label_provenance"),
            payload.get("product_label_provenance"),
            payload.get("productLabelProvenance"),
        )
        if raw_label_provenance is None:
            for envelope in reversed(product_envelopes[:-1]):
                raw_label_provenance = _pick_non_empty(
                    envelope.get("label_provenance"),
                    envelope.get("product_label_provenance"),
                    envelope.get("productLabelProvenance"),
                )
                if raw_label_provenance is not None:
                    break
        if raw_label_provenance is None:
            flat_provenance_fields: Dict[str, Any] = {}
            for envelope in reversed(product_envelopes):
                flat_provenance_fields = {
                    key: envelope.get(key)
                    for key in (
                        "label_source_type",
                        "label_source_id",
                        "label_source_record_id",
                        "label_observed_at",
                        "upstream_updated_at",
                        "upstream_revision",
                        "ingested_content_revision_sha256",
                        "content_revision_sha256",
                        "label_covered_fact_groups",
                        "label_lineage_status",
                    )
                    if envelope.get(key) not in (None, "")
                }
                if flat_provenance_fields:
                    break
            raw_label_provenance = flat_provenance_fields or None
        payload["label_provenance"] = raw_label_provenance or {}
        payload["allergy"] = _ensure_list(allergy_value)
        payload["allergy_info"] = _ensure_list(_pick_non_empty(payload.get("allergy_info"), payload.get("allergy_warning"), payload.get("allergyWarning"), allergy_value))
        nutrition_aliases = {
            "kcal": ["kcal", "energy", "energy_kcal", "energyKcal", "에너지", "열량"],
            "protein": ["protein", "protein_g", "proteinG", "단백질"],
            "fat": ["fat", "fat_g", "fatG", "지방"],
            "carbohydrate": ["carbohydrate", "carbs_g", "carbsG", "탄수화물"],
            "sugars": ["sugars", "sugar", "sugar_g", "sugarG", "당류", "당"],
            "sodium": ["sodium", "sodium_mg", "sodiumMg", "나트륨"],
            "saturated_fat": ["saturated_fat", "saturatedFat", "포화지방"],
            "cholesterol": ["cholesterol", "cholesterol_mg", "cholesterolMg", "콜레스테롤"],
        }
        nutrition_units = {
            "kcal": "kcal",
            "protein": "g",
            "fat": "g",
            "carbohydrate": "g",
            "sugars": "g",
            "sodium": "mg",
            "saturated_fat": "g",
            "cholesterol": "mg",
        }
        nutrition_expected_units = {
            alias: nutrition_units[field_name]
            for field_name, aliases in nutrition_aliases.items()
            for alias in aliases
        }
        nutrition = _normalize_numeric_mapping(
            payload.get("nutrition"),
            expected_units=nutrition_expected_units,
        )
        for field_name, aliases in nutrition_aliases.items():
            if field_name in nutrition and nutrition[field_name] not in (None, ""):
                continue
            raw_nutrition_value = _pick_non_empty(
                *(nutrition.get(alias) for alias in aliases),
                *(payload.get(alias) for alias in aliases),
            )
            parsed = _to_float(
                raw_nutrition_value,
                expected_unit=nutrition_units[field_name],
            )
            if parsed is not None:
                nutrition[field_name] = parsed
        for field_name, aliases in nutrition_aliases.items():
            for alias in aliases:
                if alias != field_name:
                    nutrition.pop(alias, None)
        payload["nutrition"] = nutrition
        percent_aliases = {
            "carbohydrate": ["carbs_percent", "carbsPercent"],
            "protein": ["protein_percent", "proteinPercent"],
            "fat": ["fat_percent", "fatPercent"],
            "sodium": ["sodium_percent", "sodiumPercent"],
            "cholesterol": ["cholesterol_percent", "cholesterolPercent"],
        }
        percent_expected_units = {
            alias: "%"
            for field_name, aliases in percent_aliases.items()
            for alias in [field_name, *aliases]
        }
        nutrition_percent = _normalize_numeric_mapping(
            payload.get("nutrition_percent") or payload.get("nutrientPercents"),
            expected_units=percent_expected_units,
        )
        for field_name, aliases in percent_aliases.items():
            if field_name in nutrition_percent and nutrition_percent[field_name] not in (None, ""):
                continue
            parsed = _to_float(
                _pick_non_empty(
                    *(nutrition_percent.get(alias) for alias in aliases),
                    *(payload.get(alias) for alias in aliases),
                ),
                expected_unit="%",
            )
            if parsed is not None:
                nutrition_percent[field_name] = parsed
        # Mass columns are not daily-value percentages, including when a
        # legacy client nests them inside the percentage object.
        payload["nutrition_percent"] = {
            key: value for key, value in nutrition_percent.items()
            if key in percent_aliases
        }
        macro_percent_aliases = {
            "carbohydrate": ["carbs_percent", "carbsPercent"],
            "protein": ["protein_percent", "proteinPercent"],
            "fat": ["fat_percent", "fatPercent"],
        }
        macro_percent = _normalize_numeric_mapping(
            payload.get("macro_percent"),
            expected_units={
                alias: "%"
                for field_name, aliases in macro_percent_aliases.items()
                for alias in [field_name, *aliases]
            },
        )
        for field_name, aliases in macro_percent_aliases.items():
            if field_name in macro_percent and macro_percent[field_name] not in (None, ""):
                continue
            parsed = _to_float(
                _pick_non_empty(
                    *(macro_percent.get(alias) for alias in aliases),
                    *(payload.get(alias) for alias in aliases),
                ),
                expected_unit="%",
            )
            if parsed is not None:
                macro_percent[field_name] = parsed
        payload["macro_percent"] = macro_percent
        if "found" in payload and "product_found" not in payload:
            product_found = _to_bool(payload.get("found"))
            if product_found is not None:
                payload["product_found"] = product_found
        return payload

    @model_validator(mode="after")
    def bind_label_content_revision(self) -> "Product":
        """Bind lineage to normalized facts without inventing source dates."""

        provenance = self.label_provenance
        ingested_revision = provenance.ingested_content_revision_sha256
        computed_revision = product_label_content_revision_sha256(self)
        hash_mismatch = bool(
            ingested_revision and ingested_revision != computed_revision
        )
        required_trace = all(
            getattr(provenance, field) is not None
            for field in (
                "source_type",
                "source_id",
                "source_record_id",
                "label_observed_at",
            )
        )
        all_fact_groups_covered = set(PRODUCT_LABEL_FACT_GROUPS).issubset(
            provenance.covered_fact_groups
        )
        if ingested_revision is None or hash_mismatch:
            # A stored revision that does not describe the normalized facts is
            # missing, stale, or corrupt. Expose the actual content hash but do
            # not use that row for absence, threshold, or comparison claims.
            # Requiring the source record to retain the hash also lets a later
            # database mutation be detected instead of silently blessing a new
            # revision at read time.
            lineage_status = "unavailable"
        elif provenance.lineage_status == "complete":
            lineage_status = (
                "complete"
                if required_trace and all_fact_groups_covered
                else "partial"
                if required_trace or provenance.covered_fact_groups
                else "unavailable"
            )
        elif provenance.lineage_status == "partial":
            lineage_status = (
                "partial"
                if required_trace or provenance.covered_fact_groups
                else "unavailable"
            )
        else:
            lineage_status = "unavailable"

        object.__setattr__(
            self,
            "label_provenance",
            provenance.model_copy(update={
                "content_revision_sha256": computed_revision,
                "content_revision_binding_status": (
                    "ingested_revision_missing"
                    if ingested_revision is None
                    else "ingested_revision_mismatch"
                    if hash_mismatch
                    else "matched_ingested_record"
                ),
                "lineage_status": lineage_status,
            }),
        )
        return self

    @model_serializer(mode="wrap")
    def serialize_with_effective_label_revision(self, serializer):
        """Never serialize a stale hash as complete/partial lineage."""

        payload = serializer(self)
        provenance = payload.get("label_provenance")
        if isinstance(provenance, dict):
            provenance["content_revision_sha256"] = (
                product_label_content_revision_sha256(self)
            )
            provenance["content_revision_binding_status"] = (
                content_revision_binding_status(self)
            )
            provenance["lineage_status"] = effective_label_lineage_status(self)
        return payload

    def resolved_allergies(self) -> List[str]:
        return list(dict.fromkeys([
            *self.allergy_info,
            *self.allergy,
        ]))

    def ingredient_text_sources(self) -> List[str]:
        items = list(self.ingredients)
        if self.rawmtrl_nm:
            items.append(self.rawmtrl_nm)
        return [item for item in items if item]


class ChatHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: ChatHistoryContent


class BackendChatHistoryItem(ChatHistoryItem):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_V2_HISTORY_CONTENT)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Optional[Profile] = None
    profiles: List[Profile] = Field(default_factory=list, max_length=MAX_V2_PROFILES)
    analysis_context: Optional[BackendAnalysisContext] = Field(
        default=None, validation_alias=AliasChoices("analysisContext", "analysis_context"),
    )
    product: Optional[Product] = None
    raw_profile: Optional[Dict[str, Any]] = None
    raw_profiles: List[Dict[str, Any]] = Field(default_factory=list, max_length=MAX_ACTIVE_PROFILES)
    raw_product: Optional[Dict[str, Any]] = None
    barcode: Optional[str] = None
    report_number: Optional[str] = None
    conversation_state: Dict[str, Any] = Field(default_factory=dict)
    chat_history: List[ChatHistoryItem | BackendChatHistoryItem] = Field(default_factory=list, max_length=MAX_CHAT_HISTORY_ITEMS)
    conversation_id: Optional[UUID] = None
    turn_id: Optional[UUID] = None
    message: str = Field(max_length=MAX_CHAT_MESSAGE_LENGTH)

    @model_validator(mode="before")
    @classmethod
    def normalize_nullable_backend_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        # Consent to disclose health/chat context to an external AI is accepted
        # only through the API's trusted-backend HMAC assertion. Silently remove
        # common client/body flags so they can neither grant permission nor turn
        # a consent-less request into an error instead of a rule-only answer.
        for untrusted_consent_field in (
            "external_ai_consent",
            "externalAiConsent",
            "allow_external_ai",
            "allowExternalAi",
        ):
            payload.pop(untrusted_consent_field, None)
        for key in ["profiles", "raw_profiles", "chat_history"]:
            if payload.get(key) is None:
                payload[key] = []

        if payload.get("analysisContext") is not None or payload.get("analysis_context") is not None:
            profile_items = payload.get("profiles")
            for p in [payload.get("profile"), *(profile_items if isinstance(profile_items, list) else [])]:
                if isinstance(p, dict) and "profile_id" in p and type(p["profile_id"]) is not int:
                    raise ValueError("v2 profile_id must be an integer")
                if isinstance(p, dict) and p.get("id") is not None:
                    key = p.get("profileKey", p.get("profile_key", ""))
                    if not isinstance(key, str) or not key.startswith("self:") or type(p["id"]) is not int or str(p["id"]) != key.split(":", 1)[1]:
                        raise ValueError("v2 id is only the self account identifier")

        raw_profile_payloads = []
        if payload.get("raw_profile") is not None:
            raw_profile_payloads.append(payload["raw_profile"])
        raw_profiles = payload.get("raw_profiles")
        if isinstance(raw_profiles, list):
            raw_profile_payloads.extend(raw_profiles)

        combined_raw_profile_length = 0
        for index, raw_profile in enumerate(raw_profile_payloads):
            if not isinstance(raw_profile, dict):
                continue
            payload_length = _json_text_length(raw_profile)
            if payload_length > MAX_RAW_PROFILE_PAYLOAD_LENGTH:
                raise ValueError(
                    f"raw profile[{index}] exceeds "
                    f"{MAX_RAW_PROFILE_PAYLOAD_LENGTH} characters"
                )
            combined_raw_profile_length += payload_length
        if combined_raw_profile_length > MAX_COMBINED_RAW_PROFILE_PAYLOAD_LENGTH:
            raise ValueError(
                "combined raw profile payload exceeds "
                f"{MAX_COMBINED_RAW_PROFILE_PAYLOAD_LENGTH} characters"
            )

        payload["conversation_state"] = _sanitize_conversation_state(
            payload.get("conversation_state")
        )
        return payload

    @model_validator(mode="after")
    def validate_client_context_boundaries(self):
        supplied_profile_count = (
            (1 if self.profile is not None else 0)
            + len(self.profiles)
            + (1 if self.raw_profile is not None else 0)
            + len(self.raw_profiles)
        )
        max_profiles = MAX_V2_PROFILES if self.analysis_context else MAX_ACTIVE_PROFILES
        if supplied_profile_count > max_profiles:
            raise ValueError(
                "combined profile inputs exceed "
                f"{max_profiles} profiles"
            )

        validated_profiles = [
            profile
            for profile in [self.profile, *self.profiles]
            if profile is not None
        ]
        for raw_profile in [
            *([self.raw_profile] if self.raw_profile is not None else []),
            *self.raw_profiles,
        ]:
            validated_profiles.append(Profile.model_validate(raw_profile))

        combined_profile_text_length = 0
        for profile in validated_profiles:
            combined_profile_text_length += sum(len(value) for value in [
                profile.profile_name,
                profile.email or "",
                profile.nickname or "",
                profile.target_type,
                *profile.resolved_allergies(),
                *profile.conditions,
                *profile.resolved_special_diets(),
                *profile.disliked_ingredients,
            ])
        if combined_profile_text_length > MAX_COMBINED_PROFILE_TEXT_LENGTH:
            raise ValueError(
                "combined profile text exceeds "
                f"{MAX_COMBINED_PROFILE_TEXT_LENGTH} characters"
            )

        history_text_length = sum(
            len(item.content)
            for item in self.chat_history
        )
        history_limit = MAX_V2_HISTORY_TOTAL if self.analysis_context else MAX_CHAT_HISTORY_TOTAL_LENGTH
        content_limit = MAX_V2_HISTORY_CONTENT if self.analysis_context else MAX_CHAT_HISTORY_CONTENT_LENGTH
        if any(len(item.content) > content_limit for item in self.chat_history):
            raise ValueError(f"chat_history content exceeds {content_limit} characters")
        if self.analysis_context and len(self.chat_history) > MAX_V2_HISTORY_ITEMS:
            raise ValueError(f"v2 chat_history exceeds {MAX_V2_HISTORY_ITEMS} items")
        if history_text_length > history_limit:
            raise ValueError(
                "chat_history text exceeds "
                f"{history_limit} characters"
            )
        if history_text_length + len(self.message) > history_limit + MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError(
                "message and chat_history text exceed "
                f"{MAX_CHAT_CONTEXT_TOTAL_LENGTH} characters"
            )

        for previous, current in zip(self.chat_history, self.chat_history[1:]):
            if previous.role == current.role and self.analysis_context is None:
                raise ValueError(
                    "chat_history roles must alternate between user and assistant"
                )

        if self.analysis_context is not None:
            if not self.message.strip():
                raise ValueError("v2 message must not be blank")
            if self.raw_profile is not None or self.raw_profiles or self.raw_product is not None:
                raise ValueError("v2 accepts only typed profile/profiles/product")
            if self.profile is not None and self.profiles:
                raise ValueError("v2 uses profile for one person or profiles for multiple people")
            if len(self.profiles) == 1:
                raise ValueError("v2 single person must use profile")
            validate_request_context(self.analysis_context, validated_profiles, self.product)
            if self.product is not None:
                # Reject unrenderable backend snapshots at the request boundary,
                # never truncate label facts or crash during response encoding.
                from app.core.public_product import public_product_payload
                public_product_payload(self.product)
            if self.product and self.barcode and self.product.barcode != self.barcode:
                raise ValueError("barcode disagrees with v2 product")
            if self.product is None and (self.barcode or self.report_number):
                raise ValueError("v2 identifiers require backend product resolution")
        elif any(p.profile_key is not None or p.profile_source is not None for p in validated_profiles):
            raise ValueError("profileKey/profileSource require analysisContext")

        if self.conversation_id is not None:
            if self.turn_id is None:
                raise ValueError(
                    "turn_id is required for persisted conversation requests"
                )
            if self.chat_history:
                raise ValueError(
                    "chat_history cannot be supplied with a server conversation"
                )
            if self.conversation_state:
                raise ValueError(
                    "conversation_state cannot be supplied with a server conversation"
                )
        elif self.turn_id is not None:
            raise ValueError(
                "turn_id requires conversation_id"
            )
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fingerprint(self, serializer):
        payload = serializer(self)
        if self.analysis_context is None:
            payload.pop("analysis_context", None)
        return payload


ConversationTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CONVERSATION_TITLE_LENGTH,
    ),
]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The authenticated backend generates this UUID. Using the resource ID as the
    # create idempotency key prevents duplicate empty rooms on network retries.
    conversation_id: UUID
    title: Optional[ConversationTitle] = None


class ConversationRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: ConversationTitle


class SafetyGuidance(BaseModel):
    """User-facing actions that must remain structured in every UI."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    urgency: Literal["routine", "urgent", "emergency"]
    headline: str = Field(min_length=1, max_length=300)
    actions: List[ChatResponseReason] = Field(min_length=1, max_length=8)
    emergency_contact: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[0-9+()\- ]+$",
    )


class SourceDetail(BaseModel):
    """A public, independently verifiable source behind safety guidance."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=200)
    url: str = Field(
        min_length=1,
        max_length=2048,
        pattern=r"^https://[^\s]+$",
    )
    reviewed_at: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    source_id: Optional[str] = Field(
        default=None,
        max_length=200,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    claim_scope: Optional[str] = Field(default=None, min_length=1, max_length=500)
    review_due: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class PolicyEvidence(BaseModel):
    """A typed link between one applied policy rule and its support."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    evidence_id: str = Field(min_length=1, max_length=200)
    rule_id: str = Field(
        max_length=200,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    claim_scope: str = Field(min_length=1, max_length=500)
    evidence_type: Literal[
        "official_guidance", "service_safety_routing", "service_heuristic",
        "unsupported_boundary", "label_fact_match"
    ]
    source_id: Optional[str] = Field(
        default=None,
        max_length=200,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    support_level: Literal["direct", "conservative_adaptation", "internal_heuristic"]
    reviewed_at: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    review_due: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class AssessmentSummary(BaseModel):
    """UI-safe meaning of a result without pretending to be a probability."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    scope: Literal[
        "emergency_guidance",
        "recipe",
        "comparison",
        "overall_intake",
        "ingredient",
        "nutrition",
        "product_information",
        "product_selection",
        "general_guidance",
        "unsupported",
        "unknown",
    ]
    decision: Literal[
        "urgent_action",
        "avoid",
        "caution",
        "no_conflict_found",
        "insufficient_information",
        "not_assessed",
    ]
    evidence_status: Literal[
        "sufficient_for_scope",
        "limited",
        "insufficient",
        "not_applicable",
    ]
    safety_assessed: bool
    personalized: bool
    repository_identity_matched: Optional[bool] = None
    label_lineage_status: Optional[
        Literal["complete", "partial", "unavailable"]
    ] = None
    label_freshness_status: Optional[Literal[
        "within_policy",
        "stale",
        "future_observation",
        "source_not_eligible",
        "source_registry_configuration_invalid",
        "invalid_temporal_order",
        "policy_configuration_invalid",
        "unavailable",
    ]] = None
    product_data_verified: Optional[bool] = None
    explanation: str = Field(min_length=1, max_length=800)
    missing_information: List[ChatResponseReason] = Field(
        default_factory=list,
        max_length=20,
    )


class ProductDataTrust(BaseModel):
    """Public trust summary containing no source-record identifiers."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    repository_identity_matched: bool
    label_lineage_status: Literal["complete", "partial", "unavailable"]
    label_freshness_status: Literal[
        "within_policy",
        "stale",
        "future_observation",
        "source_not_eligible",
        "source_registry_configuration_invalid",
        "invalid_temporal_order",
        "policy_configuration_invalid",
        "unavailable",
    ]
    label_observed_at: Optional[datetime] = None
    content_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_revision_binding_status: Literal[
        "matched_ingested_record",
        "ingested_revision_missing",
        "ingested_revision_mismatch",
    ]
    content_revision_meaning: Literal[
        "normalized_snapshot_identifier_not_freshness_proof"
    ] = "normalized_snapshot_identifier_not_freshness_proof"


class ActivityProductLabelTrace(BaseModel):
    """Identifier-free product snapshot facts retained for response auditing.

    The trace exists only when a repository-verified product snapshot was
    actually attached to the response.  Source-record identifiers and the
    product's display/lookup identity deliberately remain outside this block.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_identity_matched: Literal[True]
    label_lineage_status: Literal["complete", "partial", "unavailable"]
    label_freshness_status: Literal[
        "within_policy",
        "stale",
        "future_observation",
        "source_not_eligible",
        "source_registry_configuration_invalid",
        "invalid_temporal_order",
        "policy_configuration_invalid",
        "unavailable",
    ]
    label_observed_at: Optional[datetime] = None
    content_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_revision_binding_status: Literal[
        "matched_ingested_record",
        "ingested_revision_missing",
        "ingested_revision_mismatch",
    ]
    nutrition_basis_type: Literal[
        "unknown",
        "per_serving",
        "per_100g",
        "per_100ml",
        "per_package",
    ]

    @model_validator(mode="after")
    def require_unambiguous_observation_time(self) -> "ActivityProductLabelTrace":
        observed_at = self.label_observed_at
        if observed_at is None:
            return self
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError(
                "label_observed_at must include an explicit UTC offset"
            )
        object.__setattr__(
            self,
            "label_observed_at",
            observed_at.astimezone(timezone.utc),
        )
        return self


class ExternalAIProcessingSummary(BaseModel):
    """Identity-free disclosure status for the current/generated answer."""

    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    allowed_for_request: bool = False
    used: bool = False
    status: Literal[
        "used",
        "allowed_not_used",
        "operator_disabled",
        "consent_denied",
        "consent_not_verified",
        "not_evaluated",
    ] = "not_evaluated"
    services: List[Literal["llm", "rag"]] = Field(default_factory=list, max_length=2)


class RecipeIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]
    amount: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]


class RecipeResult(BaseModel):
    """Structured recipe data that the app can render without parsing prose."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    servings: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ]
    product_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]
    product_barcode: Optional[Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]] = None
    source_product_blocked: bool = False
    ingredients: List[RecipeIngredient] = Field(min_length=1, max_length=30)
    steps: List[Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    ]] = Field(min_length=1, max_length=20)
    substitutions: List[ChatResponseReason] = Field(default_factory=list, max_length=10)
    applied_profiles: List[ProfileName] = Field(default_factory=list, max_length=MAX_V2_PROFILES)
    applied_diets: List[ProfileListItem] = Field(default_factory=list, max_length=MAX_PROFILE_LIST_ITEMS)
    requested_diets: List[ProfileListItem] = Field(default_factory=list, max_length=MAX_PROFILE_LIST_ITEMS)
    diet_limitations: List[ChatResponseReason] = Field(default_factory=list, max_length=10)
    allergen_check: List[ChatResponseReason] = Field(min_length=1, max_length=MAX_V2_PROFILES)
    packaged_item_cautions: List[ChatResponseReason] = Field(default_factory=list, max_length=10)


class ChatResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    # A server-generated, non-semantic identifier lets clients report one exact
    # answer without copying the user's question or the assistant's text into
    # operational records. Saved conversations replace this value with the
    # persisted assistant message UUID before returning the response.
    response_id: ChatResponseIdentifier = Field(default_factory=lambda: str(uuid4()))
    generated_at: ChatResponseTimestamp = Field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    task_type: ChatResponseCode = "general_chat"
    answer_source: ChatResponseCode = "rules"
    pipeline_stage: ChatResponseCode = Field(
        default="rules", json_schema_extra={"deprecated": True},
    )
    intent: ChatResponseCode
    answer: str = Field(max_length=MAX_CHAT_ANSWER_LENGTH)
    risk_level: Literal["low", "medium", "high", "unknown"]
    # Independent UI-facing axes. ``risk_level`` remains temporarily for
    # backward compatibility, but must not be used to infer these meanings.
    overall_decision: Literal[
        "avoid", "caution", "check_required", "no_known_conflict"
    ] = "check_required"
    allergy_status: Literal[
        "conflict", "cross_contact", "no_known_match", "unknown"
    ] = "unknown"
    diet_match: Literal["match", "caution", "not_match", "unknown"] = "unknown"
    preference_match: Literal[
        "matched", "disliked_found", "unknown"
    ] = "unknown"
    nutrition_facts: PublicNutritionFacts = Field(
        default_factory=PublicNutritionFacts
    )
    missing_information: List[ChatResponseReason] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_AUXILIARY_REASONS,
    )
    assessment_status: Literal["complete", "incomplete", "unsupported"] = "complete"
    # Backward-compatible field. This has historically represented product-data
    # completeness, not a calibrated probability that the answer is correct.
    # New clients should render data_completeness and assessment_summary instead.
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Deprecated completeness heuristic; not medical-answer confidence."
        ),
        json_schema_extra={"deprecated": True},
    )
    confidence_reasons: List[ChatResponseReason] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_AUXILIARY_REASONS,
    )
    data_completeness: Optional[float] = Field(default=None, ge=0, le=1)
    data_completeness_reasons: List[ChatResponseReason] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_AUXILIARY_REASONS,
    )
    assessment_summary: Optional[AssessmentSummary] = None
    product_data_trust: Optional[ProductDataTrust] = None
    reasons: List[ChatResponseReason] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_REASONS,
    )
    profile_name: Optional[ProfileName] = None
    active_profile_names: List[ProfileName] = Field(
        default_factory=list,
        max_length=MAX_V2_PROFILES,
    )
    per_profile_results: List[PublicPerProfileResult] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PROFILE_RESULTS,
    )
    backend_analysis: Optional[PublicBackendAnalysis] = None
    current_product: Optional[PublicProduct] = None
    # Server-owned lookup reference for a saved response. It is persisted
    # outside the public payload and is never serialized to clients.
    # Keeping it separate lets ``current_product`` omit the internal surrogate
    # product ID and other repository-only fields.
    product_reference: Optional[Dict[str, Any]] = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    # Full source lineage used only to create/verify a server-owned saved
    # snapshot. A separate private field keeps source-record IDs out of both
    # runtime JSON and the public OpenAPI schema for ProductDataTrust.
    product_snapshot_provenance: Optional[ProductLabelProvenance] = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    product_snapshot_nutrition_basis: Optional[NutritionBasis] = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    conversation_state: PublicConversationState = Field(
        default_factory=PublicConversationState
    )
    # Saved rooms can retain a repository-only product ID without returning it
    # through the stateless/public conversation-state contract.
    internal_conversation_state: Dict[str, Any] = Field(
        default_factory=dict,
        exclude=True,
        repr=False,
    )
    conversation_id: Optional[ChatResponseIdentifier] = None
    conversation_title: Optional[ConversationTitle] = None
    conversation_revision: Optional[int] = Field(
        default=None,
        ge=0,
        le=9_223_372_036_854_775_807,
    )
    turn_id: Optional[ChatResponseIdentifier] = None
    recommended_questions: List[ChatResponseRecommendedQuestion] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_RECOMMENDED_QUESTIONS,
    )
    product_candidates: List[PublicProductCandidateCard] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PRODUCT_CANDIDATES,
    )
    recommended_products: List[PublicRecommendedProductCard] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_RECOMMENDED_PRODUCTS,
    )
    recipe: Optional[RecipeResult] = None
    comparison_products: List[PublicProduct] = Field(default_factory=list, max_length=3)
    ui_guidance: Optional[PublicUiGuidance] = None
    sources: List[ChatResponseSource] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_SOURCES,
    )
    safety_guidance: Optional[SafetyGuidance] = None
    source_details: List[SourceDetail] = Field(
        default_factory=list, max_length=10,
        json_schema_extra={"deprecated": True},
    )
    policy_version: Optional[ChatResponseCode] = Field(
        default=None, json_schema_extra={"deprecated": True},
    )
    applied_rule_ids: List[ChatResponseCode] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_RULE_IDS,
        json_schema_extra={"deprecated": True},
    )
    evidence: List[PolicyEvidence] = Field(
        default_factory=list, max_length=50,
        json_schema_extra={"deprecated": True},
    )
    provenance_status: Literal[
        "complete", "partial", "unavailable", "legacy_unavailable"
    ] = "legacy_unavailable"
    # Exact retrieved text used only by the in-process final grounding pass.
    # It must never become part of the public API response or stored activity.
    grounding_context: List[ChatResponseGroundingEntry] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_GROUNDING_ENTRIES,
        exclude=True,
        repr=False,
    )
    fallback_reason: Optional[ChatResponseCode] = Field(
        default=None, json_schema_extra={"deprecated": True},
    )
    validation_failure_types: List[ChatResponseCode] = Field(
        default_factory=list,
        max_length=MAX_CHAT_RESPONSE_VALIDATION_FAILURE_TYPES,
        json_schema_extra={"deprecated": True},
    )
    critical_safety_violation: bool = Field(
        default=False, json_schema_extra={"deprecated": True},
    )
    blocked_llm_output_stored: bool = Field(
        default=False, json_schema_extra={"deprecated": True},
    )
    # Lets the UI say whether this answer sent user context to an external AI.
    # It contains no user id, signature, provider secret, or failure detail.
    external_ai_processing: ExternalAIProcessingSummary = Field(
        default_factory=ExternalAIProcessingSummary
    )

    @model_validator(mode="after")
    def preserve_legacy_profile_limit(self):
        if self.backend_analysis is None and not backend_analysis_active.get():
            if max(len(self.active_profile_names), len(self.per_profile_results)) > MAX_ACTIVE_PROFILES:
                raise ValueError("legacy responses support at most 20 profiles")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ):
        """Return a fully revalidated copy, including excluded server fields."""

        copied = super().model_copy(update=dict(update or {}), deep=deep)
        return type(self).model_validate(dict(copied.__dict__))

    @model_serializer(mode="wrap")
    def serialize_with_closed_nested_contracts(self, serializer):
        """Revalidate the entire response after any unsafe base ``model_copy``.

        Pydantic deliberately does not validate values supplied through
        ``BaseModel.model_copy(update=...)``.  Validate every field here so a
        malformed scalar, oversized list, or pre-mutated nested model cannot
        cross either the HTTP or saved-snapshot serialization boundary.
        """

        validated = type(self).model_validate(dict(self.__dict__))
        return serializer(validated)


class ConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    conversation_id: ChatResponseIdentifier
    title: ConversationTitle
    title_manually_set: bool = False
    revision: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)
    created_at: ChatResponseTimestamp
    updated_at: ChatResponseTimestamp
    expires_at: Optional[ChatResponseTimestamp] = None


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        revalidate_instances="always",
    )

    message_id: ChatResponseIdentifier
    sequence: int = Field(ge=1, le=MAX_PUBLIC_CONVERSATION_SEQUENCE)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CHAT_ANSWER_LENGTH)
    turn_id: Optional[ChatResponseIdentifier] = None
    created_at: ChatResponseTimestamp
    # Assistant rows retain the structured public response so a reopened room can
    # restore product cards, risk badges, and follow-up buttons as well as text.
    response: Optional[ChatResponse] = None


class ConversationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: List[ConversationSummaryResponse] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_CONVERSATION_LIST_ITEMS,
    )
    next_cursor: Optional[str] = Field(
        default=None,
        max_length=MAX_PUBLIC_CONVERSATION_CURSOR_LENGTH,
    )


class ConversationDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: ConversationSummaryResponse
    messages: List[ConversationMessageResponse] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_CONVERSATION_MESSAGE_ITEMS,
    )
    next_cursor: Optional[str] = Field(
        default=None,
        max_length=MAX_PUBLIC_CONVERSATION_CURSOR_LENGTH,
    )


class ActivityRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user_id: Optional[int] = None
    barcode: Optional[str] = None
    report_number: Optional[str] = None
    product_id: Optional[int] = None
    action_type: str = "chat"
    response_id: Optional[str] = None
    external_ai_used: bool = False
    external_ai_services: List[Literal["llm", "rag"]] = Field(
        default_factory=list,
        max_length=2,
    )
    policy_version: Optional[str] = None
    applied_rule_ids: List[str] = Field(default_factory=list, max_length=50)
    source_ids: List[str] = Field(default_factory=list, max_length=50)
    response_product_label_trace: Optional[ActivityProductLabelTrace] = None
    message: Optional[str] = None
    intent: Optional[str] = None
    answer_source: Optional[str] = None
    pipeline_stage: Optional[str] = None
    risk_level: Optional[str] = None
    confidence: Optional[float] = None
    confidence_reasons: List[str] = Field(default_factory=list)
    fallback_reason: Optional[str] = None
    validation_failure_types: List[str] = Field(default_factory=list)
    critical_safety_violation: bool = False
    blocked_llm_output_stored: bool = False
    created_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_activity_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        payload["user_id"] = _pick_non_empty(payload.get("user_id"), payload.get("userId"))
        payload["report_number"] = _pick_non_empty(payload.get("report_number"), payload.get("reportNo"), payload.get("report_no"))
        payload["action_type"] = _pick_non_empty(payload.get("action_type"), payload.get("actionType"), "chat")
        payload["created_at"] = _pick_non_empty(payload.get("created_at"), payload.get("createdAt"))
        payload["validation_failure_types"] = _ensure_list(payload.get("validation_failure_types") or payload.get("validationFailureTypes"))
        payload["applied_rule_ids"] = _ensure_list(payload.get("applied_rule_ids") or payload.get("appliedRuleIds"))
        payload["source_ids"] = _ensure_list(payload.get("source_ids") or payload.get("sourceIds"))
        critical_safety_violation = _to_bool(_pick_non_empty(payload.get("critical_safety_violation"), payload.get("criticalSafetyViolation")))
        if critical_safety_violation is not None:
            payload["critical_safety_violation"] = critical_safety_violation
        blocked_llm_output_stored = _to_bool(_pick_non_empty(payload.get("blocked_llm_output_stored"), payload.get("blockedLlmOutputStored")))
        if blocked_llm_output_stored is not None:
            payload["blocked_llm_output_stored"] = blocked_llm_output_stored
        return payload
