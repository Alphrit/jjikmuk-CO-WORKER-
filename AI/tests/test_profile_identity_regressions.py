from datetime import datetime, timedelta, timezone

import pytest

from app.api.adapters import normalize_active_profiles, normalize_chat_request
from app.core.models import ChatRequest, Product, Profile
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.services.activity import log_chat_activity
from app.services.chat import generate_chat_response
from app.services.handlers import build_can_i_eat_result
from app.services.profile_context import active_profiles_from_inputs


def _complete_product() -> Product:
    return Product.model_validate({
        "product_name": "안전 정보가 있는 제품",
        "rawmtrl_nm": "쌀, 소금",
        "allergy": [],
        "nutrition": {
            "kcal": 100,
            "carbohydrate": 20,
            "sugars": 1,
            "fat": 1,
            "sodium": 100,
        },
    })


def _with_synthetic_label_lineage(product: Product) -> Product:
    payload = product.model_dump(mode="python")
    observed_at = datetime.now(timezone.utc) - timedelta(days=1)
    payload["label_provenance"] = {
        "source_type": "manufacturer_label",
        "source_id": "synthetic-safety-test-fixture",
        "source_record_id": "profile-identity-fixture",
        "label_observed_at": observed_at,
        "upstream_updated_at": observed_at - timedelta(days=1),
        "upstream_revision": "test-v1",
        "ingested_content_revision_sha256": (
            product_label_content_revision_sha256(product)
        ),
        "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
        "lineage_status": "complete",
    }
    return Product.model_validate(payload)


@pytest.mark.parametrize(
    "profile_field",
    ["profile", "raw_profile"],
)
def test_inactive_single_profile_is_not_restored_as_request_profile(
    profile_field: str,
):
    profile_payload = {
        "id": 701,
        "nickname": "비활성 프로필",
        "allergies": [],
        "conditions": [],
        "special_diets": [],
        "is_active": False,
    }
    request = ChatRequest(
        message="이거 먹어도 돼?",
        product=_complete_product(),
        **{profile_field: profile_payload},
    )

    assert normalize_active_profiles(request) == []

    profile, product, _, _ = normalize_chat_request(request)
    result = build_can_i_eat_result(profile, product, request.message)

    assert profile is None
    assert result.risk_level == "unknown"
    assert result.assessment_status == "incomplete"


def test_generic_id_is_only_a_profile_id_not_an_account_user_id():
    profile = Profile.model_validate({
        "id": 701,
        "nickname": "프로필 행",
        "allergies": [],
        "conditions": [],
        "special_diets": [],
    })

    assert profile.profile_id == 701
    assert profile.user_id is None


@pytest.mark.parametrize(
    ("user_key", "user_value"),
    [
        ("user_id", 42),
        ("userId", 43),
        ("account_id", 44),
        ("accountId", 45),
    ],
)
def test_explicit_account_user_id_aliases_remain_supported(
    user_key: str,
    user_value: int,
):
    profile = Profile.model_validate({
        "id": 701,
        user_key: user_value,
    })

    assert profile.profile_id == 701
    assert profile.user_id == user_value


class _CapturingActivityRepository:
    def __init__(self):
        self.saved = []

    def save(self, activity):
        self.saved.append(activity)


def test_trusted_activity_does_not_treat_profile_row_id_as_user_id():
    repository = _CapturingActivityRepository()
    profile = Profile.model_validate({"id": 701})

    log_chat_activity(
        profile=profile,
        product=None,
        barcode=None,
        report_number=None,
        message="기록 테스트",
        intent="general_chat",
        trusted_user_id=None,
        repository=repository,
    )

    assert len(repository.saved) == 1
    assert repository.saved[0].user_id is None


def test_trusted_activity_uses_only_request_verified_user_id():
    repository = _CapturingActivityRepository()
    profile = Profile.model_validate({"id": 701, "userId": 42})

    log_chat_activity(
        profile=profile,
        product=None,
        barcode=None,
        report_number=None,
        message="기록 테스트",
        intent="general_chat",
        trusted_user_id=42,
        repository=repository,
    )

    assert len(repository.saved) == 1
    assert repository.saved[0].user_id == 42


def _wrapped_inactive_profile() -> dict:
    return {
        "user_id": 42,
        "is_active": False,
        "data": {
            "id": 701,
            "userId": 999,
            "nickname": "비활성 래퍼 프로필",
            "allergies": ["우유"],
            "conditions": [],
            "special_diets": [],
            "isActive": True,
        },
    }


@pytest.mark.parametrize("profile_field", ["profile", "raw_profile"])
def test_outer_inactive_tombstone_wins_for_single_data_wrapped_profile(profile_field):
    request = ChatRequest(
        message="이거 먹어도 돼?",
        product=_complete_product(),
        **{profile_field: _wrapped_inactive_profile()},
    )

    assert normalize_active_profiles(request) == []
    profile, product, _, _ = normalize_chat_request(request)
    result = build_can_i_eat_result(profile, product, request.message)

    assert profile is None
    assert result.risk_level == "unknown"
    assert result.assessment_status == "incomplete"


@pytest.mark.parametrize("profile_field", ["profiles", "raw_profiles"])
def test_outer_inactive_tombstone_wins_for_list_data_wrapped_profile(profile_field):
    request = ChatRequest(
        message="이거 먹어도 돼?",
        product=_complete_product(),
        **{profile_field: [_wrapped_inactive_profile()]},
    )

    assert normalize_active_profiles(request) == []
    profile, product, _, _ = normalize_chat_request(request)
    result = build_can_i_eat_result(profile, product, request.message)

    assert profile is None
    assert result.risk_level == "unknown"
    assert result.assessment_status == "incomplete"


@pytest.mark.parametrize("outer_user_id", [42, None])
def test_outer_canonical_identity_wins_over_inner_data_alias(
    outer_user_id,
):
    repository = _CapturingActivityRepository()
    profile = Profile.model_validate({
        "user_id": outer_user_id,
        "is_active": True,
        "data": {
            "id": 701,
            "userId": 999,
            "isActive": False,
            "nickname": "내부 프로필",
            "allergies": [],
            "conditions": [],
            "special_diets": [],
        },
    })

    assert profile.user_id == outer_user_id
    assert profile.is_active is True
    assert profile.profile_id == 701

    log_chat_activity(
        profile=profile,
        product=None,
        barcode=None,
        report_number=None,
        message="기록 테스트",
        intent="general_chat",
        trusted_user_id=outer_user_id,
        repository=repository,
    )

    assert repository.saved[0].user_id == outer_user_id


@pytest.mark.parametrize(
    "explicit_value",
    [
        "inactive",
        "disabled",
        "off",
        "no",
        "false",
        "0",
        0,
        False,
        "unknown-state",
        -1,
        2,
        None,
        "",
        [],
        {},
    ],
)
def test_explicit_invalid_or_inactive_status_fails_closed(explicit_value):
    profile = Profile.model_validate({
        "id": 701,
        "is_active": explicit_value,
    })

    assert profile.is_active is False


@pytest.mark.parametrize(
    "explicit_value",
    [True, 1, "true", "yes", "on", "active", "enabled"],
)
def test_explicit_supported_active_status_remains_active(explicit_value):
    profile = Profile.model_validate({
        "id": 701,
        "is_active": explicit_value,
    })

    assert profile.is_active is True


def test_missing_active_status_is_the_only_implicit_active_default():
    assert Profile.model_validate({"id": 701}).is_active is True


@pytest.mark.parametrize("profile_field", ["profiles", "raw_profiles"])
@pytest.mark.parametrize("inactive_first", [True, False])
def test_inactive_stable_id_tombstones_active_clone_in_any_list_order(
    profile_field,
    inactive_first,
):
    inactive = {
        "id": 701,
        "nickname": "비활성 본체",
        "allergies": ["우유"],
        "is_active": False,
    }
    active = {
        "id": 701,
        "nickname": "활성 복제본",
        "allergies": ["땅콩"],
        "is_active": True,
    }
    values = [inactive, active] if inactive_first else [active, inactive]
    request = ChatRequest(message="테스트", **{profile_field: values})

    assert normalize_active_profiles(request) == []


@pytest.mark.parametrize("primary_field", ["profile", "raw_profile"])
def test_inactive_primary_tombstones_same_id_active_profile_list(primary_field):
    request = ChatRequest(
        message="테스트",
        profiles=[
            {
                "id": 701,
                "nickname": "활성 복제본",
                "allergies": ["땅콩"],
                "is_active": True,
            },
        ],
        **{
            primary_field: {
                "id": 701,
                "nickname": "비활성 본체",
                "allergies": ["우유"],
                "is_active": False,
            },
        },
    )

    assert normalize_active_profiles(request) == []


@pytest.mark.parametrize(
    "request_payload",
    [
        {
            "profile": {"id": 701, "is_active": True},
            "raw_profile": {"id": 701, "is_active": False},
        },
        {
            "profile": {"id": 701, "is_active": True},
            "raw_profiles": [{"id": 701, "is_active": False}],
        },
        {
            "profiles": [{"id": 701, "is_active": True}],
            "raw_profile": {"id": 701, "is_active": False},
        },
        {
            "profiles": [{"id": 701, "is_active": True}],
            "raw_profiles": [{"id": 701, "is_active": False}],
        },
    ],
)
def test_inactive_tombstone_applies_across_typed_and_raw_sources(request_payload):
    request = ChatRequest(message="테스트", **request_payload)

    assert normalize_active_profiles(request) == []


@pytest.mark.parametrize("inactive_first", [True, False])
def test_downstream_active_profiles_honors_stable_id_tombstone(inactive_first):
    inactive = Profile(
        profile_id=701,
        profile_name="비활성 본체",
        allergies=["우유"],
        is_active=False,
    )
    active = Profile(
        profile_id=701,
        profile_name="활성 복제본",
        allergies=["땅콩"],
        is_active=True,
    )
    values = [inactive, active] if inactive_first else [active, inactive]

    assert active_profiles_from_inputs(None, values) == []
    assert active_profiles_from_inputs(inactive, [active]) == []


def test_anonymous_inactive_profile_does_not_tombstone_unrelated_anonymous_active():
    inactive = Profile(
        profile_name="익명 비활성",
        allergies=["우유"],
        is_active=False,
    )
    active = Profile(
        profile_name="익명 활성",
        allergies=["땅콩"],
        is_active=True,
    )

    assert active_profiles_from_inputs(None, [inactive, active]) == [active]


def test_distinct_active_safety_payloads_with_same_id_are_both_preserved():
    milk_profile = Profile(
        profile_id=701,
        profile_name="동일 ID",
        allergies=["우유"],
        is_active=True,
    )
    peanut_profile = Profile(
        profile_id=701,
        profile_name="동일 ID",
        allergies=["땅콩"],
        is_active=True,
    )

    assert active_profiles_from_inputs(None, [milk_profile, peanut_profile]) == [
        milk_profile,
        peanut_profile,
    ]


def test_duplicate_profile_identity_keeps_allergy_warning_on_its_owner(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    milk_profile = Profile(
        profile_id=701,
        profile_name="동일 ID",
        allergies=["우유"],
        conditions=["고혈압"],
        special_diets=[],
        is_active=True,
    )
    non_allergic_profile = Profile(
        profile_id=701,
        profile_name="동일 ID",
        allergies=[],
        conditions=["고혈압"],
        special_diets=[],
        is_active=True,
    )
    product = _with_synthetic_label_lineage(Product(
        product_name="우유 함유 고나트륨 과자",
        rawmtrl_nm="밀가루, 우유",
        allergy=["우유"],
        nutrition={"kcal": 100, "sodium": 2000},
        nutrition_basis={"basis_type": "per_serving"},
        facts_verified=True,
    ))

    result = generate_chat_response(
        profile=milk_profile,
        active_profiles=[milk_profile, non_allergic_profile],
        product=product,
        message="이 제품 먹어도 돼?",
    )

    assert result.risk_level == "high"
    assert len(result.per_profile_results) == 2
    assert result.per_profile_results[0]["risk_level"] == "high"
    assert "우유" in result.per_profile_results[0]["answer"]
    assert "피하는 것이 좋아요" in result.per_profile_results[0]["answer"]
    assert result.per_profile_results[1]["risk_level"] != "high"
    assert "프로필 알러지 확인" not in result.per_profile_results[1]["answer"]
