"""Dependency-free closed schemas for every public product response surface."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


MAX_PUBLIC_PRODUCT_NAME_LENGTH = 512
MAX_PUBLIC_PRODUCT_IDENTIFIER_LENGTH = 256
MAX_PUBLIC_PRODUCT_SHORT_TEXT_LENGTH = 512
MAX_PUBLIC_PRODUCT_LONG_TEXT_LENGTH = 8_192
MAX_PUBLIC_IMAGE_URL_LENGTH = 2_048
MAX_PUBLIC_PRODUCT_LIST_ITEMS = 128
MAX_PUBLIC_PRODUCT_LIST_ITEM_LENGTH = 512
MAX_PUBLIC_CARD_REASON_LENGTH = 4_000
MAX_PUBLIC_CARD_REASONS = 16
MAX_PUBLIC_PRODUCT_CANDIDATES = 3
MAX_PUBLIC_RECOMMENDED_PRODUCTS = 10
MAX_PUBLIC_RECOMMENDATION_PROFILE_ITEMS = 32
MAX_PUBLIC_COMPARISON_ITEMS = 8
MAX_PUBLIC_RULE_IDS = 20
MAX_PUBLIC_NUTRITION_VALUE = 1_000_000_000.0
MAX_PUBLIC_PERCENT_VALUE = 1_000_000.0
MAX_PUBLIC_SERVINGS_PER_PACKAGE = 1_000_000.0


PublicProductName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PRODUCT_NAME_LENGTH,
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
PublicProductShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PRODUCT_SHORT_TEXT_LENGTH,
    ),
]
PublicProductLongText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PRODUCT_LONG_TEXT_LENGTH,
    ),
]
PublicImageUrl = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_IMAGE_URL_LENGTH,
    ),
]
PublicProductListItem = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_PRODUCT_LIST_ITEM_LENGTH,
    ),
]
PublicCardReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PUBLIC_CARD_REASON_LENGTH,
    ),
]
PublicRuleId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class PublicPayloadModel(BaseModel, Mapping[str, Any]):
    """Keep legacy dict-style reads while publishing a closed JSON schema."""

    # Public nested models are sometimes carried through ChatResponse.model_copy.
    # Revalidating existing instances prevents a lower-level copy of a frozen
    # object from smuggling oversized or extra values through the final response.
    model_config = ConfigDict(revalidate_instances="always")

    def _public_mapping(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude_none=True)

    def __getitem__(self, key: str) -> Any:
        if key not in type(self).model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._public_mapping())

    def __len__(self) -> int:
        return len(self._public_mapping())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._public_mapping()

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def items(self):
        return self._public_mapping().items()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self._public_mapping() == dict(other)
        return super().__eq__(other)


class PublicProductLabelTrust(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lineage_status: Literal["complete", "partial", "unavailable"]
    freshness_status: Literal[
        "within_policy", "stale", "future_observation", "source_not_eligible",
        "source_registry_configuration_invalid", "invalid_temporal_order",
        "policy_configuration_invalid", "unavailable",
    ]
    label_observed_at: Optional[datetime] = None
    content_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_revision_binding_status: Literal[
        "matched_ingested_record", "ingested_revision_missing",
        "ingested_revision_mismatch",
    ]
    content_revision_meaning: Literal[
        "normalized_snapshot_identifier_not_freshness_proof"
    ] = "normalized_snapshot_identifier_not_freshness_proof"


class PublicNutritionBasis(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    basis_type: Literal[
        "unknown", "per_serving", "per_100g", "per_100ml", "per_package",
    ] = "unknown"
    serving_size_value: Optional[float] = Field(
        default=None,
        gt=0,
        le=MAX_PUBLIC_NUTRITION_VALUE,
    )
    serving_size_unit: Optional[Literal["g", "ml"]] = None
    package_size_value: Optional[float] = Field(
        default=None,
        gt=0,
        le=MAX_PUBLIC_NUTRITION_VALUE,
    )
    package_size_unit: Optional[Literal["g", "ml"]] = None
    servings_per_package: Optional[float] = Field(
        default=None,
        gt=0,
        le=MAX_PUBLIC_SERVINGS_PER_PACKAGE,
    )


class PublicNutritionFacts(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    kcal: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    protein: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    fat: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    carbohydrate: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    sugars: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    sodium: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    saturated_fat: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    cholesterol: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)


class PublicNutritionPercent(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    carbohydrate: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    protein: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    fat: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    sodium: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    cholesterol: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)


class PublicMacroPercent(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    carbohydrate: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    protein: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)
    fat: Optional[float] = Field(default=None, ge=0, le=MAX_PUBLIC_PERCENT_VALUE)


class PublicProduct(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    product_name: Optional[PublicProductName] = None
    food_code: Optional[PublicProductIdentifier] = None
    report_number: Optional[PublicProductIdentifier] = None
    manufacturer_name: Optional[PublicProductShortText] = None
    company_name: Optional[PublicProductShortText] = None
    nutrient_text: Optional[PublicProductLongText] = None
    allergy_warning: Optional[PublicProductLongText] = None
    weight: Optional[PublicProductShortText] = None
    image_url: Optional[PublicImageUrl] = None
    total_weight: Optional[PublicProductShortText] = None
    prdlst_dcnm: Optional[PublicProductShortText] = None
    barcode: Optional[PublicProductIdentifier] = None
    rawmtrl_nm: Optional[PublicProductLongText] = None
    allergy: list[PublicProductListItem] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PRODUCT_LIST_ITEMS,
    )
    ingredients: list[PublicProductListItem] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PRODUCT_LIST_ITEMS,
    )
    allergy_info: list[PublicProductListItem] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_PRODUCT_LIST_ITEMS,
    )
    nutrition: PublicNutritionFacts = Field(default_factory=PublicNutritionFacts)
    nutrition_basis: PublicNutritionBasis = Field(default_factory=PublicNutritionBasis)
    nutrition_percent: PublicNutritionPercent = Field(default_factory=PublicNutritionPercent)
    macro_percent: PublicMacroPercent = Field(default_factory=PublicMacroPercent)
    label_trust: PublicProductLabelTrust


class PublicProductCandidate(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    product_name: Optional[PublicProductName] = None
    report_number: Optional[PublicProductIdentifier] = None
    manufacturer_name: Optional[PublicProductShortText] = None
    company_name: Optional[PublicProductShortText] = None
    weight: Optional[PublicProductShortText] = None
    image_url: Optional[PublicImageUrl] = None
    total_weight: Optional[PublicProductShortText] = None
    prdlst_dcnm: Optional[PublicProductShortText] = None
    barcode: Optional[PublicProductIdentifier] = None
    label_trust: PublicProductLabelTrust


class PublicProductCandidateCard(PublicProductCandidate):
    model_config = ConfigDict(extra="forbid")

    reason: PublicCardReason
    reasons: list[PublicCardReason] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_CARD_REASONS,
    )
    selection_type: Literal["product_candidate"] = "product_candidate"


class PublicNutritionComparison(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    label: PublicProductShortText
    unit: Literal["mg", "g", "kcal"]
    current: float = Field(ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    candidate: float = Field(ge=0, le=MAX_PUBLIC_NUTRITION_VALUE)
    delta: float = Field(
        ge=-MAX_PUBLIC_NUTRITION_VALUE,
        le=MAX_PUBLIC_NUTRITION_VALUE,
    )


class PublicNutritionComparisons(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    sodium: Optional[PublicNutritionComparison] = None
    sugars: Optional[PublicNutritionComparison] = None
    carbohydrate: Optional[PublicNutritionComparison] = None
    fat: Optional[PublicNutritionComparison] = None
    saturated_fat: Optional[PublicNutritionComparison] = None
    cholesterol: Optional[PublicNutritionComparison] = None
    kcal: Optional[PublicNutritionComparison] = None
    protein: Optional[PublicNutritionComparison] = None


class PublicRecommendationBasis(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    profile_allergy_checked: bool
    allergy_absence_supported: bool = False
    profile_special_diets: list[PublicProductListItem] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_RECOMMENDATION_PROFILE_ITEMS,
    )
    profile_special_diets_checked: bool
    profile_disliked_ingredients: list[PublicProductListItem] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_RECOMMENDATION_PROFILE_ITEMS,
    )
    profile_disliked_ingredients_checked: bool = False
    comparison_basis: list[PublicProductShortText] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_COMPARISON_ITEMS,
    )
    nutrition_basis_comparable: bool
    current_nutrition_basis: PublicProductShortText
    candidate_nutrition_basis: Optional[PublicProductShortText] = None
    improved_nutrition_count: int = Field(
        default=0,
        ge=0,
        le=MAX_PUBLIC_COMPARISON_ITEMS,
    )
    applied_rule_ids: list[PublicRuleId] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_RULE_IDS,
    )
    recommendation_available: bool = True
    current_label_trust: Optional[PublicProductLabelTrust] = None
    candidate_label_trust: Optional[PublicProductLabelTrust] = None


class PublicRecommendedProduct(PublicPayloadModel):
    model_config = ConfigDict(extra="forbid")

    product_name: Optional[PublicProductName] = None
    barcode: Optional[PublicProductIdentifier] = None
    report_number: Optional[PublicProductIdentifier] = None
    reason: PublicCardReason
    reasons: list[PublicCardReason] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_CARD_REASONS,
    )
    caution: PublicCardReason
    cautions: list[PublicCardReason] = Field(
        default_factory=list,
        max_length=MAX_PUBLIC_CARD_REASONS,
    )
    nutrition_comparison: PublicNutritionComparisons = Field(
        default_factory=PublicNutritionComparisons
    )
    recommendation_basis: PublicRecommendationBasis


PublicRecommendedProductCard = PublicProductCandidateCard | PublicRecommendedProduct
