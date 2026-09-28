import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import Product, Profile
from app.core.product_provenance import (
    PRODUCT_LABEL_FACT_GROUPS,
    product_label_content_revision_sha256,
)
from app.data.dictionaries import (
    ALLERGEN_CANONICAL_ALIASES,
    ALLERGEN_CHILDREN,
    ALLERGY_MAP,
    SPECIAL_DIET_RESTRICTIONS,
)
from app.infrastructure.repository import ProductRepository
from app.services.product import recommend_alternative_products
from app.services.response_helpers import (
    aliases_for_allergen,
    canonical_allergen_name,
    find_cross_contamination_aliases,
    find_matching_aliases,
)


BROAD_ALLERGEN_DESCENDANTS = (
    ("견과류", "피칸", "피칸"),
    ("견과류", "마카다미아", "마카다미아"),
    ("견과류", "브라질너트", "브라질너트"),
    ("견과류", "캐슈", "캐슈넛"),
    ("갑각류", "랍스터", "랍스터"),
    ("갑각류", "가재", "가재"),
    ("조개류", "가리비", "가리비"),
    ("조개류", "바지락", "바지락"),
    ("조개류", "꼬막", "꼬막"),
)


class _CandidateRepository(ProductRepository):
    def __init__(self, candidates: list[Product]):
        self._candidates = candidates

    def recommend_alternatives(
        self,
        product: Product,
        limit: int = 3,
    ) -> list[Product]:
        return self._candidates[:limit]


def _traced_product(
    *,
    product_name: str,
    barcode: str,
    rawmtrl_nm: str,
    ingredients: list[str],
) -> Product:
    payload = {
        "product_name": product_name,
        "barcode": barcode,
        "report_number": f"report-{barcode}",
        "rawmtrl_nm": rawmtrl_nm,
        "ingredients": ingredients,
        "allergy": [],
        "allergy_info": [],
        "allergy_warning": None,
        "prdlst_dcnm": "과자",
    }
    product = Product.model_validate(payload)
    observed_at = datetime.now(timezone.utc) - timedelta(days=1)
    payload["label_provenance"] = {
        "source_type": "manufacturer_label",
        "source_id": "synthetic-label-test",
        "source_record_id": (
            "record-" + hashlib.sha256(barcode.encode("utf-8")).hexdigest()[:20]
        ),
        "label_observed_at": observed_at.isoformat(),
        "upstream_updated_at": (observed_at - timedelta(days=1)).isoformat(),
        "upstream_revision": "allergy-ontology-v1",
        "ingested_content_revision_sha256": (
            product_label_content_revision_sha256(product)
        ),
        "covered_fact_groups": list(PRODUCT_LABEL_FACT_GROUPS),
        "lineage_status": "complete",
    }
    return Product.model_validate(payload).model_copy(
        update={"match_confidence": 0.9}
    )


@pytest.mark.parametrize(
    ("broad_allergen", "ingredient", "expected_canonical"),
    BROAD_ALLERGEN_DESCENDANTS,
)
def test_broad_allergen_matches_descendant_in_direct_and_cross_contact_evidence(
    broad_allergen: str,
    ingredient: str,
    expected_canonical: str,
):
    direct = Product(rawmtrl_nm=f"귀리, {ingredient}, 코코아")
    cross_contact = Product(
        allergy_warning=f"같은 제조시설에서 {ingredient}를 사용한 제품과 함께 제조"
    )

    assert find_matching_aliases(direct, broad_allergen)
    assert find_cross_contamination_aliases(cross_contact, broad_allergen)
    assert canonical_allergen_name(ingredient) == expected_canonical


@pytest.mark.parametrize(
    ("specific_allergen", "sibling"),
    (("피칸", "마카다미아"), ("호두", "캐슈"), ("랍스터", "새우")),
)
def test_specific_allergen_does_not_expand_to_sibling_taxa(
    specific_allergen: str,
    sibling: str,
):
    assert canonical_allergen_name(specific_allergen) != canonical_allergen_name(
        sibling
    )
    assert sibling not in aliases_for_allergen(specific_allergen)


def test_generic_parent_label_remains_relevant_to_specific_child_profile():
    assert find_matching_aliases(
        Product(rawmtrl_nm="견과류 함유"),
        "피칸",
    )
    assert find_matching_aliases(
        Product(rawmtrl_nm="갑각류 함유"),
        "랍스터",
    )


def test_allergy_and_diet_safety_registries_are_deeply_immutable():
    with pytest.raises(TypeError):
        ALLERGY_MAP["우유"] = ()  # type: ignore[index]
    with pytest.raises(AttributeError):
        ALLERGY_MAP["우유"].clear()  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        ALLERGEN_CHILDREN["견과류"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        ALLERGEN_CANONICAL_ALIASES["피칸"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        SPECIAL_DIET_RESTRICTIONS["채식"]["blocked_ingredients"] = ()  # type: ignore[index]
    with pytest.raises(AttributeError):
        SPECIAL_DIET_RESTRICTIONS["채식"]["blocked_ingredients"].append(  # type: ignore[attr-defined]
            "임의 성분"
        )


@pytest.mark.parametrize(
    ("broad_allergen", "ingredient", "_expected_canonical"),
    BROAD_ALLERGEN_DESCENDANTS,
)
@pytest.mark.parametrize("evidence_channel", ("ingredients", "product_name"))
def test_complete_provenance_recommendation_never_claims_absence_for_descendant(
    broad_allergen: str,
    ingredient: str,
    _expected_canonical: str,
    evidence_channel: str,
):
    suffix = f"{broad_allergen}-{ingredient}-{evidence_channel}"
    reference = _traced_product(
        product_name="기준 귀리 스낵",
        barcode=f"reference-{suffix}",
        rawmtrl_nm="귀리, 코코아",
        ingredients=["귀리", "코코아"],
    )
    unsafe = _traced_product(
        product_name=(
            f"{ingredient} 크런치"
            if evidence_channel == "product_name"
            else "위험 후보"
        ),
        barcode=f"unsafe-{suffix}",
        rawmtrl_nm=(
            "귀리, 코코아"
            if evidence_channel == "product_name"
            else f"귀리, {ingredient}"
        ),
        ingredients=(
            ["귀리", "코코아"]
            if evidence_channel == "product_name"
            else ["귀리", ingredient]
        ),
    )
    safe = _traced_product(
        product_name="검증 쌀 크래커",
        barcode=f"safe-{suffix}",
        rawmtrl_nm="쌀, 코코아",
        ingredients=["쌀", "코코아"],
    )

    results = recommend_alternative_products(
        reference,
        profile=Profile(profile_name="나", allergies=[broad_allergen]),
        repository=_CandidateRepository([unsafe, safe]),
        limit=1,
    )

    assert [item["product_name"] for item in results] == [safe.product_name]
    assert unsafe.product_name not in {
        item["product_name"] for item in results
    }
    basis = results[0]["recommendation_basis"]
    assert basis["profile_allergy_checked"] is True
    assert basis["allergy_absence_supported"] is True
    assert basis["candidate_label_trust"]["lineage_status"] == "complete"
    assert basis["candidate_label_trust"]["freshness_status"] == "within_policy"
