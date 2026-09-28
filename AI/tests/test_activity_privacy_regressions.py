import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api import main as api_main
from app.core.models import (
    ActivityProductLabelTrace,
    ChatResponse,
    MAX_CHAT_MESSAGE_LENGTH,
    Product,
)
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.core.public_product import public_product_payload
from app.infrastructure.repository import SQLiteActivityRepository
from app.services.activity import log_chat_activity, raw_chat_message_logging_enabled
from app.services.response_assessment import attach_response_assessment


class _CapturingRepository:
    def __init__(self):
        self.records = []

    def save(self, activity):
        self.records.append(activity)


def _log(repository, message="땅콩 알레르기가 있고 전화번호는 010-1234-5678이에요"):
    log_chat_activity(
        profile=None,
        product=None,
        barcode=None,
        report_number=None,
        message=message,
        intent="allergy_check",
        repository=repository,
    )


def _verified_product() -> Product:
    payload = {
        "product_id": 8101,
        "product_name": "감사 추적 제품",
        "barcode": "8800000008101",
        "report_number": "202600008101",
        "rawmtrl_nm": "귀리, 코코아",
        "ingredients": ["귀리", "코코아"],
        "nutrition": {"sodium": 120, "sugars": 3},
        "nutrition_basis": {
            "basis_type": "per_serving",
            "serving_size": "30g",
            "basis_source": "label",
        },
    }
    revision = product_label_content_revision_sha256(
        Product.model_validate(payload)
    )
    observed_at = datetime.now(timezone.utc) - timedelta(days=1)
    payload["label_provenance"] = {
        "source_type": "manufacturer_label",
        "source_id": "synthetic-label-test",
        "source_record_id": "private-source-record-8101",
        "label_observed_at": observed_at.isoformat(),
        "upstream_updated_at": (observed_at - timedelta(hours=1)).isoformat(),
        "upstream_revision": "private-upstream-revision",
        "ingested_content_revision_sha256": revision,
        "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
        "lineage_status": "complete",
    }
    return Product.model_validate(payload)


def _assessed_product_response(product: Product) -> ChatResponse:
    response = ChatResponse(
        intent="nutrition_explain",
        answer="제품 표시 영양성분을 확인했어요.",
        risk_level="low",
        current_product=public_product_payload(product),
    )
    return attach_response_assessment(response, product=product)


def test_raw_chat_message_is_not_retained_by_default():
    repository = _CapturingRepository()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FOOD_CHATBOT_STORE_RAW_MESSAGES", None)
        assert raw_chat_message_logging_enabled() is False
        _log(repository)

    assert repository.records[0].message is None
    assert repository.records[0].intent == "allergy_check"


def test_raw_chat_message_logging_requires_explicit_true_value():
    repository = _CapturingRepository()
    with patch.dict(
        os.environ,
        {"FOOD_CHATBOT_STORE_RAW_MESSAGES": "true"},
        clear=False,
    ):
        _log(repository, "사용자가 보관에 동의한 원문")

    assert repository.records[0].message == "사용자가 보관에 동의한 원문"


def test_invalid_raw_message_flag_fails_closed():
    repository = _CapturingRepository()
    with patch.dict(
        os.environ,
        {"FOOD_CHATBOT_STORE_RAW_MESSAGES": "maybe"},
        clear=False,
    ):
        assert raw_chat_message_logging_enabled() is False
        _log(repository)

    assert repository.records[0].message is None


def test_direct_service_logging_truncates_opted_in_message():
    repository = _CapturingRepository()
    with patch.dict(
        os.environ,
        {"FOOD_CHATBOT_STORE_RAW_MESSAGES": "1"},
        clear=False,
    ):
        _log(repository, "x" * (MAX_CHAT_MESSAGE_LENGTH + 100))

    assert len(repository.records[0].message) == MAX_CHAT_MESSAGE_LENGTH


def test_sqlite_activity_trace_persists_only_bounded_external_ai_metadata(tmp_path):
    repository = SQLiteActivityRepository(str(tmp_path / "activity.sqlite3"))
    response_id = str(uuid4())
    with patch.dict(
        os.environ,
        {"FOOD_CHATBOT_STORE_RAW_MESSAGES": "false"},
        clear=False,
    ):
        log_chat_activity(
            profile=None,
            product=None,
            barcode=None,
            report_number=None,
            message="땅콩 알레르기 원문",
            intent="unknown",
            response_id=response_id,
            external_ai_used=True,
            external_ai_services=["rag", "llm"],
            repository=repository,
        )

    readiness = repository.readiness_check()
    assert readiness["available"] is True
    with sqlite3.connect(repository.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM activity_history").fetchone()
        assert row["message"] is None
        assert row["response_id"] == response_id
        assert "response_kind" not in row.keys()
        assert "recheck_of_response_id" not in row.keys()
        assert "root_response_id" not in row.keys()
        assert row["external_ai_services"] == '["rag", "llm"]'
    # user_id is absent, so normal per-user history intentionally has no row.
    assert repository.list_by_user_id(1) == []


def test_verified_product_used_by_response_builds_exact_identifier_free_trace(
    monkeypatch,
):
    product = _verified_product()
    response = _assessed_product_response(product)
    captured = {}
    monkeypatch.setattr(
        api_main,
        "log_chat_activity",
        lambda **kwargs: captured.update(kwargs),
    )

    api_main._log_pipeline_response(
        response,
        activity_context={
            "profile": None,
            "product": product,
            "barcode": product.barcode,
            "report_number": product.report_number,
            "message": "영양성분 알려줘",
        },
        allow_raw_audit_message=True,
    )

    trace = captured["response_product_label_trace"]
    assert isinstance(trace, ActivityProductLabelTrace)
    payload = trace.model_dump(mode="json")
    assert set(payload) == {
        "repository_identity_matched",
        "label_lineage_status",
        "label_freshness_status",
        "label_observed_at",
        "content_revision_sha256",
        "content_revision_binding_status",
        "nutrition_basis_type",
    }
    assert payload["repository_identity_matched"] is True
    assert payload["label_lineage_status"] == "complete"
    assert payload["label_freshness_status"] == "within_policy"
    assert payload["content_revision_binding_status"] == "matched_ingested_record"
    assert payload["nutrition_basis_type"] == "per_serving"
    assert payload["content_revision_sha256"] == (
        product_label_content_revision_sha256(product)
    )
    assert "source_id" not in payload
    assert "source_record_id" not in payload
    assert "upstream_revision" not in payload
    assert "content_revision_meaning" not in payload


def test_product_context_without_a_verified_current_product_response_has_no_trace(
    monkeypatch,
):
    product = _verified_product()
    response = ChatResponse(
        intent="product_confirmation",
        answer="제품을 먼저 선택해 주세요.",
        risk_level="unknown",
    )
    captured = {}
    monkeypatch.setattr(
        api_main,
        "log_chat_activity",
        lambda **kwargs: captured.update(kwargs),
    )

    api_main._log_pipeline_response(
        response,
        activity_context={
            "profile": None,
            "product": product,
            "barcode": product.barcode,
            "report_number": product.report_number,
            "message": "이 제품 봐줘",
        },
        allow_raw_audit_message=False,
    )

    assert captured["response_product_label_trace"] is None


def test_activity_product_trace_structurally_rejects_false_or_extra_claims():
    product = _verified_product()
    trace = api_main._response_product_label_trace(
        _assessed_product_response(product),
        product,
    )
    assert trace is not None
    payload = trace.model_dump(mode="json")

    with pytest.raises(ValidationError):
        ActivityProductLabelTrace.model_validate({
            **payload,
            "repository_identity_matched": False,
        })
    with pytest.raises(ValidationError):
        ActivityProductLabelTrace.model_validate({
            **payload,
            "source_record_id": "must-not-be-retained",
        })
