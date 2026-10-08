"""Synthetic product-label provenance for deterministic offline evaluation.

These helpers never mutate production/catalog data.  A case must opt in with
``evaluation_label_fixture`` on each exact product row whose provenance is part
of the scenario.  At evaluation time we normalize that row, bind its content
hash, and use a short-lived observation timestamp.  The fixed source identifier
is registered only inside the evaluator process and is explicitly synthetic;
it must never be copied into an operational database or presented as a real
manufacturer/regulator source.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping

from app.api.adapters import normalize_product_payload
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)


EVALUATION_LABEL_FIXTURE_KEY = "evaluation_label_fixture"
EVALUATION_LABEL_SOURCE_TYPE = "manufacturer_label"
EVALUATION_LABEL_SOURCE_ID = "synthetic-evaluation-label-fixture"
_SOURCE_REGISTRY_ENV = "FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES"


def materialize_evaluation_product_label(
    raw_product: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Bind explicitly marked synthetic label evidence to one product row."""

    payload = deepcopy(dict(raw_product))
    fixture = payload.pop(EVALUATION_LABEL_FIXTURE_KEY, None)
    if fixture is None:
        return payload
    if not isinstance(fixture, Mapping):
        raise ValueError(
            f"{EVALUATION_LABEL_FIXTURE_KEY} must be an explicit mapping"
        )
    if any(
        key in payload
        for key in (
            "label_provenance",
            "product_label_provenance",
            "label_source_type",
            "label_source_id",
            "ingested_content_revision_sha256",
        )
    ):
        raise ValueError(
            "evaluation fixture rows must not carry precomputed provenance"
        )

    source_record_id = str(fixture.get("source_record_id") or "").strip()
    if not source_record_id:
        raise ValueError(
            "evaluation_label_fixture.source_record_id is required"
        )
    covered_groups = fixture.get("covered_fact_groups")
    if covered_groups is None:
        covered_groups = list(PRODUCT_LABEL_FACT_GROUPS)
    if not isinstance(covered_groups, list) or not covered_groups:
        raise ValueError(
            "evaluation_label_fixture.covered_fact_groups must be a non-empty list"
        )

    normalized = normalize_product_payload(payload, repository_record=True)
    if normalized is None:
        raise ValueError("evaluation label fixture must contain a product")
    revision = product_label_content_revision_sha256(normalized)
    observation = observed_at or (datetime.now(timezone.utc) - timedelta(days=1))
    if observation.tzinfo is None or observation.utcoffset() is None:
        raise ValueError("evaluation label observation must be timezone-aware")

    payload["label_provenance"] = {
        "source_type": EVALUATION_LABEL_SOURCE_TYPE,
        "source_id": EVALUATION_LABEL_SOURCE_ID,
        "source_record_id": source_record_id,
        "label_observed_at": observation.astimezone(timezone.utc).isoformat(),
        "ingested_content_revision_sha256": revision,
        "covered_fact_groups": list(covered_groups),
        "lineage_status": "complete",
    }
    return payload


def materialize_evaluation_repository_seed(
    seed_data: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(key): materialize_evaluation_product_label(product)
        for key, product in seed_data.items()
    }


@contextmanager
def synthetic_evaluation_label_source_registry() -> Iterator[None]:
    """Temporarily allow only the evaluator's explicitly synthetic source."""

    previous = os.environ.get(_SOURCE_REGISTRY_ENV)
    os.environ[_SOURCE_REGISTRY_ENV] = (
        f"{EVALUATION_LABEL_SOURCE_TYPE}:{EVALUATION_LABEL_SOURCE_ID}"
    )
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_SOURCE_REGISTRY_ENV, None)
        else:
            os.environ[_SOURCE_REGISTRY_ENV] = previous
