"""Closed, bounded schemas for public non-product API payloads.

The service used to expose a few nested dictionaries whose keys were only
constrained by convention.  Keeping these models separate from the internal
conversation repository types makes it impossible to add a server-only field
to the public contract by accident.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import (
    ConfigDict,
    Field,
    StringConstraints,
    model_serializer,
    model_validator,
)

from app.core.public_product_schema import PublicPayloadModel, PublicProduct
from app.core.backend_analysis import ProfileKey, ProfileSource, SafetyStatus, DietStatus


MAX_PUBLIC_STATE_TEXT_LENGTH = 256
MAX_PUBLIC_PROFILE_RESULTS = 21
MAX_PUBLIC_PROFILE_NAME_LENGTH = 128
MAX_PUBLIC_TARGET_TYPE_LENGTH = 32
MAX_PUBLIC_INTENT_LENGTH = 128
MAX_PUBLIC_PROFILE_ANSWER_LENGTH = 16_000
MAX_PUBLIC_PROFILE_REASONS = 50
MAX_PUBLIC_REASON_LENGTH = 1_000
MAX_PUBLIC_PRODUCT_IDENTIFIER_LENGTH = 256
MAX_PUBLIC_PENDING_MESSAGE_LENGTH = 4_000


PublicStateText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_STATE_TEXT_LENGTH,
    ),
]
PublicProfileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PROFILE_NAME_LENGTH,
    ),
]
PublicTargetType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_TARGET_TYPE_LENGTH,
    ),
]
PublicIntent = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_INTENT_LENGTH,
    ),
]
PublicProfileAnswer = Annotated[
    str,
    StringConstraints(max_length=MAX_PUBLIC_PROFILE_ANSWER_LENGTH),
]
PublicReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_REASON_LENGTH,
    ),
]
PublicProductIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PRODUCT_IDENTIFIER_LENGTH,
    ),
]


class PublicPerProfileResult(PublicPayloadModel):
    """One bounded, UI-safe result in a multi-profile answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: int = Field(ge=0, le=9_223_372_036_854_775_807)
    profile_name: PublicProfileName
    target_type: PublicTargetType
    profileKey: Optional[ProfileKey] = None
    profileSource: Optional[ProfileSource] = None
    backend_status: Optional[SafetyStatus] = None
    backend_diet_status: Optional[DietStatus] = None
    intent: PublicIntent
    risk_level: Literal["low", "medium", "high", "unknown"]
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
    assessment_status: Literal["complete", "incomplete", "unsupported"]
    answer: PublicProfileAnswer
    reasons: list[PublicReason] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PROFILE_REASONS,
    )

    @model_serializer(mode="wrap")
    def omit_absent_backend_fields(self, serializer):
        payload = serializer(self)
        for key in ("profileKey", "profileSource", "backend_status", "backend_diet_status"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload


class PublicUiGuidance(PublicPayloadModel):
    """A bounded UI action hint; clients own the actual navigation route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["UPDATE_PROFILE_ALLERGIES"]
    message: PublicReason
    profile_id: Optional[int] = Field(
        default=None,
        ge=0,
        le=9_223_372_036_854_775_807,
    )
    profile_name: Optional[PublicProfileName] = None


class PublicConversationProductReference(PublicPayloadModel):
    """Public label identifiers allowed to round-trip through stateless chat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_name: Optional[PublicProductIdentifier] = None
    barcode: Optional[PublicProductIdentifier] = None
    report_number: Optional[PublicProductIdentifier] = None

    @model_validator(mode="after")
    def require_authoritative_public_identifier(self):
        if self.barcode is None and self.report_number is None:
            raise ValueError("barcode or report_number is required")
        return self

    @model_serializer(mode="wrap")
    def serialize_without_empty_optionals(self, serializer):
        return {
            key: value
            for key, value in serializer(self).items()
            if value is not None
        }


class PublicVegetarianAllowance(PublicPayloadModel):
    profile_key: PublicStateText
    dairy: Optional[bool] = None
    egg: Optional[bool] = None
    profile_signature: Optional[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = None
    dairy_profile_signature: Optional[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = None
    egg_profile_signature: Optional[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = None
    dairy_pending: Optional[bool] = None
    egg_pending: Optional[bool] = None


class PublicPendingAllergy(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    confirmed: list[PublicStateText] = Field(default_factory=list, max_length=32)
    uncertain: list[PublicStateText] = Field(default_factory=list, max_length=32)
    unsupported_confirmed: list[PublicStateText] = Field(default_factory=list, max_length=32)
    unsupported_uncertain: list[PublicStateText] = Field(default_factory=list, max_length=32)
    unspecified: bool = False
    owner_key: Optional[PublicStateText] = None


class PublicConversationState(PublicPayloadModel):
    """Closed public state; repository IDs and product facts are unrepresentable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Optional[Literal[1]] = None
    last_intent: Optional[PublicStateText] = None
    last_task_type: Optional[PublicStateText] = None
    last_confirmed_product: Optional[PublicConversationProductReference] = None
    pending_product_candidates: list[PublicConversationProductReference] = Field(
        default_factory=list,
        max_length=3,
    )
    # Kept only while the user is choosing or identifying a requested product. This is
    # the user's own bounded question, not a product fact or server credential.
    pending_user_message: Optional[Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=MAX_PUBLIC_PENDING_MESSAGE_LENGTH,
        ),
    ]] = None
    pending_intent: Optional[PublicStateText] = None
    unresolved_product_query: Optional[PublicStateText] = None
    recipe_fingerprints: list[Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]] = Field(default_factory=list, max_length=20)
    recipe_requested_diets: list[Literal["채식", "저염식", "저당식", "글루텐프리", "저칼로리", "저지방", "고단백"]] = Field(default_factory=list, max_length=7)
    recipe_goals_initialized: Optional[Literal[True]] = None
    recipe_receipt: Optional[Annotated[str, StringConstraints(max_length=3072, pattern=r"^[A-Za-z0-9_=\-]+\.[0-9a-f]{64}$")]] = None
    recipe_product: Optional[PublicConversationProductReference] = None
    pending_target_product: Optional[PublicConversationProductReference] = None
    vegetarian_allowances: list[PublicVegetarianAllowance] = Field(default_factory=list, max_length=20)
    pending_allergy: Optional[PublicPendingAllergy] = None
    comparison_products: list[Optional[PublicConversationProductReference]] = Field(default_factory=list, max_length=3)
    comparison_queries: list[PublicStateText] = Field(default_factory=list, max_length=3)
    comparison_pending_index: Optional[int] = Field(default=None, ge=0, le=2)
    comparison_status: Optional[Literal["active", "pending", "unresolved", "cleared"]] = None
    comparison_axes: list[Literal["칼로리", "단백질", "지방", "포화지방", "탄수화물", "당류", "나트륨", "콜레스테롤", "알레르기"]] = Field(default_factory=list, max_length=9)

    @model_validator(mode="before")
    @classmethod
    def normalize_empty_and_versioned_state(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        semantic_keys = {
            "last_intent",
            "last_task_type",
            "last_confirmed_product",
            "pending_product_candidates",
            "pending_user_message",
            "pending_intent",
            "unresolved_product_query",
            "recipe_fingerprints",
            "recipe_requested_diets", "recipe_goals_initialized", "recipe_product", "vegetarian_allowances",
            "pending_allergy",
            "pending_target_product",
            "recipe_receipt",
            "comparison_products", "comparison_queries", "comparison_pending_index", "comparison_status", "comparison_axes",
        }
        has_semantic_value = any(
            payload.get(key) not in (None, "", []) for key in semantic_keys
        )
        if has_semantic_value:
            payload.setdefault("version", 1)
        elif set(payload).issubset({"version"}):
            # Preserve the long-standing empty-state wire representation: {}.
            payload = {}
        return payload

    @model_serializer(mode="wrap")
    def serialize_without_empty_optionals(self, serializer):
        payload = serializer(self)
        return {
            key: value
            for key, value in payload.items()
            if value is not None and (value != [] or (
                key == "recipe_requested_diets" and self.recipe_goals_initialized
            ) or (
                key in {"comparison_products", "comparison_queries"} and self.comparison_status
            ))
        }


class ProductLabelMismatchReportRequest(PublicPayloadModel):
    """A deliberately small report: no chat text or free-form health data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: Literal[
        "product_name",
        "ingredients",
        "allergens",
        "nutrition",
        "other_label_information",
    ]


class ProductLabelMismatchReportResponse(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True
    barcode: PublicProductIdentifier
    field: Literal[
        "product_name",
        "ingredients",
        "allergens",
        "nutrition",
        "other_label_information",
    ]


class ConversationDeleteAllResponse(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    deleted: int = Field(ge=0)


__all__ = [
    "ConversationDeleteAllResponse",
    "MAX_PUBLIC_PROFILE_RESULTS",
    "ProductLabelMismatchReportRequest",
    "ProductLabelMismatchReportResponse",
    "PublicConversationProductReference",
    "PublicConversationState",
    "PublicPerProfileResult",
    "PublicProductIdentifier",
]
