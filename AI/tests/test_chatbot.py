"""Small end-to-end smoke tests for the current allergy-first chatbot.

Focused contracts live in the dedicated conversation, provenance, recipe,
recommendation, request-guard, and security test modules.
"""

from datetime import datetime, timedelta, timezone

from app.core.models import Product, Profile
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.infrastructure.llm import set_llm_client
from app.services.chat import generate_chat_response


def _profile(name="나", *, allergies=None, diets=None, dislikes=None):
    return Profile(
        profile_name=name,
        allergies=allergies or [],
        special_diets=diets or [],
        disliked_ingredients=dislikes or [],
    )


def _verified_product(
    name="새우깡",
    *,
    product_id=7001,
    ingredients="밀가루, 새우, 소금",
    allergy=None,
    warning=None,
    nutrition=None,
    nutrition_basis=None,
):
    raw = Product(
        product_id=product_id,
        product_name=name,
        barcode=f"88000000{product_id}",
        report_number=f"2026{product_id:08d}",
        rawmtrl_nm=ingredients,
        allergy=allergy or [],
        allergy_warning=warning,
        nutrition=nutrition or {},
        nutrition_basis=nutrition_basis,
        prdlst_dcnm="과자",
        facts_verified=True,
        product_found=True,
    )
    observed = datetime.now(timezone.utc) - timedelta(days=1)
    return Product.model_validate({
        **raw.model_dump(mode="python"),
        "label_provenance": {
            "source_type": "manufacturer_label",
            "source_id": "synthetic-safety-test-fixture",
            "source_record_id": f"smoke-{product_id}",
            "label_observed_at": observed,
            "upstream_updated_at": observed - timedelta(hours=1),
            "upstream_revision": "v1",
            "ingested_content_revision_sha256": (
                product_label_content_revision_sha256(raw)
            ),
            "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
            "lineage_status": "complete",
        },
    })


def test_direct_allergen_conflict_produces_avoid_decision():
    response = generate_chat_response(
        profile=_profile(allergies=["새우"]),
        product=_verified_product(allergy=["새우", "밀"]),
        message="새우깡 먹어도 돼?",
    )

    assert response.intent == "can_i_eat"
    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
    assert "새우" in response.answer


def test_cross_contact_is_caution_not_direct_conflict():
    response = generate_chat_response(
        profile=_profile(allergies=["땅콩"]),
        product=_verified_product(
            name="현미 과자",
            ingredients="현미, 소금",
            warning="땅콩을 사용한 제품과 같은 제조시설에서 제조",
        ),
        message="이거 먹어도 돼?",
    )

    assert response.overall_decision == "caution"
    assert response.allergy_status == "cross_contact"
    assert response.risk_level == "medium"


def test_no_profile_is_not_presented_as_safe():
    response = generate_chat_response(
        product=_verified_product(),
        profile=None,
        message="이거 먹어도 돼?",
    )

    assert response.overall_decision == "check_required"
    assert response.risk_level == "unknown"
    assert "안전" not in response.answer


def test_any_direct_conflict_controls_multi_profile_overall_decision():
    response = generate_chat_response(
        profile=None,
        active_profiles=[
            _profile("나"),
            _profile("아이", allergies=["새우"]),
        ],
        product=_verified_product(allergy=["새우", "밀"]),
        message="우리 둘 다 먹어도 돼?",
    )

    assert response.overall_decision == "avoid"
    assert {item.profile_name for item in response.per_profile_results} == {"나", "아이"}
    child = next(item for item in response.per_profile_results if item.profile_name == "아이")
    assert child.allergy_status == "conflict"
    assert "새우" in " ".join(child.reasons)


def test_ingredient_question_uses_verified_label_text():
    response = generate_chat_response(
        profile=_profile(),
        product=_verified_product(allergy=["새우", "밀"]),
        message="원재료와 알레르기 표시 알려줘",
    )

    assert response.intent == "ingredient_check"
    assert "새우" in response.answer
    assert response.current_product is not None


def test_nutrition_question_displays_value_without_private_high_low_cutoff():
    response = generate_chat_response(
        profile=_profile(),
        product=_verified_product(
            name="현미 과자",
            ingredients="현미, 소금",
            nutrition={"sodium": 180},
            nutrition_basis={"basis_type": "per_serving", "serving_size": "30g"},
        ),
        message="나트륨 얼마야?",
    )

    assert response.intent == "nutrition_explain"
    assert "180mg" in response.answer
    assert "높" not in response.answer and "낮" not in response.answer


def test_gluten_free_is_not_inferred_from_missing_wheat_word():
    response = generate_chat_response(
        profile=_profile(diets=["글루텐프리"]),
        product=_verified_product(name="현미 과자", ingredients="현미, 소금"),
        message="이거 먹어도 돼?",
    )

    assert response.overall_decision != "suitable"
    assert "글루텐프리" in response.answer
    assert "표시" in response.answer or "인증" in response.answer


def test_non_food_question_stays_out_of_scope():
    response = generate_chat_response(
        profile=_profile(),
        product=None,
        message="오늘 날씨 알려줘",
    )

    assert response.intent == "out_of_domain"
    assert "식품" in response.answer


def test_missing_product_question_requests_a_product_identifier():
    response = generate_chat_response(
        profile=_profile(allergies=["우유"]),
        product=None,
        message="이 제품 먹어도 돼?",
    )

    assert response.intent == "missing_product"
    assert response.overall_decision == "check_required"
    assert "제품" in response.answer


def test_injected_llm_failure_falls_back_without_breaking_rules():
    set_llm_client(lambda _prompt: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        response = generate_chat_response(
            profile=_profile(),
            product=None,
            message="zyxq 모호한 문장",
        )
    finally:
        set_llm_client(None)

    assert response.answer_source == "rules"
    assert response.answer
