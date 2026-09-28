"""Deterministic product-label lineage helpers.

Product identity being resolved from the configured repository is a different
claim from the label facts in that row being traceable to an observed source.
This module keeps that boundary small and dependency-free so repositories,
assessment code, and nutrition rules all use the same definition.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


PRODUCT_LABEL_CONTENT_HASH_SCHEMA = "normalized-product-label-snapshot-v2"
PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
PRODUCT_LABEL_FACT_GROUPS = (
    "identity",
    "ingredients",
    "allergens",
    "nutrition",
    "nutrition_basis",
)
ELIGIBLE_PRODUCT_LABEL_SOURCE_TYPES = frozenset({
    "manufacturer_label",
    "regulatory_database",
})
DEFAULT_PRODUCT_LABEL_MAX_AGE_DAYS = 180
MAX_CONFIGURABLE_PRODUCT_LABEL_AGE_DAYS = 3650


def eligible_product_label_sources() -> frozenset[tuple[str, str]] | None:
    """Parse the operator-controlled exact source registry.

    Entries use ``source_type:source_id`` and are comma-separated. This is an
    eligibility registry, not cryptographic proof that the source assertion is
    genuine.
    """

    raw_value = os.getenv("FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES", "")
    if not raw_value.strip():
        return None
    entries: set[tuple[str, str]] = set()
    for raw_entry in raw_value.split(","):
        source_type, separator, source_id = raw_entry.strip().partition(":")
        source_type = source_type.strip()
        source_id = source_id.strip()
        if (
            not separator
            or source_type not in ELIGIBLE_PRODUCT_LABEL_SOURCE_TYPES
            or not source_id
            or len(source_id) > 128
            or re.fullmatch(PRODUCT_LABEL_PUBLIC_IDENTIFIER_PATTERN, source_id)
            is None
        ):
            return None
        entries.add((source_type, source_id))
    return frozenset(entries) or None


def product_label_source_registry_ready() -> bool:
    return eligible_product_label_sources() is not None


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def product_label_content_payload(product: Any) -> dict[str, Any]:
    """Return the canonical label content covered by one revision hash.

    List order is preserved because ingredient order can be meaningful on a
    label. Mapping keys are sorted during JSON encoding. Internal surrogate
    IDs, derived search names/categories, match confidence, repository-match
    state, and provenance itself are deliberately excluded. This is still a
    normalized catalog snapshot, not a portable manufacturer signature.
    """

    return {
        "schema": PRODUCT_LABEL_CONTENT_HASH_SCHEMA,
        "identity": {
            key: getattr(product, key, None)
            for key in (
                "product_name",
                "food_code",
                "report_number",
                "barcode",
                "manufacturer_name",
                "company_name",
                "weight",
                "total_weight",
                "prdlst_dcnm",
            )
        },
        "ingredients": {
            "rawmtrl_nm": getattr(product, "rawmtrl_nm", None),
            "ingredients": list(getattr(product, "ingredients", None) or []),
        },
        "allergens": {
            "allergy_warning": getattr(product, "allergy_warning", None),
            "allergy": list(getattr(product, "allergy", None) or []),
            "allergy_info": list(getattr(product, "allergy_info", None) or []),
        },
        "nutrition": {
            "values": dict(getattr(product, "nutrition", None) or {}),
            "daily_value_percent": dict(
                getattr(product, "nutrition_percent", None) or {}
            ),
            "macro_percent": dict(getattr(product, "macro_percent", None) or {}),
            "label_text": getattr(product, "nutrient_text", None),
        },
        "nutrition_basis": _model_payload(
            getattr(product, "nutrition_basis", None)
        ),
    }


def product_label_content_revision_sha256(product: Any) -> str:
    canonical = json.dumps(
        product_label_content_payload(product),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def product_label_content_revision_matches(product: Any) -> bool:
    provenance = getattr(product, "label_provenance", None)
    ingested_revision = getattr(
        provenance,
        "ingested_content_revision_sha256",
        None,
    )
    if not ingested_revision:
        return False
    try:
        return ingested_revision == product_label_content_revision_sha256(product)
    except (TypeError, ValueError):
        return False


def content_revision_binding_status(product: Any) -> str:
    """Describe whether source evidence is bound to the live normalized facts.

    The computed hash alone is only a snapshot identifier.  It becomes a
    source-record binding only when a separately retained source hash matches
    the current canonical facts.
    """

    provenance = getattr(product, "label_provenance", None)
    ingested_revision = getattr(
        provenance,
        "ingested_content_revision_sha256",
        None,
    )
    if not ingested_revision:
        return "ingested_revision_missing"
    return (
        "matched_ingested_record"
        if product_label_content_revision_matches(product)
        else "ingested_revision_mismatch"
    )


def effective_label_lineage_status(product: Any) -> str:
    """Return fail-closed status after checking the live content revision."""

    provenance = getattr(product, "label_provenance", None)
    declared = getattr(provenance, "lineage_status", "unavailable")
    if declared not in {"complete", "partial"}:
        return "unavailable"
    if content_revision_binding_status(product) != "matched_ingested_record":
        return "unavailable"
    return declared


def product_label_max_age_days() -> int | None:
    """Return the explicit operational recency window for label observations."""

    raw_value = os.getenv(
        "FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS",
        str(DEFAULT_PRODUCT_LABEL_MAX_AGE_DAYS),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    if not 1 <= value <= MAX_CONFIGURABLE_PRODUCT_LABEL_AGE_DAYS:
        return None
    return value


def label_freshness_status(
    product: Any,
    *,
    now: datetime | None = None,
) -> str:
    """Return claim-eligibility recency, not a claim of a market-latest label."""

    provenance = getattr(product, "label_provenance", None)
    if provenance is None or effective_label_lineage_status(product) == "unavailable":
        return "unavailable"
    if getattr(provenance, "source_type", None) not in (
        ELIGIBLE_PRODUCT_LABEL_SOURCE_TYPES
    ):
        return "source_not_eligible"
    source_registry = eligible_product_label_sources()
    if source_registry is None:
        return "source_registry_configuration_invalid"
    if (
        getattr(provenance, "source_type", None),
        getattr(provenance, "source_id", None),
    ) not in source_registry:
        return "source_not_eligible"

    observed_at = getattr(provenance, "label_observed_at", None)
    if (
        observed_at is None
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        return "unavailable"
    upstream_updated_at = getattr(provenance, "upstream_updated_at", None)
    if (
        upstream_updated_at is not None
        and upstream_updated_at > observed_at
    ):
        return "invalid_temporal_order"

    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("now must include an explicit UTC offset")
    reference_time = reference_time.astimezone(timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    if observed_at > reference_time:
        return "future_observation"
    max_age_days = product_label_max_age_days()
    if max_age_days is None:
        return "policy_configuration_invalid"
    if reference_time - observed_at > timedelta(days=max_age_days):
        return "stale"
    return "within_policy"


def label_provenance_supports(
    product: Any,
    required_fact_groups: Iterable[str],
) -> bool:
    """Whether the stated lineage is usable for the requested label claims."""

    provenance = getattr(product, "label_provenance", None)
    if provenance is None:
        return False
    if effective_label_lineage_status(product) not in {
        "complete",
        "partial",
    }:
        return False
    if label_freshness_status(product) != "within_policy":
        return False
    if not all(
        getattr(provenance, field, None)
        for field in (
            "source_type",
            "source_id",
            "source_record_id",
            "label_observed_at",
            "ingested_content_revision_sha256",
            "content_revision_sha256",
        )
    ):
        return False
    covered = set(getattr(provenance, "covered_fact_groups", None) or [])
    return set(required_fact_groups).issubset(covered)


def missing_label_provenance_groups(
    product: Any,
    required_fact_groups: Iterable[str],
) -> list[str]:
    required = list(dict.fromkeys(required_fact_groups))
    provenance = getattr(product, "label_provenance", None)
    if (
        provenance is None
        or effective_label_lineage_status(product) == "unavailable"
        or label_freshness_status(product) != "within_policy"
    ):
        return required
    covered = set(getattr(provenance, "covered_fact_groups", None) or [])
    return [group for group in required if group not in covered]


def label_provenance_limitation_reason(
    product: Any,
    required_fact_groups: Iterable[str],
) -> str:
    """Return one truthful user-facing reason for a blocked label claim."""

    provenance = getattr(product, "label_provenance", None)
    if provenance is None:
        return "제품 표시 정보의 출처·수집 시점·내용 기록이 제공되지 않았어요."

    binding = content_revision_binding_status(product)
    if binding == "ingested_revision_missing":
        return (
            "제품 표시 정보가 수집 당시 저장된 내용 기록과 연결되지 않아, "
            "지금 보이는 정보가 당시와 같은지 확인할 수 없어요."
        )
    if binding == "ingested_revision_mismatch":
        return (
            "지금 보이는 제품 표시 정보가 수집 당시 저장된 내용 기록과 달라 "
            "판단 근거로 사용할 수 없어요."
        )
    if effective_label_lineage_status(product) == "unavailable":
        return "제품 표시 정보의 출처 추적 상태를 확인할 수 없어 판단 근거로 사용할 수 없어요."

    required_fields = (
        "source_type",
        "source_id",
        "source_record_id",
        "label_observed_at",
    )
    if not all(getattr(provenance, field, None) for field in required_fields):
        return "제품 표시의 출처 식별자나 실제 관찰일이 빠져 있어 추적할 수 없어요."

    required = set(required_fact_groups)
    covered = set(getattr(provenance, "covered_fact_groups", None) or [])
    if not required.issubset(covered):
        return "판단에 필요한 원재료·알레르기 또는 영양 정보가 출처 추적 범위에 포함되지 않았어요."

    freshness = label_freshness_status(product)
    if freshness == "source_not_eligible":
        return (
            "이 출처는 부재·저위험·영양 비교에 사용할 수 있도록 운영상 "
            "등록되지 않았어요."
        )
    if freshness == "source_registry_configuration_invalid":
        return "제품 표시 판단에 사용할 출처 목록 설정을 확인할 수 없어 판단을 보류했어요."
    if freshness == "future_observation":
        return "제품 표시 관찰일이 현재보다 미래라 관찰 기록을 신뢰할 수 없어요."
    if freshness == "stale":
        max_age = product_label_max_age_days()
        return (
            f"제품 표시 관찰 후 운영 유효기간({max_age}일)이 지나 "
            "부재·저위험·영양 비교 근거로 사용할 수 없어요."
        )
    if freshness == "invalid_temporal_order":
        return "출처 갱신일이 표시 수집일보다 늦게 기록되어 시간 순서를 확인할 수 없어요."
    if freshness == "policy_configuration_invalid":
        return "제품 표시 유효기간 운영 설정이 올바르지 않아 판단을 보류했어요."
    return "제품 표시 정보의 출처·수집 시점 확인 조건이 충분하지 않아 판단을 보류했어요."
