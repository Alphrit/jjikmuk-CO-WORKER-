import json

import pytest
from pydantic import ValidationError

from app.api import main as api_main
from app.core.models import ChatResponse, Product, ProductDataTrust
from app.core.public_product import public_product_payload
from app.core.public_product_schema import MAX_PUBLIC_PRODUCT_NAME_LENGTH, PublicProduct, PublicProductCandidate


PRIVATE_FIELDS = {
    "product_id",
    "source",
    "raw_payload",
    "facts_verified",
    "product_found",
    "match_confidence",
    "label_provenance",
}


def _internal_product(**updates):
    payload = {
        "product_id": 98765,
        "product_name": "현미 과자",
        "barcode": "8800000009876",
        "report_number": "202600009876",
        "rawmtrl_nm": "현미, 코코아, 소금",
        "allergy": ["대두"],
        "nutrition": {"sodium": 90, "sugars": 3},
        "nutrition_basis": {"basis_type": "per_serving", "serving_size": "30g"},
        "source": "private-db",
        "raw_payload": {"secret": "must-not-leak"},
        "facts_verified": True,
        "product_found": True,
        "match_confidence": 0.99,
    }
    payload.update(updates)
    return Product.model_validate(payload)


def test_public_product_payload_removes_internal_repository_fields():
    payload = public_product_payload(_internal_product())

    assert PRIVATE_FIELDS.isdisjoint(payload)
    assert "must-not-leak" not in json.dumps(payload)
    assert payload["product_name"] == "현미 과자"
    assert payload["nutrition"]["sodium"] == 90
    PublicProduct.model_validate(payload)


def test_candidate_view_uses_the_smaller_closed_schema():
    payload = public_product_payload(_internal_product(), view="candidate")

    PublicProductCandidate.model_validate(payload)
    assert "rawmtrl_nm" not in payload
    assert "nutrition" not in payload
    assert PRIVATE_FIELDS.isdisjoint(payload)


def test_unapproved_image_host_is_not_exposed(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_PUBLIC_IMAGE_HOSTS", "cdn.example.com")

    payload = public_product_payload(
        _internal_product(image_url="https://tracker.invalid/private.png")
    )

    assert payload["image_url"] is None


def test_malformed_oversized_product_is_rejected_by_public_contract():
    with pytest.raises((ValidationError, ValueError)):
        public_product_payload(_internal_product(product_name="x" * (MAX_PUBLIC_PRODUCT_NAME_LENGTH + 1)))


def test_chat_response_schema_never_serializes_internal_product_state():
    response = ChatResponse(
        intent="product_summary",
        answer="제품 정보예요.",
        risk_level="unknown",
        assessment_status="incomplete",
        current_product=public_product_payload(_internal_product()),
    )
    payload = response.model_dump(mode="json")

    assert PRIVATE_FIELDS.isdisjoint(payload["current_product"])
    assert "internal_conversation_state" not in payload
    assert "product_reference" not in payload


def test_chat_response_rejects_private_product_card_shape():
    base = {
        "intent": "unknown",
        "answer": "확인이 필요해요.",
        "risk_level": "unknown",
    }
    private_card = {
        "product_id": 987,
        "source_record_id": "private-source-record",
        "match_confidence": 0.99,
    }

    with pytest.raises(ValidationError):
        ChatResponse(**base, product_candidates=[private_card])
    with pytest.raises(ValidationError):
        ChatResponse(**base, recommended_products=[private_card])


def test_openapi_product_models_are_closed_and_private_fields_are_absent():
    schemas = api_main.app.openapi()["components"]["schemas"]

    for model_name in (
        "PublicProduct",
        "PublicProductCandidateCard",
        "PublicRecommendedProduct",
        "PublicNutritionFacts",
    ):
        assert schemas[model_name]["additionalProperties"] is False
    assert PRIVATE_FIELDS.isdisjoint(schemas["PublicProduct"]["properties"])
    assert "ProductLookupResponse" not in schemas


def test_product_data_trust_does_not_accept_private_lineage_object():
    with pytest.raises(ValidationError):
        ProductDataTrust(
            repository_identity_matched=True,
            label_lineage_status="complete",
            label_freshness_status="within_policy",
            content_revision_sha256="a" * 64,
            content_revision_binding_status="matched_ingested_record",
            content_revision_meaning="normalized_snapshot_identifier_not_freshness_proof",
            label_provenance={"source_id": "private"},
        )
