import pytest

from app.core.models import Product, Profile
from app.services.chat import generate_chat_response
from app.services.message_constraint_guard import (
    _message_allergens,
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


@pytest.mark.parametrize("message", [
    "밀 알레르기와 글루텐프리는 같은 뜻이야?",
    "락토프리 우유는 우유 알레르기에도 괜찮다는 뜻이야?",
    "우유 알레르기와 유당불내증 차이 알려줘",
    "우유 알레르기와 밀 알레르기 차이가 뭐야?",
])
def test_general_concept_comparison_does_not_assert_personal_allergy(message):
    parsed = _message_allergens(message)
    assert not parsed.confirmed and not parsed.uncertain and not parsed.unspecified
    response = generate_chat_response(_profile(), None, message)
    assert response.ui_guidance is None
    assert "임시로 반영" not in response.answer


def test_concept_question_does_not_discard_separate_personal_statement():
    parsed = _message_allergens("나는 새우 알레르기가 있어. 밀 알레르기와 글루텐프리는 같은 뜻이야?")
    assert parsed.confirmed == frozenset({"새우"})


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


@pytest.mark.parametrize("question", [
    "영국 알레르기 혼입 표시 의무를 알려줘",
    "같은 제조시설의 알레르기 혼입 확률은 몇 %야?",
    "같은 제조시설의 알레르기 교차접촉 뜻을 설명해줘",
    "바나나 알레르기 혼입 주의문구 무슨 뜻이야?",
    "비건은 알레르기 유발물질 부재를 보장하는 표시야?",
    "같은 공장이라는 알레르기 문구 뜻",
])
def test_labelling_information_does_not_invent_a_personal_allergen(question):
    parsed = _message_allergens(question)
    assert not parsed.confirmed and not parsed.uncertain and not parsed.denied
    assert build_message_constraint_guard_response(question, profiles=[_profile()]) is None


def test_vegan_label_information_keeps_an_explicit_personal_allergy():
    parsed = _message_allergens("바나나 알레르기가 있어. 비건은 알레르기 유발물질 부재를 보장하는 표시야?")
    assert parsed.confirmed == frozenset({"바나나"})
    assert not parsed.uncertain and not parsed.denied


def test_labelling_information_still_preserves_an_explicit_unknown_food_allergy():
    parsed = _message_allergens("바나나 알레르기가 있어. 영국 알레르기 혼입 표시 의무를 알려줘")
    assert parsed.confirmed == frozenset({"바나나"})
    assert not parsed.uncertain


def test_merge_preserves_allergies_but_marks_omitted_context_incomplete():
    complete = _profile("A", allergies=["우유"])
    omitted = Profile(profile_name="B")

    merged = merge_profiles([complete, omitted])

    assert merged is not None
    assert merged.resolved_allergies() == ["우유"]
    assert merged.allergy_context_provided is False
