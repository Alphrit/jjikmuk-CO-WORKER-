from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.adapters import normalize_product_payload
from app.core.models import ChatResponse, Product, ProductLabelProvenance, Profile
from app.core.nutrition_basis import nutrition_values_comparable
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    content_revision_binding_status,
    effective_label_lineage_status,
    label_freshness_status,
    label_provenance_supports,
    product_label_content_revision_sha256,
)
from app.core.public_product import public_product_payload
from app.infrastructure.repository import ProductRepository
from app.prompts.builder import build_rule_explainer_prompt
from app.services import general_handlers
from app.services.general_handlers import handle_food_compare
from app.services.handlers import build_can_i_eat_result, handle_ingredient_check
from app.services.hybrid import (
    _fallback_with_grounding_reason,
    _grounding_failure_types,
    enhance_rule_response,
)
from app.services import hybrid
from app.services.product_status_handlers import handle_product_summary
from app.services.response_assessment import attach_response_assessment


def _product_payload(**updates):
    payload = {
        "product_id": 7001,
        "product_name": "계보 테스트 바",
        "barcode": "8800000007001",
        "report_number": "202600007001",
        "manufacturer_name": "테스트 제조사",
        "rawmtrl_nm": "귀리, 코코아",
        "ingredients": ["귀리", "코코아"],
        "allergy": ["우유"],
        "allergy_warning": "우유 함유",
        "nutrition": {"sodium": 500, "sugars": 8, "kcal": 180},
        "nutrition_basis": {
            "basis_type": "per_serving",
            "serving_size": "40g",
            "basis_source": "label",
        },
    }
    payload.update(updates)
    return payload


def _traced_product(
    *,
    covered_fact_groups=None,
    lineage_status="complete",
    **updates,
) -> Product:
    payload = _product_payload(**updates)
    content_revision = product_label_content_revision_sha256(
        Product.model_validate(payload)
    )
    observed_at = datetime.now(timezone.utc) - timedelta(days=1)
    payload["label_provenance"] = {
        "source_type": "manufacturer_label",
        "source_id": "synthetic-label-test",
        "source_record_id": "label-7001-v3",
        "label_observed_at": observed_at.isoformat(),
        "upstream_updated_at": (observed_at - timedelta(days=1)).isoformat(),
        "upstream_revision": "v3",
        "ingested_content_revision_sha256": content_revision,
        "covered_fact_groups": (
            list(PRODUCT_LABEL_FACT_GROUPS)
            if covered_fact_groups is None
            else covered_fact_groups
        ),
        "lineage_status": lineage_status,
    }
    return Product.model_validate(payload)


def _complete_profile(*, allergies=None) -> Profile:
    return Profile(
        profile_name="나",
        allergies=allergies or [],
        conditions=[],
        special_diets=[],
    )


def test_legacy_free_form_source_does_not_invent_label_lineage():
    product = normalize_product_payload({
        "product_name": "legacy",
        "source": "some free-form catalog",
        "updated_at": "2026-09-03T00:00:00Z",
        "nutrition": {"sodium": "500mg"},
    })

    assert product is not None
    assert product.source == "some free-form catalog"
    assert product.label_provenance.lineage_status == "unavailable"
    assert product.label_provenance.source_id is None
    assert product.label_provenance.label_observed_at is None
    # A deterministic snapshot identifier exists, but does not make lineage
    # or freshness available.
    assert len(product.label_provenance.content_revision_sha256 or "") == 64
    assert not label_provenance_supports(product, ["nutrition"])


def test_canonical_content_revision_is_stable_across_mapping_order():
    left = Product.model_validate(_product_payload(
        nutrition={"sodium": 500, "sugars": 8, "kcal": 180},
    ))
    right = Product.model_validate(_product_payload(
        nutrition={"kcal": 180, "sugars": 8, "sodium": 500},
    ))

    assert (
        left.label_provenance.content_revision_sha256
        == right.label_provenance.content_revision_sha256
    )


@pytest.mark.parametrize(
    "change",
    [
        {"product_name": "다른 제품"},
        {"rawmtrl_nm": "귀리, 땅콩"},
        {"allergy": ["땅콩"]},
        {"nutrition": {"sodium": 501, "sugars": 8, "kcal": 180}},
        {"nutrition_basis": {"basis_type": "per_serving", "serving_size": "50g"}},
    ],
)
def test_content_revision_covers_every_required_fact_family(change):
    baseline = Product.model_validate(_product_payload())
    changed = Product.model_validate(_product_payload(**change))

    assert (
        baseline.label_provenance.content_revision_sha256
        != changed.label_provenance.content_revision_sha256
    )


def test_source_supplied_revision_mismatch_fails_closed():
    product = Product.model_validate({
        **_product_payload(),
        "label_provenance": {
            "source_type": "manufacturer_label",
            "source_id": "manufacturer-a",
            "source_record_id": "record-a",
            "label_observed_at": "2026-08-01T00:00:00Z",
            "ingested_content_revision_sha256": "0" * 64,
            "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
            "lineage_status": "complete",
        },
    })

    assert product.label_provenance.lineage_status == "unavailable"
    assert product.label_provenance.ingested_content_revision_sha256 == "0" * 64
    assert product.label_provenance.content_revision_sha256 != "0" * 64
    assert content_revision_binding_status(product) == "ingested_revision_mismatch"
    assert not label_provenance_supports(product, PRODUCT_LABEL_FACT_GROUPS)


def test_missing_persisted_revision_cannot_be_completed_at_read_time():
    product = Product.model_validate({
        **_product_payload(),
        "label_provenance": {
            "source_type": "manufacturer_label",
            "source_id": "manufacturer-a",
            "source_record_id": "record-a",
            "label_observed_at": "2026-08-01T00:00:00Z",
            "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
            "lineage_status": "complete",
        },
    })

    assert product.label_provenance.content_revision_sha256
    assert product.label_provenance.lineage_status == "unavailable"


def test_missing_explicit_lineage_status_never_auto_promotes_partial():
    payload = _product_payload()
    ingested_revision = product_label_content_revision_sha256(
        Product.model_validate(payload)
    )
    product = Product.model_validate({
        **payload,
        "label_provenance": {
            "source_type": "manufacturer_label",
            "source_id": "manufacturer-a",
            "source_record_id": "record-a",
            "label_observed_at": datetime.now(timezone.utc).isoformat(),
            "ingested_content_revision_sha256": ingested_revision,
            "covered_fact_groups": ["ingredients", "allergens"],
        },
    })

    assert product.label_provenance.lineage_status == "unavailable"
    assert not label_provenance_supports(product, ["ingredients", "allergens"])


def test_aliases_are_consumed_and_aware_times_are_normalized_to_utc():
    base = Product.model_validate(_product_payload())
    product = Product.model_validate({
        **_product_payload(),
        "productLabelProvenance": {
            "sourceType": "manufacturer_label",
            "sourceId": " source-a ",
            "sourceRecordId": " record-a ",
            "labelObservedAt": "2026-08-20T09:00:00+09:00",
            "upstreamUpdatedAt": "2026-08-19T18:00:00-06:00",
            "ingestedContentRevisionSha256": (
                base.label_provenance.content_revision_sha256
            ),
            "coveredFactGroups": list(PRODUCT_LABEL_FACT_GROUPS),
            "lineageStatus": "complete",
        },
    })

    provenance = product.label_provenance
    assert provenance.source_id == "source-a"
    assert provenance.source_record_id == "record-a"
    assert provenance.label_observed_at == datetime(
        2026, 8, 20, 0, 0, tzinfo=timezone.utc
    )
    assert provenance.upstream_updated_at == datetime(
        2026, 8, 20, 0, 0, tzinfo=timezone.utc
    )
    assert provenance.lineage_status == "complete"


def test_raw_naive_time_is_rejected_but_mysql_datetime_contract_attaches_utc():
    with pytest.raises(ValidationError, match="explicit UTC offset"):
        ProductLabelProvenance(
            label_observed_at="2026-08-20T09:00:00",
        )

    product = normalize_product_payload(
        {
            "product_name": "database row",
            "label_observed_at": datetime(2026, 8, 20, 9, 0),
            "label_source_type": "internal_catalog",
            "label_source_id": "catalog-a",
            "label_source_record_id": "row-1",
            "label_covered_fact_groups": '["identity"]',
        },
        repository_record=True,
    )
    assert product is not None
    assert product.label_provenance.label_observed_at == datetime(
        2026, 8, 20, 9, 0, tzinfo=timezone.utc
    )
    assert product.label_provenance.lineage_status == "unavailable"


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("label_source_type", "made_up_source"),
        ("label_source_id", "https://private.example/feed?token=secret"),
        ("label_source_record_id", "operator@example.com"),
        ("upstream_revision", "internal path/value"),
        ("ingested_content_revision_sha256", "not-a-sha256"),
        ("label_lineage_status", "verified"),
    ],
)
def test_malformed_repository_provenance_downgrades_without_losing_product(
    bad_field,
    bad_value,
):
    product = normalize_product_payload(
        {
            **_product_payload(),
            "label_source_type": "manufacturer_label",
            "label_source_id": "source-a",
            "label_source_record_id": "row-a",
            "label_observed_at": datetime.now(timezone.utc),
            "label_lineage_status": "complete",
            "label_covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
            bad_field: bad_value,
        },
        repository_record=True,
    )

    assert product is not None
    assert product.product_name == "계보 테스트 바"
    assert product.label_provenance.lineage_status == "unavailable"
    assert not label_provenance_supports(product, PRODUCT_LABEL_FACT_GROUPS)


def test_sqlite_style_naive_timestamp_string_is_safely_unavailable():
    product = normalize_product_payload(
        {
            **_product_payload(),
            "label_source_type": "manufacturer_label",
            "label_source_id": "source-a",
            "label_source_record_id": "row-a",
            "label_observed_at": "2026-09-01 00:00:00",
            "label_lineage_status": "complete",
        },
        repository_record=True,
    )

    assert product is not None
    assert product.label_provenance.lineage_status == "unavailable"


@pytest.mark.parametrize("field", ["source_id", "source_record_id", "upstream_revision"])
def test_whitespace_only_trace_identifiers_are_rejected(field):
    with pytest.raises(ValidationError):
        ProductLabelProvenance.model_validate({field: "   "})


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "https://catalog.example/private?id=1",
        "operator@example.com",
        "record with spaces",
        "record\nheader",
    ],
)
@pytest.mark.parametrize("field", ["source_id", "source_record_id", "upstream_revision"])
def test_public_lineage_identifiers_reject_sensitive_free_form_values(
    field,
    unsafe_value,
):
    with pytest.raises(ValidationError):
        ProductLabelProvenance.model_validate({field: unsafe_value})


def test_mysql_binding_migration_enforces_public_safe_lineage_identifiers():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "20260903_product_label_revision_binding.sql"
    ).read_text(encoding="utf-8")

    assert migration.count("^[A-Za-z0-9][A-Za-z0-9._-]*$") == 3
    for column in (
        "label_source_id",
        "label_source_record_id",
        "upstream_revision",
    ):
        assert f"{column} REGEXP" in migration


def test_post_construction_mutation_invalidates_lineage_and_public_trust():
    product = _traced_product()
    original_revision = product.label_provenance.content_revision_sha256
    assert label_provenance_supports(product, PRODUCT_LABEL_FACT_GROUPS)

    product.nutrition["sodium"] = 999

    assert effective_label_lineage_status(product) == "unavailable"
    assert not label_provenance_supports(product, ["nutrition"])
    public_product = product.model_dump(mode="json")
    assert public_product["label_provenance"]["lineage_status"] == "unavailable"
    assert (
        public_product["label_provenance"]["content_revision_sha256"]
        != original_revision
    )

    assessed = attach_response_assessment(
        ChatResponse(
            intent="nutrition_explain",
            answer="수치 표시",
            risk_level="unknown",
            assessment_status="incomplete",
        ),
        profiles=[_complete_profile()],
        product=product,
    )
    assert assessed.product_data_trust is not None
    assert assessed.product_data_trust.label_lineage_status == "unavailable"
    assert assessed.assessment_summary is not None
    assert assessed.assessment_summary.product_data_verified is False


def test_partial_lineage_only_authorizes_its_covered_fact_groups():
    product = _traced_product(
        covered_fact_groups=["identity", "nutrition", "nutrition_basis"],
        lineage_status="partial",
    )

    assert label_provenance_supports(product, ["nutrition", "nutrition_basis"])
    assert not label_provenance_supports(product, ["ingredients", "allergens"])


def test_covered_fact_groups_cannot_be_mutated_to_elevate_lineage():
    product = _traced_product(
        covered_fact_groups=["identity"],
        lineage_status="partial",
    )

    assert isinstance(product.label_provenance.covered_fact_groups, tuple)
    assert not label_provenance_supports(product, ["ingredients"])
    with pytest.raises(AttributeError):
        product.label_provenance.covered_fact_groups.extend(["ingredients"])
    assert not label_provenance_supports(product, ["ingredients"])


def _product_observed_at(
    observed_at: datetime,
    *,
    source_type="manufacturer_label",
    upstream_updated_at=None,
) -> Product:
    payload = _product_payload()
    revision = product_label_content_revision_sha256(Product.model_validate(payload))
    return Product.model_validate({
        **payload,
        "label_provenance": {
            "source_type": source_type,
            "source_id": "freshness-test",
            "source_record_id": "record-1",
            "label_observed_at": observed_at.isoformat(),
            "upstream_updated_at": (
                upstream_updated_at.isoformat()
                if upstream_updated_at is not None
                else None
            ),
            "ingested_content_revision_sha256": revision,
            "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
            "lineage_status": "complete",
        },
    })


def test_freshness_policy_exact_boundary_future_stale_and_source_eligibility(
    monkeypatch,
):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS", "30")

    assert label_freshness_status(
        _product_observed_at(now - timedelta(days=30)),
        now=now,
    ) == "within_policy"
    assert label_freshness_status(
        _product_observed_at(now - timedelta(days=30, microseconds=1)),
        now=now,
    ) == "stale"
    assert label_freshness_status(
        _product_observed_at(now + timedelta(microseconds=1)),
        now=now,
    ) == "future_observation"
    assert label_freshness_status(
        _product_observed_at(now, source_type="user_supplied_label"),
        now=now,
    ) == "source_not_eligible"
    arbitrary_manufacturer = _product_observed_at(now)
    arbitrary_manufacturer = Product.model_validate({
        **arbitrary_manufacturer.model_dump(mode="json"),
        "label_provenance": {
            **arbitrary_manufacturer.label_provenance.model_dump(mode="json"),
            "source_id": "unregistered-manufacturer-feed",
        },
    })
    assert label_freshness_status(
        arbitrary_manufacturer,
        now=now,
    ) == "source_not_eligible"


def test_invalid_temporal_order_and_invalid_policy_are_fail_closed(monkeypatch):
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    invalid_order = _product_observed_at(
        now,
        upstream_updated_at=now + timedelta(days=1),
    )
    assert label_freshness_status(invalid_order) == "invalid_temporal_order"
    assert not label_provenance_supports(invalid_order, ["ingredients"])

    monkeypatch.setenv("FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS", "not-an-int")
    current = _product_observed_at(now)
    assert label_freshness_status(current) == "policy_configuration_invalid"
    assert not label_provenance_supports(current, ["ingredients"])

    monkeypatch.setenv("FOOD_CHATBOT_PRODUCT_LABEL_MAX_AGE_DAYS", "180")
    monkeypatch.setenv(
        "FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES",
        "malformed-registry-entry",
    )
    assert label_freshness_status(current) == (
        "source_registry_configuration_invalid"
    )
    assert not label_provenance_supports(current, ["ingredients"])


def test_missing_lineage_blocks_negative_ingredient_claim_but_keeps_positive_allergen():
    legacy = Product.model_validate(_product_payload(
        allergy=[],
        allergy_warning=None,
        rawmtrl_nm="귀리, 코코아",
    ))
    negative = handle_ingredient_check(None, legacy, "땅콩 들어 있어?")

    assert negative.risk_level == "unknown"
    assert "보이지 않아요" not in negative.answer
    assert "확인되지 않았어요" not in negative.answer
    assert "수집 당시 저장된 내용 기록" in negative.answer

    positive_product = Product.model_validate(_product_payload(
        rawmtrl_nm="땅콩, 코코아",
        allergy=["땅콩"],
        allergy_warning="땅콩 함유",
    ))
    positive = build_can_i_eat_result(
        _complete_profile(allergies=["땅콩"]),
        positive_product,
        "먹어도 돼?",
    )
    assessed = attach_response_assessment(
        positive,
        profiles=[_complete_profile(allergies=["땅콩"])],
        product=positive_product,
    )

    assert assessed.risk_level == "high"
    assert assessed.assessment_summary is not None
    assert assessed.assessment_summary.decision == "avoid"
    assert assessed.assessment_summary.evidence_status == "limited"


def test_untraceable_product_summary_labels_raw_db_facts_without_internal_jargon():
    product = Product.model_validate({
        **_product_payload(),
        "product_name": "표시 테스트 바",
        "facts_verified": True,
    })

    response = handle_product_summary(None, product, "제품 설명해줘")

    assert "DB에 등록된" in response.answer
    assert "우유" in response.answer
    assert "수집 당시 저장된 내용 기록" in response.answer
    assert "현재 판매 포장과 같다고 볼 수 없어요" in response.answer
    assert not any(
        internal_term in response.answer
        for internal_term in ("facts", "snapshot", "unavailable", "계보")
    )


def test_missing_lineage_blocks_same_basis_comparison_winner(monkeypatch):
    left = Product.model_validate(_product_payload(product_name="A"))
    right = Product.model_validate(_product_payload(
        product_name="B",
        barcode="8800000007002",
        report_number="202600007002",
        nutrition={"sodium": 100},
    ))

    assert not nutrition_values_comparable(left, right)
    from app.services import comparison_context
    monkeypatch.setattr(
        comparison_context,
        "resolve_comparison",
        lambda message, _product, _state: ([left, right], None, {}, message),
    )
    response = handle_food_compare(None, left, "A와 B 나트륨 비교해줘")
    assert "수집 당시 저장된 내용 기록" in response.answer
    assert "우승 제품을 고르지 않았" in response.answer
    assert not any(reason.startswith("비교 결과:") for reason in response.reasons)


@pytest.mark.parametrize(
    "answer",
    [
        "최신 라벨 정보에는 땅콩 성분이 없어요.",
        "현재   판매 중인 제품에는 땅콩이 들어 있지 않아요.",
        "가장   최근 데이터를 보면 알레르기 성분이 없습니다.",
    ],
)
def test_grounding_blocks_freshness_and_absence_claims_for_untraceable_label(answer):
    product = Product.model_validate(_product_payload(
        allergy=[],
        allergy_warning=None,
    ))
    rule = ChatResponse(
        intent="ingredient_check",
        answer="포함 여부를 판단하기 어려워요.",
        risk_level="unknown",
        assessment_status="incomplete",
    )
    failures = _grounding_failure_types(answer, rule, product, [])

    assert "unsupported_product_freshness_claim" in failures
    assert "untraceable_label_absence_claim" in failures
    fallback = _fallback_with_grounding_reason(
        rule,
        validation_failure_types=failures,
    )
    assert fallback.critical_safety_violation is True
    assert fallback.answer_source == "rules"


@pytest.mark.parametrize(
    "generated_text",
    [
        "편하게 드셔도 되는 안전한 선택이에요.",
        "우유를 뺀 레시피라 걱정 없이 즐겨도 돼요.",
        "당류가 무난해 혈당 관리용으로 알맞아요.",
    ],
)
def test_product_chat_never_calls_free_llm_rewrite(monkeypatch, generated_text):
    called = False

    def _unexpected_generate(_prompt):
        nonlocal called
        called = True
        return generated_text

    monkeypatch.setattr(hybrid, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(hybrid, "generate_text", _unexpected_generate)
    rule = ChatResponse(
        task_type="product_chat",
        intent="can_i_eat",
        answer="규칙 결론을 유지해요.",
        risk_level="low",
        assessment_status="complete",
    )

    result = enhance_rule_response(
        rule,
        "먹어도 돼?",
        _complete_profile(),
        _traced_product(),
    )
    assert called is False
    assert result.answer == rule.answer
    assert result.answer_source == "rules"
    assert result.fallback_reason == "product_rule_template_preserved"


def test_prompt_states_hash_and_missing_lineage_boundaries():
    product = Product.model_validate(_product_payload())
    rule = ChatResponse(
        intent="ingredient_check",
        answer="포함 여부를 판단하기 어려워요.",
        risk_level="unknown",
        assessment_status="incomplete",
    )

    prompt = build_rule_explainer_prompt("땅콩 없어?", rule, None, product)
    assert "출처 권위·최신성의 증명이 아님" in prompt
    assert "부재 주장을 하지 말 것" in prompt
    assert "'최신'" in prompt


def test_saved_response_snapshot_round_trips_label_provenance_and_hash():
    product = _traced_product()
    public_product = public_product_payload(product)
    response = attach_response_assessment(
        ChatResponse(
            intent="product_summary",
            answer="제품 정보예요.",
            risk_level="unknown",
            assessment_status="incomplete",
            current_product=public_product,
        ),
        product=product,
    )

    snapshot = response.model_dump(mode="json")
    restored = ChatResponse.model_validate(snapshot)
    restored_trust = restored.current_product["label_trust"]
    assert restored_trust["lineage_status"] == "complete"
    assert restored_trust["label_observed_at"].tzinfo is not None
    assert restored_trust["content_revision_sha256"] == (
        product.label_provenance.content_revision_sha256
    )
    assert "label_provenance" not in restored.current_product
    assert restored.product_data_trust is not None
    assert restored.product_data_trust.repository_identity_matched is True
    assert "label_provenance" not in restored.product_data_trust.model_json_schema()[
        "properties"
    ]
    assert restored.product_data_trust.content_revision_meaning == (
        "normalized_snapshot_identifier_not_freshness_proof"
    )
