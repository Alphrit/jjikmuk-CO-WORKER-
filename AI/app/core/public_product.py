"""One public serialization contract for repository-backed products.

``Product`` is an internal catalog model.  It deliberately contains search,
matching, ingestion, and repository fields that must not become an accidental
API contract.  Every public product surface uses the helpers in this module so
adding an internal field to ``Product`` cannot silently expose it.
"""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime
from typing import Any, Literal, Mapping, Optional
from urllib.parse import urlsplit

from app.core.models import Product
from app.core.public_product_schema import (
    PublicMacroPercent,
    PublicNutritionBasis,
    PublicNutritionFacts,
    PublicNutritionPercent,
    PublicProduct,
    PublicProductCandidate,
    PublicProductLabelTrust,
    PublicRecommendedProduct,
)
from app.core.product_provenance import (
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    product_label_content_revision_sha256,
)


_LOCAL_HOST_SUFFIXES = (
    ".internal",
    ".lan",
    ".local",
    ".localhost",
)
_PUBLIC_IMAGE_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def configured_public_image_hosts() -> frozenset[str] | None:
    """Return the exact operator-controlled image-host allowlist.

    Missing or malformed configuration disables public product images. Wildcard
    and suffix matching are intentionally unsupported because a sibling or
    user-controlled subdomain is not equivalent to the reviewed host.
    """

    raw_value = os.getenv("FOOD_CHATBOT_PUBLIC_IMAGE_HOSTS", "")
    if not raw_value.strip():
        return None
    hosts: set[str] = set()
    for raw_host in raw_value.split(","):
        host = raw_host.strip().lower().rstrip(".")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if not _PUBLIC_IMAGE_HOST_PATTERN.fullmatch(host):
            return None
        if host == "localhost" or host.endswith(_LOCAL_HOST_SUFFIXES):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
        hosts.add(host)
    return frozenset(hosts) or None


def public_safe_image_url(value: object) -> Optional[str]:
    """Return a display-safe public HTTPS image URL, otherwise ``None``.

    Product images are rendered by a client, not fetched by this service, but a
    catalog URL can still disclose credentials or point a user at a local
    resource.  The public contract therefore rejects userinfo, query strings,
    fragments, non-default ports, local/private IPs, and local hostnames.  This
    is a syntactic boundary; it does not claim that the remote image is benign
    or permanently controlled by the expected publisher.
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(ord(char) < 0x20 for char in candidate):
        return None
    if "\\" in candidate or any(char.isspace() for char in candidate):
        return None

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() != "https" or not hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.query or parsed.fragment:
        return None
    if port not in (None, 443):
        return None

    normalized_host = hostname.rstrip(".").lower()
    if (
        not normalized_host
        or normalized_host == "localhost"
        or "." not in normalized_host
        or normalized_host.endswith(_LOCAL_HOST_SUFFIXES)
    ):
        return None
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            ascii_host = normalized_host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        labels = ascii_host.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            for label in labels
        ):
            return None
        if ascii_host.replace(".", "").isdigit():
            # Reject legacy/octal-looking IPv4 spellings that URL consumers may
            # reinterpret differently from ``ipaddress``.
            return None
    else:
        if not address.is_global:
            return None

    allowed_hosts = configured_public_image_hosts()
    if allowed_hosts is None:
        return None
    try:
        comparable_host = normalized_host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if comparable_host not in allowed_hosts:
        return None

    return candidate


def public_product_label_trust(
    product: Product,
    *,
    now: datetime | None = None,
) -> PublicProductLabelTrust:
    """Build the same minimal label-trust block for every product surface."""

    return PublicProductLabelTrust(
        lineage_status=effective_label_lineage_status(product),
        freshness_status=label_freshness_status(product, now=now),
        label_observed_at=product.label_provenance.label_observed_at,
        content_revision_sha256=product_label_content_revision_sha256(product),
        content_revision_binding_status=content_revision_binding_status(product),
    )


def public_product_payload(
    product: Product,
    *,
    view: Literal["detail", "candidate"] = "detail",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Serialize a product through a closed allowlist.

    Candidate cards intentionally contain only fields needed to distinguish and
    select a result.  Detail views retain label display facts.  Both exclude the
    repository ID, legacy free-form source, search-normalized name, match score,
    match-state flag, raw provenance identifiers, and hidden verification bit.
    """

    common = {
        "product_name": product.product_name,
        "report_number": product.report_number,
        "manufacturer_name": product.manufacturer_name,
        "company_name": product.company_name,
        "weight": product.weight,
        "image_url": public_safe_image_url(product.image_url),
        "total_weight": product.total_weight,
        "prdlst_dcnm": product.prdlst_dcnm,
        "barcode": product.barcode,
        "label_trust": public_product_label_trust(product, now=now),
    }
    if view == "candidate":
        model = PublicProductCandidate.model_validate(common)
    else:
        model = PublicProduct.model_validate({
            **common,
            "food_code": product.food_code,
            "nutrient_text": product.nutrient_text,
            "allergy_warning": product.allergy_warning,
            "rawmtrl_nm": product.rawmtrl_nm,
            "allergy": list(product.allergy),
            "ingredients": list(product.ingredients),
            "allergy_info": list(product.allergy_info),
            "nutrition": PublicNutritionFacts.model_validate({
                key: value
                for key, value in product.nutrition.items()
                if key in PublicNutritionFacts.model_fields
            }),
            "nutrition_basis": PublicNutritionBasis.model_validate(
                product.nutrition_basis.model_dump(exclude={"basis_source"})
            ),
            "nutrition_percent": PublicNutritionPercent.model_validate({
                key: value
                for key, value in product.nutrition_percent.items()
                if key in PublicNutritionPercent.model_fields
            }),
            "macro_percent": PublicMacroPercent.model_validate({
                key: value
                for key, value in product.macro_percent.items()
                if key in PublicMacroPercent.model_fields
            }),
        })
    payload = model.model_dump(mode="json", exclude_none=True)
    # A stable key lets clients clear an older image when the current catalog
    # value is invalid instead of accidentally continuing to display it.
    payload["image_url"] = public_safe_image_url(product.image_url)
    return payload


def public_recommendation_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and serialize one alternative recommendation card."""

    payload = PublicRecommendedProduct.model_validate(value).model_dump(
        mode="json",
        exclude_none=True,
    )
    # ``null`` identity values are an intentional unavailable-card contract,
    # not omitted fields.  Existing clients use them to distinguish a status
    # card from a selectable recommendation.
    for key in ("product_name", "barcode", "report_number"):
        payload.setdefault(key, None)
    return payload


def internal_product_reference(value: Product | Mapping[str, Any] | None) -> Optional[dict[str, Any]]:
    """Return a bounded saved/state reference, never product facts.

    This payload is suitable for server-owned saved metadata.  It is separate
    from ``PublicProduct`` so an internal surrogate ID is not exposed merely to
    make a later follow-up possible.
    """

    if value is None:
        return None
    getter = value.get if isinstance(value, Mapping) else lambda key: getattr(value, key, None)

    reference: dict[str, Any] = {}
    product_id = getter("product_id")
    if isinstance(product_id, int) and not isinstance(product_id, bool) and 0 <= product_id <= 9223372036854775807:
        reference["product_id"] = product_id
    for key, max_length in (
        ("barcode", 256),
        ("report_number", 256),
        ("product_name", 256),
    ):
        raw = getter(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if text:
            reference[key] = text[:max_length]

    if not any(reference.get(key) is not None for key in ("product_id", "barcode", "report_number")):
        return None
    return reference


def public_conversation_state(value: object) -> dict[str, Any]:
    """Remove repository-only IDs from client round-trip state."""

    if not isinstance(value, Mapping):
        return {}
    state: dict[str, Any] = {}
    for key in ("last_intent", "last_task_type", "pending_intent"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            state[key] = raw.strip()[:256]

    pending_user_message = value.get("pending_user_message")
    if isinstance(pending_user_message, str) and pending_user_message.strip():
        state["pending_user_message"] = pending_user_message.strip()[:4000]

    def _public_reference(raw_reference: object) -> Optional[dict[str, str]]:
        if not isinstance(raw_reference, Mapping):
            return None
        reference: dict[str, str] = {}
        for field_name in ("product_name", "barcode", "report_number"):
            raw = raw_reference.get(field_name)
            if isinstance(raw, str) and raw.strip():
                reference[field_name] = raw.strip()[:256]
        if not (reference.get("barcode") or reference.get("report_number")):
            return None
        return reference

    last_product = _public_reference(value.get("last_confirmed_product"))
    if last_product is not None:
        state["last_confirmed_product"] = last_product

    raw_candidates = value.get("pending_product_candidates")
    if isinstance(raw_candidates, list):
        candidates = [
            candidate
            for item in raw_candidates[:3]
            if (candidate := _public_reference(item)) is not None
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

    from app.core.conversation_context_fields import task_context_fields
    state.update(task_context_fields(value, _public_reference))
    if state:
        state["version"] = 1
    return state
