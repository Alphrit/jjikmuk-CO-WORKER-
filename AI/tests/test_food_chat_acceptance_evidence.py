"""Direct evidence for release scenarios not implied by a test-count total."""

import pytest

from app.core.models import ChatRequest, Profile
from app.main import chat
from app.services.chat import generate_chat_response
from app.services.product import get_product_repository, set_product_repository
from test_recommendation_policy_regressions import _CandidateRepository, _product


def test_productless_allergy_recommendation_returns_only_matching_catalog_candidates():
    previous = get_product_repository()
    excluded = _product(9011, "새우 과자", ingredients="쌀, 새우", allergy=["새우"])
    accepted = _product(9012, "현미 과자", ingredients="현미, 소금")
    set_product_repository(_CandidateRepository([excluded, accepted]))
    try:
        response = generate_chat_response(
            Profile(allergies=["새우"]), None, "새우 알레르기 없는 과자 추천해줘",
        )
    finally:
        set_product_repository(previous)
    assert response.intent == "alternative_food_recommend"
    assert [item.product_name for item in response.recommended_products] == ["현미 과자"]
    assert response.recommended_products[0].recommendation_basis.profile_allergy_checked is True


@pytest.mark.parametrize("question,barcode", [
    ("이 제품 먹어도 돼?", "8801043035989"),
    ("이 제품 나트륨 몇 mg야?", "P00156"),
    ("이 제품 원재료 알려줘", "P00156"),
    ("저염 레시피 추천해줘", None),
    ("이 제품 레시피 추천해줘", "P00156"),
])
def test_missing_information_agrees_with_summary_across_scopes(question, barcode):
    response = chat(ChatRequest(message=question, barcode=barcode, profile=Profile(allergies=["새우"])))
    assert response.assessment_summary is not None
    assert response.missing_information == response.assessment_summary.missing_information


def test_recipe_family_results_are_separate_for_a_selected_product():
    response = chat(ChatRequest(
        message="이 제품 레시피 추천해줘", barcode="P00156",
        profiles=[
            Profile(profile_id=1, profile_name="나", allergies=["새우"]),
            Profile(profile_id=2, profile_name="아이", allergies=["땅콩"]),
        ],
    ))
    assert response.recipe is not None
    assert response.recipe.applied_profiles == ["나", "아이"]
    assert [item.profile_name for item in response.per_profile_results] == ["나", "아이"]
    assert all(item.allergy_status == "no_known_match" for item in response.per_profile_results)
