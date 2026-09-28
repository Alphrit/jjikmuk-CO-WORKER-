from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.models import Product, Profile
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.infrastructure.llm import set_llm_client
from app.prompts.builder import _product_summary, _profile_summary, _profiles_summary
from app.services.chat import build_rule_based_response, generate_chat_response
from app.services.hybrid import (
    _grounding_failure_types,
    _has_disease_treatment_claim,
    _sources_from_entries,
)
from app.services.profile_context import merge_profiles


@pytest.fixture(autouse=True)
def isolate_llm_and_rag(monkeypatch):
    set_llm_client(None)
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    monkeypatch.delenv("LLM_API_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with patch("app.services.hybrid.get_rag_context_entries", return_value=[]):
        yield
    set_llm_client(None)


def complete_profile(name: str = "나", allergies=None, conditions=None) -> Profile:
    return Profile(
        profile_name=name,
        allergies=[] if allergies is None else allergies,
        conditions=[] if conditions is None else conditions,
        special_diets=[],
    )


def with_test_lineage(product: Product) -> Product:
    payload = product.model_dump(
        mode="json",
        exclude={"label_provenance"},
    )
    revision = product_label_content_revision_sha256(Product.model_validate(payload))
    payload["label_provenance"] = {
        "source_type": "manufacturer_label",
        "source_id": "synthetic-safety-test-fixture",
        "source_record_id": f"safety-fixture-{revision[:12]}",
        "label_observed_at": (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        "ingested_content_revision_sha256": revision,
        "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
        "lineage_status": "complete",
    }
    return Product.model_validate(payload)


def milk_product(*, cross_contact: bool = False, per_serving: bool = False) -> Product:
    if cross_contact:
        return Product(
            product_name="교차오염 테스트 과자",
            rawmtrl_nm="감자전분, 설탕",
            allergy_warning="같은 제조시설에서 우유를 사용한 제품과 함께 제조",
            nutrition={"kcal": 100},
        )
    product = Product(
        product_name="우유 과자",
        rawmtrl_nm="밀가루, 우유",
        allergy=["우유"],
        nutrition={"kcal": 100, "sodium": 2000},
        nutrition_basis={"basis_type": "per_serving"} if per_serving else {},
    )
    return with_test_lineage(product) if per_serving else product


def answer_client(answer: str, intent: str = "can_i_eat"):
    def client(prompt: str) -> str:
        if "의도 분류기" in prompt:
            return f'{{"intent":"{intent}"}}'
        return answer

    return client


def test_synonym_allergy_alert_survives_with_llm_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    result = generate_chat_response(
        profile=complete_profile(allergies=["달걀"]),
        product=Product(
            product_name="계란 과자",
            rawmtrl_nm="밀가루, 계란",
            allergy=["계란"],
            nutrition={"kcal": 100},
        ),
        message="제품 설명해줘",
    )

    assert result.answer_source == "rules"
    assert result.risk_level == "high"
    assert result.fallback_reason is None
    assert "달걀" in result.answer and "계란" in result.answer
    assert "피하는 편" in result.answer


def test_product_template_gate_keeps_trusted_synonym_allergy_alert():
    set_llm_client(answer_client("이 제품은 당뇨를 치료합니다.", "product_summary"))
    result = generate_chat_response(
        profile=complete_profile(allergies=["달걀"]),
        product=Product(
            product_name="계란 과자",
            rawmtrl_nm="밀가루, 계란",
            allergy=["계란"],
            nutrition={"kcal": 100},
        ),
        message="제품 설명해줘",
    )

    assert result.answer_source == "rules"
    assert result.risk_level == "high"
    assert result.fallback_reason == "product_rule_template_preserved"
    assert result.validation_failure_types == ["free_product_rewrite_disabled"]
    assert "달걀" in result.answer and "계란" in result.answer
    assert "피하는 편" in result.answer


@pytest.mark.parametrize(
    "unsafe_answer",
    [
        "안전합니다.",
        "드셔도 됩니다.",
        "우유가 있지만 섭취 가능합니다.",
        "알레르기 위험이 낮아요.",
        "우유가 포함되어 있지 않습니다.",
        "알레르기 유발 성분은 없습니다.",
        "안전합니다. 안전하다고 단정할 수는 없습니다.",
        "안심하셔도 돼요.",
        "섭취하셔도 무방해요.",
        "걱정 안 하셔도 돼요.",
        "드시는 데 지장이 없습니다.",
        "주의하면 안심하고 드셔도 됩니다.",
    ],
)
def test_high_allergy_reassurance_and_absence_contradictions_are_blocked(unsafe_answer):
    product = milk_product()
    profile = complete_profile(allergies=["우유"])
    rule = build_rule_based_response(
        intent="can_i_eat",
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )
    failures = _grounding_failure_types(unsafe_answer, rule, product, [])
    assert set(failures) & {
        "risk_conclusion_mismatch",
        "allergen_presence_contradiction",
    }

    set_llm_client(answer_client(unsafe_answer))
    result = generate_chat_response(
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )

    assert result.answer_source == "rules"
    assert result.risk_level == "high"
    assert result.fallback_reason == "product_rule_template_preserved"
    assert result.validation_failure_types == ["free_product_rewrite_disabled"]
    assert unsafe_answer not in result.answer


@pytest.mark.parametrize(
    "unsafe_answer",
    [
        "먹어도 괜찮아요.",
        "교차오염 가능성이 있지만 먹어도 괜찮아요.",
        "교차오염에 주의하면 드셔도 됩니다.",
    ],
)
def test_medium_cross_contact_reassurance_is_blocked(unsafe_answer):
    product = milk_product(cross_contact=True)
    profile = complete_profile(allergies=["우유"])
    rule = build_rule_based_response(
        intent="can_i_eat",
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )
    assert "risk_conclusion_mismatch" in _grounding_failure_types(
        unsafe_answer,
        rule,
        product,
        [],
    )

    set_llm_client(answer_client(unsafe_answer))
    result = generate_chat_response(
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )

    assert result.risk_level == "medium"
    assert result.answer_source == "rules"
    assert result.validation_failure_types == ["free_product_rewrite_disabled"]
    assert "교차오염" in result.answer


def test_explicit_safety_denial_is_preserved():
    safe_denial = (
        "우유가 포함되어 있지 않다고 단정할 수 없습니다. "
        "우유가 확인되어 섭취를 피해야 합니다."
    )
    product = milk_product()
    profile = complete_profile(allergies=["우유"])
    rule = build_rule_based_response(
        intent="can_i_eat",
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )
    failures = _grounding_failure_types(safe_denial, rule, product, [])
    assert "risk_conclusion_mismatch" not in failures
    assert "allergen_presence_contradiction" not in failures

    set_llm_client(answer_client(safe_denial))
    result = generate_chat_response(
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )

    assert result.answer_source == "rules"
    assert result.validation_failure_types == ["free_product_rewrite_disabled"]
    assert safe_denial not in result.answer


@pytest.mark.parametrize(
    "safe_denial",
    [
        "안심하셔도 된다고 단정할 수 없습니다. 우유가 확인되어 섭취를 피해야 합니다.",
        "섭취하셔도 무방하다는 뜻이 아닙니다. 우유가 확인되어 섭취를 피해야 합니다.",
        "걱정하지 않아도 된다고 판단할 수 없습니다. 섭취를 피하세요.",
        "드시는 데 지장이 없다고 말할 수 없습니다. 섭취를 피하세요.",
    ],
)
def test_semantic_reassurance_denials_do_not_trigger_false_positive(safe_denial):
    product = milk_product()
    profile = complete_profile(allergies=["우유"])
    rule = build_rule_based_response(
        intent="can_i_eat",
        profile=profile,
        product=product,
        message="이거 먹어도 돼?",
    )
    assert "risk_conclusion_mismatch" not in _grounding_failure_types(
        safe_denial,
        rule,
        product,
        [],
    )


@pytest.mark.parametrize(
    "medical_claim",
    [
        "이 제품은 당뇨를 치료합니다. 예방 효과는 단정할 수 없습니다.",
        "이 제품은 당뇨를 치료하고 예방 효과는 단정할 수 없습니다.",
        "면역력을 개선해 줘요.",
        "암을 예방하는 효과가 있어요.",
        "체중 감량 효과가 있습니다.",
        "혈압을 낮춰 줍니다.",
        "면역력을 높여 줍니다.",
        "혈압을 떨어뜨려 줍니다.",
        "콜레스테롤 관리에 효과적입니다.",
        "지방간에 좋습니다.",
        "이 제품은 암에 특효약입니다.",
        "이 제품을 먹으면 신장 기능이 회복됩니다.",
        "이 제품은 혈압을 정상화합니다.",
        "이 제품은 당뇨에 탁월해요.",
    ],
)
def test_medical_and_effect_claims_are_checked_per_clause(medical_claim):
    product = Product(
        product_name="일반 과자",
        rawmtrl_nm="밀가루, 설탕",
        allergy=["밀"],
        nutrition={"kcal": 100},
    )
    rule = build_rule_based_response(
        intent="product_summary",
        profile=complete_profile(),
        product=product,
        message="제품 설명해줘",
    )
    assert "disease_treatment_claim" in _grounding_failure_types(
        medical_claim,
        rule,
        product,
        [],
    )


@pytest.mark.parametrize(
    "claim_denial",
    [
        "이 제품이 당뇨를 치료한다고 단정할 수 없습니다.",
        "면역력을 높여 준다고 단정할 수 없습니다.",
        "혈압을 떨어뜨려 준다고 단정할 수 없습니다.",
        "콜레스테롤 관리에 효과적이라고 단정할 수 없습니다.",
        "지방간에 좋다고 단정할 수 없습니다.",
        "이 제품이 암에 특효약이라는 근거는 없습니다.",
        "이 제품이 신장 기능을 회복시킨다고 단정할 수 없습니다.",
        "이 제품이 혈압을 정상화한다는 근거는 확인되지 않았습니다.",
        "이 제품이 당뇨에 탁월하다는 뜻이 아닙니다.",
    ],
)
def test_explicit_medical_denial_does_not_trigger_false_positive(claim_denial):
    safe_denial = f"{claim_denial} 확인된 원재료 정보만 참고해야 합니다."
    product = Product(
        product_name="일반 과자",
        rawmtrl_nm="밀가루, 설탕",
        allergy=["밀"],
        nutrition={"kcal": 100},
    )
    rule = build_rule_based_response(
        intent="product_summary",
        profile=complete_profile(),
        product=product,
        message="제품 설명해줘",
    )
    assert "disease_treatment_claim" not in _grounding_failure_types(
        safe_denial,
        rule,
        product,
        [],
    )


@pytest.mark.parametrize(
    "guidance",
    [
        "당류와 탄수화물은 혈당 관리에서 같이 보는 편이 좋아요.",
        "저칼로리는 열량, 저지방은 지방 표시를 먼저 확인하는 편이 좋아요.",
        "고혈압이 있다면 제품을 고를 때 나트륨 표시를 확인하는 것이 좋아요.",
    ],
)
def test_dietary_label_guidance_is_not_misclassified_as_medical_efficacy(guidance):
    assert _has_disease_treatment_claim(guidance) is False


def test_transient_symptom_improvement_warning_is_not_a_treatment_claim():
    assert _has_disease_treatment_claim(
        "증상이 좋아져도 다시 악화될 수 있으므로 응급실에서 진료를 받으세요."
    ) is False


def test_emergency_evaluation_warning_is_not_a_treatment_claim():
    assert _has_disease_treatment_claim(
        "증상이 잠시 좋아져도 응급 평가를 미루지 마세요."
    ) is False


def test_out_of_scope_medical_denial_is_not_a_treatment_claim():
    assert _has_disease_treatment_claim(
        "질환·임신·약물의 진단·치료 안내는 지원하지 않아요."
    ) is False


def test_full_scope_exclusion_is_not_a_treatment_claim():
    assert _has_disease_treatment_claim(
        "질환·임신·약물에 맞는 섭취 판단과 진단·치료 안내 및 "
        "식품과 무관한 범용 질문은 지원하지 않아요."
    ) is False


def test_product_symptom_improvement_claim_remains_blocked():
    assert _has_disease_treatment_claim(
        "이 제품을 먹으면 알레르기 증상이 좋아집니다."
    ) is True


def test_rag_sources_expose_only_bounded_internal_policy_identifiers():
    entries = [
        {"source": "internal-policy:nutrition/rules.md", "text": "one"},
        {"source": "internal-policy:nutrition/rules.md", "text": "duplicate"},
        {"source": "internal-policy:../secret.md", "text": "invalid"},
        {"source": "https://example.com/not-verified", "text": "invalid"},
    ]

    assert _sources_from_entries(entries) == [
        "internal-policy:nutrition/rules.md"
    ]


def test_product_template_gate_does_not_fetch_or_expose_rag_context():
    set_llm_client(answer_client("근거 문서에 표시된 참고 수치는 123mg입니다.", "product_summary"))
    rag_entries = [{
        "text": "근거 문서에 표시된 참고 수치는 123mg입니다.",
        "source": "verified_policy.md",
    }]
    with patch(
        "app.services.hybrid.get_rag_context_entries",
        return_value=rag_entries,
    ):
        result = generate_chat_response(
            profile=complete_profile(),
            product=Product(
                product_name="일반 과자",
                rawmtrl_nm="밀가루, 설탕",
                allergy=["밀"],
                nutrition={"kcal": 100},
            ),
            message="제품 설명해줘",
        )

    assert result.answer_source == "rules"
    assert "123mg" not in result.answer
    assert result.sources == []
    assert result.grounding_context == []
    assert "grounding_context" not in result.model_dump()


def test_prompt_summaries_distinguish_missing_from_explicit_empty():
    missing_profile = Profile(profile_name="미입력 프로필")
    explicit_empty_profile = complete_profile("명시적 빈 프로필")

    missing_summary = _profile_summary(missing_profile)
    explicit_summary = _profile_summary(explicit_empty_profile)
    multi_summary = _profiles_summary([missing_profile, explicit_empty_profile])
    product_summary = _product_summary(Product(product_name="정보 부족 제품"))

    assert "알레르기: 미입력/미확인" in missing_summary
    assert "알레르기: 없음(사용자가 명시적으로 입력)" in explicit_summary
    assert "미입력/미확인" in multi_summary
    assert "없음(사용자가 명시적으로 입력)" in multi_summary
    assert "알레르기 성분: 미확인" in product_summary
    assert "원재료명: 미확인" in product_summary
    assert "영양정보: 미확인" in product_summary
