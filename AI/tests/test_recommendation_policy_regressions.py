from datetime import datetime, timedelta, timezone

from app.core.models import Product, Profile
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.infrastructure.repository import ProductRepository
from app.infrastructure.llm import set_llm_client
from app.services.chat import generate_chat_response
from app.services.product import (
    get_product_repository,
    recommend_catalog_products,
    set_product_repository,
)
from app.services.recipe import build_recipe_response


def _product(
    product_id: int,
    name: str,
    *,
    ingredients: str = "귀리, 코코아",
    allergy: list[str] | None = None,
    warning: str | None = None,
) -> Product:
    raw = Product(
        product_id=product_id,
        product_name=name,
        barcode=f"880000000{product_id}",
        report_number=f"2026{product_id:08d}",
        rawmtrl_nm=ingredients,
        ingredients=[item.strip() for item in ingredients.split(",")],
        allergy=allergy or [],
        allergy_warning=warning,
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
            "source_record_id": f"fixture-{product_id}",
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


class _CandidateRepository(ProductRepository):
    def __init__(self, candidates: list[Product]):
        self.candidates = candidates

    def recommend_alternatives(self, product, limit=3):
        return self.candidates[:limit]

    def search_candidates(self, text, limit=3):
        return self.candidates[:limit]


def _profile(name: str, *, allergies=None, diets=None, dislikes=None) -> Profile:
    return Profile(
        profile_name=name,
        allergies=allergies or [],
        special_diets=diets or [],
        disliked_ingredients=dislikes or [],
    )


def test_any_active_profile_allergy_excludes_candidate():
    source = _product(8101, "기준 과자")
    milk = _product(
        8102,
        "우유 과자",
        ingredients="귀리, 우유",
        allergy=["우유"],
        warning="우유 함유",
    )
    safe = _product(8103, "코코아 과자")

    cards = recommend_catalog_products(
        "과자 추천해줘",
        [_profile("아이", allergies=["우유"]), _profile("나")],
        source_product=source,
        repository=_CandidateRepository([milk, safe]),
    )

    assert [card["product_name"] for card in cards] == ["코코아 과자"]
    assert cards[0]["recommendation_basis"]["profile_allergy_checked"] is True


def test_cross_contact_candidate_is_not_recommended():
    source = _product(8111, "기준 과자")
    cross_contact = _product(
        8112,
        "공용 설비 과자",
        warning="땅콩을 사용한 제품과 같은 제조시설에서 제조",
    )

    cards = recommend_catalog_products(
        "과자 추천해줘",
        [_profile("나", allergies=["땅콩"])],
        source_product=source,
        repository=_CandidateRepository([cross_contact]),
    )

    assert cards == []


def test_missing_allergy_axis_withholds_positive_recommendation():
    source = _product(8121, "기준 과자")
    candidate = _product(8122, "대체 과자")
    incomplete_profile = Profile(profile_name="대상")

    assert recommend_catalog_products(
        "과자 추천해줘",
        [incomplete_profile],
        source_product=source,
        repository=_CandidateRepository([candidate]),
    ) == []


def test_composite_dislike_is_applied_before_selection():
    source = _product(8131, "기준 과자")
    disliked = _product(8132, "땅콩 과자", ingredients="귀리, 땅콩")
    accepted = _product(8133, "현미 과자", ingredients="현미, 소금")

    cards = recommend_catalog_products(
        "과자 추천해줘",
        [_profile("나", dislikes=["고수, 땅콩"])],
        source_product=source,
        repository=_CandidateRepository([disliked, accepted]),
    )

    assert [card["product_name"] for card in cards] == ["현미 과자"]


def test_diet_recommendation_requires_explicit_product_claim():
    source = _product(8141, "기준 과자")
    unclaimed = _product(8142, "담백한 과자")
    claimed = _product(8143, "저염 현미 과자")

    cards = recommend_catalog_products(
        "저염 과자 추천해줘",
        [_profile("나")],
        source_product=source,
        repository=_CandidateRepository([unclaimed, claimed]),
    )

    assert [card["product_name"] for card in cards] == ["저염 현미 과자"]
    assert cards[0]["recommendation_basis"]["applied_rule_ids"] == []


def test_recommendation_limit_is_bounded_to_requested_count():
    source = _product(8151, "기준 과자")
    candidates = [_product(8152 + index, f"대체 과자 {index}") for index in range(6)]

    cards = recommend_catalog_products(
        "과자 추천해줘",
        [_profile("나")],
        source_product=source,
        limit=3,
        repository=_CandidateRepository(candidates),
    )

    assert len(cards) == 3


def test_conflicting_product_chat_automatically_adds_database_alternative():
    source = _product(
        8161,
        "우유 과자",
        ingredients="귀리, 우유",
        allergy=["우유"],
        warning="우유 함유",
    )
    candidate = _product(8162, "현미 과자", ingredients="현미, 소금")
    repository = _CandidateRepository([candidate])
    previous = get_product_repository()
    set_product_repository(repository)
    try:
        response = generate_chat_response(
            profile=_profile("아이", allergies=["우유"]),
            product=source,
            message="이 제품 먹어도 돼?",
        )
    finally:
        set_product_repository(previous)

    assert response.overall_decision == "avoid"
    assert [card.product_name for card in response.recommended_products] == [
        "현미 과자"
    ]
    assert response.applied_rule_ids == []


def test_conflicting_product_recipe_uses_verified_substitute_and_keeps_warning():
    source = _product(
        8171, "우유 과자", ingredients="귀리, 우유",
        allergy=["우유"], warning="우유 함유",
    )
    substitute = _product(8172, "현미 과자", ingredients="현미, 소금")
    previous = get_product_repository()
    set_product_repository(_CandidateRepository([substitute]))
    set_llm_client(lambda _prompt: """
    {
      "title": "현미 과자 채소 컵",
      "servings": "1인분",
      "product_name": "현미 과자",
      "ingredients": [
        {"name": "현미 과자", "amount": "30g"},
        {"name": "오이", "amount": "1/2개"}
      ],
      "steps": ["오이를 썰고 현미 과자를 곁들여요."],
      "substitutions": []
    }
    """)
    try:
        response = build_recipe_response(
            _profile("아이", allergies=["우유"]),
            source,
            "이 제품을 이용한 레시피 추천해줘",
        )
    finally:
        set_llm_client(None)
        set_product_repository(previous)

    assert response.recipe is not None
    assert response.recipe.source_product_blocked is True
    assert response.recipe.product_name == "현미 과자"
    assert any("우유 과자" in item for item in response.recipe.substitutions)
    assert response.answer_source == "hybrid"
    assert "우유 과자" in response.answer
    assert "레시피에 사용하지 않았어요" in response.answer
    assert response.overall_decision == "avoid"
