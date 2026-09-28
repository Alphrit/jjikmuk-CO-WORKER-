from app.core.models import Product, Profile
from app.services.chat import generate_chat_response
from app.services.message_constraint_guard import (
    apply_turn_local_allergies,
    build_message_constraint_guard_response,
)
from app.services.profile_context import merge_profiles


def _profile(name="나", *, allergies=None):
    return Profile(
        profile_name=name,
        allergies=allergies or [],
        special_diets=[],
        disliked_ingredients=[],
    )


def test_explicit_allergy_is_applied_to_single_profile_for_this_turn():
    saved = _profile()

    context = apply_turn_local_allergies(
        "우유 알레르기가 있는데 이거 먹어도 돼?",
        [saved],
    )

    assert context.added_allergens == ("우유",)
    assert context.profiles[0].resolved_allergies() == ["우유"]
    assert saved.resolved_allergies() == []


def test_turn_local_allergy_changes_answer_and_adds_settings_hint():
    response = generate_chat_response(
        profile=_profile(),
        product=Product(
            product_name="버터 쿠키",
            rawmtrl_nm="밀가루, 버터, 우유",
            allergy=["우유", "밀"],
        ),
        message="우유 알레르기가 있는데 이거 먹어도 돼?",
    )

    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
    assert "임시로 반영" in response.answer
    assert "설정" in response.answer


def test_turn_local_allergy_without_profile_creates_ephemeral_target():
    context = apply_turn_local_allergies(
        "새우 알러지가 있는데 새우깡 먹어도 돼?",
        [],
    )

    assert context.profiles[0].profile_name == "이번 질문"
    assert context.profiles[0].resolved_allergies() == ["새우"]


def test_new_allergy_for_multiple_profiles_requires_exact_target():
    response = build_message_constraint_guard_response(
        "우유 알레르기가 있는데 이거 먹어도 돼?",
        profiles=[_profile("나"), _profile("아이")],
    )

    assert response is not None
    assert response.assessment_status == "incomplete"
    assert "어느 프로필" in response.answer
    assert response.recommended_products == []


def test_named_profile_receives_new_allergy_without_merging_everyone():
    context = apply_turn_local_allergies(
        "아이가 우유 알레르기가 있는데 이거 먹어도 돼?",
        [_profile("나"), _profile("아이")],
    )

    assert context.target_ambiguous is False
    assert context.profiles[0].resolved_allergies() == []
    assert context.profiles[1].resolved_allergies() == ["우유"]


def test_unspecified_allergy_without_saved_value_requests_detail():
    response = build_message_constraint_guard_response(
        "알레르기가 있는데 이거 먹어도 돼?",
        profiles=[_profile()],
    )

    assert response is not None
    assert "어떤 알레르기" in response.answer


def test_saved_allergy_criteria_reference_is_not_treated_as_new_claim():
    assert build_message_constraint_guard_response(
        "내 알레르기 기준으로 먹어도 돼?",
        profiles=[_profile(allergies=["대두"])],
    ) is None


def test_merge_preserves_allergies_but_marks_omitted_context_incomplete():
    complete = _profile("A", allergies=["우유"])
    omitted = Profile(profile_name="B")

    merged = merge_profiles([complete, omitted])

    assert merged is not None
    assert merged.resolved_allergies() == ["우유"]
    assert merged.allergy_context_provided is False
