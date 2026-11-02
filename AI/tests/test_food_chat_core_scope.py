"""Core product/allergy chatbot behavior agreed for the first release.

These tests deliberately describe the user-visible service contract instead of
legacy medical/operations behavior.  They are kept small so future feature work
cannot silently turn the chatbot back into a generic health assistant.
"""

import pytest

from app.core.models import ChatHistoryItem, ChatRequest, Product, Profile
from app.intent.classifier import classify_intent
from app.main import chat
from app.infrastructure.llm import set_llm_client
from app.services.product_search import extract_product_search_query
from app.services.response_helpers import canonical_special_diet_name
from app.infrastructure.conversations import InMemoryConversationRepository
from app.services.recipe import build_recipe_response


def test_allergy_clause_is_not_mistaken_for_the_product_name():
    assert (
        extract_product_search_query("새우 알레르기가 있는데 새우깡 먹어도 돼?")
        == "새우깡"
    )


def test_product_candidate_selection_resumes_the_original_question():
    first = chat(ChatRequest(
        message="새우 알레르기가 있는데 새우깡 먹어도 돼?",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert len(first.product_candidates) >= 2
    assert first.conversation_state.pending_user_message == (
        "새우 알레르기가 있는데 새우깡 먹어도 돼?"
    )
    assert first.conversation_state.pending_intent == "can_i_eat"

    selected = chat(ChatRequest(
        message="첫 번째",
        profile=Profile(profile_name="나", allergies=["새우"]),
        conversation_state=first.conversation_state.model_dump(
            mode="json", exclude_none=True
        ),
    ))

    assert selected.intent == "can_i_eat"
    assert selected.current_product is not None
    assert selected.allergy_status == "conflict"
    assert selected.overall_decision == "avoid"
    assert "새우" in selected.answer


def test_message_allergy_is_applied_for_this_turn_without_persisting_it():
    response = chat(ChatRequest(
        message="새우 알레르기가 있는데 이 제품 먹어도 돼?",
        barcode="8801043035989",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent == "can_i_eat"
    assert response.allergy_status == "conflict"
    assert response.overall_decision == "avoid"
    assert "새우" in response.answer
    assert "설정" in response.answer
    assert response.ui_guidance is not None
    assert response.ui_guidance.code == "UPDATE_PROFILE_ALLERGIES"
    assert response.ui_guidance.profile_name == "나"


def test_verified_product_exposes_top_level_nutrition_facts():
    response = chat(ChatRequest(
        message="이 제품 나트륨이 얼마야?",
        barcode="8801234567890",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.current_product is not None
    assert response.nutrition_facts.sodium == 420
    assert response.current_product.nutrition == response.nutrition_facts
    assert response.assessment_summary is not None
    assert set(response.assessment_summary.missing_information).issubset(
        set(response.missing_information)
    )


def test_message_allergy_does_not_get_assigned_to_every_active_profile():
    response = chat(ChatRequest(
        message="새우 알레르기가 있는데 이 제품 먹어도 돼?",
        barcode="8801043035989",
        profiles=[
            Profile(profile_id=1, profile_name="나", allergies=[]),
            Profile(profile_id=2, profile_name="아이", allergies=[]),
        ],
    ))

    assert response.assessment_status == "incomplete"
    assert response.overall_decision == "check_required"
    assert "누구" in response.answer or "프로필" in response.answer


def test_recipe_requests_have_a_first_class_intent():
    assert (
        classify_intent(
            "새우깡 제품을 이용한 레시피 추천해줘",
            product=None,
            profile=Profile(profile_name="나", allergies=[]),
        )
        == "recipe_recommend"
    )


def test_untraced_catalog_does_not_emit_allergy_recommendation_cards():
    response = chat(ChatRequest(
        message="새우 알레르기 없는 과자 추천해줘",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent == "alternative_food_recommend"
    assert response.recommended_products == []
    assert "찾지 못했어요" in response.answer
    assert "기준을 같이 정해야" not in response.answer


def test_conflicting_product_recipe_waits_when_only_untraced_alternatives_exist():
    response = chat(ChatRequest(
        message="이 제품을 이용한 레시피 추천해줘",
        barcode="8801043035989",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert response.intent == "recipe_recommend"
    assert response.overall_decision == "avoid"
    assert response.recipe is None
    assert response.recommended_products == []
    assert "대체 제품을 찾지 못해 레시피도 생성하지 않았어요" in response.answer


def test_non_conflicting_product_recipe_uses_the_requested_product():
    response = chat(ChatRequest(
        message="이 제품을 이용한 레시피 추천해줘",
        barcode="8801234567890",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent == "recipe_recommend"
    assert response.recipe is not None
    assert response.recipe.source_product_blocked is False
    assert response.recipe.product_name == "저염 두부스낵"


def test_multi_profile_results_keep_each_persons_allergy_outcome_separate():
    response = chat(ChatRequest(
        message="이 제품 먹어도 돼?",
        barcode="8801043035989",
        profiles=[
            Profile(profile_id=1, profile_name="나", allergies=["새우"]),
            Profile(profile_id=2, profile_name="아이", allergies=["땅콩"]),
        ],
    ))

    by_name = {item["profile_name"]: item for item in response.per_profile_results}
    assert response.overall_decision == "avoid"
    assert by_name["나"]["allergy_status"] == "conflict"
    assert by_name["나"]["overall_decision"] == "avoid"
    assert by_name["아이"]["allergy_status"] == "no_known_match"
    assert by_name["아이"]["overall_decision"] == "no_known_conflict"


def test_multi_profile_product_details_keep_family_results_visible():
    response = chat(ChatRequest(
        message="이 제품 원재료와 알레르기 성분 알려줘",
        barcode="8801043035989",
        profiles=[
            Profile(profile_id=1, profile_name="나", allergies=["새우"]),
            Profile(profile_id=2, profile_name="아이", allergies=["땅콩"]),
        ],
    ))

    assert response.intent == "ingredient_check"
    by_name = {item.profile_name: item for item in response.per_profile_results}
    assert by_name["나"].allergy_status == "conflict"
    assert by_name["아이"].allergy_status == "no_known_match"
    assert response.overall_decision == "avoid"


def test_productless_recipe_exposes_each_active_profiles_result():
    response = chat(ChatRequest(
        message="저염 레시피 추천해줘",
        profiles=[
            Profile(profile_id=1, profile_name="나", allergies=["대두"]),
            Profile(profile_id=2, profile_name="아이", allergies=["땅콩"]),
        ],
    ))

    assert response.recipe is not None
    assert response.recipe.applied_profiles == ["나", "아이"]
    assert len(response.per_profile_results) == 2
    assert all(
        item.allergy_status == "no_known_match"
        for item in response.per_profile_results
    )


def test_dislike_does_not_turn_into_an_allergy_warning():
    response = chat(ChatRequest(
        message="이 제품 먹어도 돼?",
        barcode="8800000000000",
        profile=Profile(
            profile_name="나",
            allergies=[],
            disliked_ingredients=["아몬드"],
        ),
    ))

    assert response.preference_match == "disliked_found"
    assert response.allergy_status == "no_known_match"
    # This seed has no bound label revision. A dislike is still not a conflict,
    # but it cannot turn the incomplete assessment into a completed result.
    assert response.overall_decision == "check_required"
    assert response.assessment_summary.decision == "insufficient_information"


def test_gluten_free_is_not_inferred_only_from_no_wheat_match():
    response = chat(ChatRequest(
        message="이 제품 글루텐프리야?",
        barcode="8806666666666",
        profile=Profile(
            profile_name="나",
            allergies=[],
            special_diets=["글루텐프리"],
        ),
    ))

    assert response.diet_match == "unknown"


def test_all_first_release_diets_have_canonical_names():
    expected = {
        "채식": "채식",
        "저당": "저당식",
        "저염": "저염식",
        "글루텐 프리": "글루텐프리",
        "저칼로리": "저칼로리",
        "저지방": "저지방",
        "고단백": "고단백",
    }
    assert {
        raw: canonical_special_diet_name(raw)
        for raw in expected
    } == expected


def test_productless_low_sodium_request_returns_verified_catalog_candidates():
    response = chat(ChatRequest(
        message="저염식으로 먹고 싶으면 뭘 먹어야 해?",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent == "general_diet_guide"
    assert [item.product_name for item in response.recommended_products] == [
        "저염 두부스낵"
    ]
    assert "제품의 명시된 특성" in response.answer


@pytest.mark.parametrize("message", [
    "저염식으로 먹고 싶으면 뭘 먹어야 해?",
    "저염 음식 추천해줘",
    "나트륨 적은 제품 알려줘",
    "저염식 간식 뭐가 좋아?",
])
def test_productless_diet_recommendation_phrasings_return_catalog_products(message):
    response = chat(ChatRequest(
        message=message,
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent in {"general_diet_guide", "alternative_food_recommend"}
    assert [item.product_name for item in response.recommended_products] == [
        "저염 두부스낵"
    ]


def test_productless_diet_recipe_does_not_select_a_similarly_named_product():
    response = chat(ChatRequest(
        message="고단백 레시피 추천해줘",
        profile=Profile(profile_name="나", allergies=[]),
    ))

    assert response.intent == "recipe_recommend"
    assert response.product_candidates == []
    assert response.recipe is not None
    assert response.recipe.product_barcode is None
    assert response.recipe.requested_diets == ["고단백"]
    assert response.recipe.applied_diets == []
    assert response.recipe.diet_limitations
    assert "고단백 목표" in response.recipe.title
    assert "실제 영양수치나 인증 충족을 단정하지 않아요" in response.answer


def _confirmed_comparison(request):
    # Family names must be explicitly confirmed in comparisons too.
    response = chat(request)
    for _ in range(3):
        if not response.product_candidates:
            break
        response = chat(ChatRequest(
            message="첫 번째", profile=request.profile,
            conversation_state=response.conversation_state.model_dump(exclude_none=True),
        ))
    assert not response.product_candidates
    return response


def test_product_comparison_reports_allergy_conflict_before_nutrition_axis():
    response = _confirmed_comparison(ChatRequest(
        message="새우깡과 저염 두부스낵을 비교해줘",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert response.intent == "food_compare"
    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
    assert "알레르기 기준으로 먼저" in response.answer
    assert "새우" in response.answer
    assert "피해야" in response.answer
    assert "비교할 영양 기준을 아직 정하지 않았어요" in response.answer
    assert response.per_profile_results[0].allergy_status == "conflict"


def test_product_comparison_keeps_allergy_conflict_when_nutrition_axis_exists():
    response = _confirmed_comparison(ChatRequest(
        message="새우깡과 저염 두부스낵 나트륨 비교해줘",
        profile=Profile(profile_name="나", allergies=["새우"]),
    ))

    assert response.overall_decision == "avoid"
    assert response.allergy_status == "conflict"
    assert "나트륨" in response.answer
    assert "새우" in response.answer


def test_product_comparison_keeps_diet_and_preference_separate_from_allergy():
    response = _confirmed_comparison(ChatRequest(
        message="새우깡과 저염 두부스낵을 저염식 기준으로 비교해줘",
        profile=Profile(
            profile_name="나", allergies=[],
            special_diets=["저염식"], disliked_ingredients=["새우"],
        ),
    ))

    assert response.intent == "food_compare"
    assert response.allergy_status == "no_known_match"
    assert response.preference_match == "disliked_found"
    assert "비선호 성분" in response.answer
    assert "식이 조건" in response.answer
    assert "제품명·표시에서 저염식 관련 근거" in response.answer
    assert "나트륨" in response.answer
    assert "비교할 영양 기준을 아직 정하지 않았어요" not in response.answer
    assert response.per_profile_results[0].preference_match == "disliked_found"


def test_saved_conversations_do_not_expire_by_default():
    repository = InMemoryConversationRepository()
    conversation = repository.create(user_id=1)

    assert conversation.expires_at is None


def test_named_product_recipe_resumes_after_candidate_selection():
    first = chat(ChatRequest(
        message="새우깡 제품을 이용한 레시피 추천해줘",
        profile=Profile(profile_name="나", allergies=[]),
    ))
    assert first.product_candidates
    assert first.conversation_state.pending_intent == "recipe_recommend"

    selected = chat(ChatRequest(
        message="첫 번째",
        profile=Profile(profile_name="나", allergies=[]),
        conversation_state=first.conversation_state.model_dump(
            mode="json", exclude_none=True
        ),
    ))
    assert selected.intent == "recipe_recommend"
    assert selected.recipe is not None


def test_another_recipe_followup_does_not_repeat_the_previous_title():
    profile = Profile(profile_name="나", allergies=[])
    first = chat(ChatRequest(
        message="이 제품 레시피 추천해줘",
        barcode="8801234567890",
        profile=profile,
    ))
    assert first.recipe is not None

    second = chat(ChatRequest(
        message="다른 레시피도 더 있어?",
        profile=profile,
        conversation_state=first.conversation_state.model_dump(
            mode="json", exclude_none=True
        ),
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 레시피 추천해줘"),
            ChatHistoryItem(role="assistant", content=first.answer),
        ],
    ))

    assert second.intent == "recipe_recommend"
    assert second.recipe is not None
    assert second.recipe.title != first.recipe.title

    third = chat(ChatRequest(
        message="다른 레시피 하나 더 추천해줘",
        profile=profile,
        conversation_state=second.conversation_state.model_dump(
            mode="json", exclude_none=True
        ),
        chat_history=[
            ChatHistoryItem(role="user", content="이 제품 레시피 추천해줘"),
            ChatHistoryItem(role="assistant", content=first.answer),
            ChatHistoryItem(role="user", content="다른 레시피도 더 있어?"),
            ChatHistoryItem(role="assistant", content=second.answer),
        ],
    ))

    assert third.recipe is not None
    assert third.recipe.title not in {first.recipe.title, second.recipe.title}


def test_recipe_followups_keep_diet_and_do_not_repeat_after_four_variants():
    profile = Profile(profile_name="나", allergies=[])
    history = []
    state = None
    responses = []
    for index in range(5):
        message = "저염 레시피 추천해줘" if index == 0 else "다른 레시피 더 있어?"
        response = chat(ChatRequest(
            message=message,
            profile=profile,
            conversation_state=state,
            chat_history=history,
        ))
        responses.append(response)
        state = response.conversation_state.model_dump(mode="json", exclude_none=True)
        history.extend([
            ChatHistoryItem(role="user", content=message),
            ChatHistoryItem(role="assistant", content=response.answer),
        ])

    recipes = [response.recipe for response in responses[:4]]
    assert all(recipe is not None for recipe in recipes)
    assert len({recipe.title for recipe in recipes}) == 4
    assert all(recipe.requested_diets == ["저염식"] for recipe in recipes)
    assert all(recipe.applied_diets == [] for recipe in recipes)
    assert len(responses[3].conversation_state.recipe_fingerprints) == 4
    assert responses[4].recipe is None


def test_gluten_free_recipe_conflict_is_not_described_as_an_allergy_conflict():
    product = Product(
        product_name="밀과자", prdlst_dcnm="과자", rawmtrl_nm="밀가루",
        allergy=["밀"], product_found=True, facts_verified=True,
    )
    profile = Profile(profile_name="나", allergies=[], special_diets=["글루텐프리"])

    response = build_recipe_response(
        profile, product, "이 제품 글루텐프리 레시피 추천해줘",
        profiles=[profile],
    )

    assert response.recipe is None
    assert response.allergy_status == "no_known_match"
    assert response.diet_match == "not_match"
    assert response.overall_decision == "caution"
    assert "알레르기 직접 충돌" not in response.answer


def test_milk_recipe_uses_a_liquid_product_method():
    product = Product(
        product_name="흰우유", prdlst_dcnm="우유", rawmtrl_nm="원유",
        allergy=["우유"], product_found=True, facts_verified=True,
    )
    profile = Profile(profile_name="나", allergies=[])

    response = build_recipe_response(profile, product, "이 제품 레시피 추천해줘")

    assert response.recipe is not None
    assert response.recipe.ingredients[0].amount.endswith("ml")
    assert "부숴" not in " ".join(response.recipe.steps)


def test_soy_bar_is_not_misclassified_as_a_liquid_recipe():
    product = Product(
        product_name="고단백 두유바", prdlst_dcnm="단백질바", rawmtrl_nm="대두",
        allergy=["대두"], product_found=True, facts_verified=True,
    )
    profile = Profile(profile_name="나", allergies=[])

    response = build_recipe_response(profile, product, "이 제품 레시피 추천해줘")

    assert response.recipe is not None
    assert response.recipe.ingredients[0].amount.endswith("g")
    assert "스무디" not in response.recipe.title


def test_vegetarian_milk_recipe_waits_for_diet_type_clarification():
    product = Product(
        product_name="흰우유", prdlst_dcnm="우유", rawmtrl_nm="원유",
        allergy=["우유"], product_found=True, facts_verified=True,
    )
    profile = Profile(profile_name="나", allergies=[], special_diets=["채식"])

    response = build_recipe_response(profile, product, "이 제품 채식 레시피 추천해줘")

    assert response.recipe is None
    assert response.allergy_status == "no_known_match"
    assert response.diet_match == "caution"
    assert "채식 유형" in response.answer


def test_unsupported_allergy_halts_recipe_without_runtime_failure():
    profile = Profile(profile_name="나", allergies=["미등록 항목"])

    response = build_recipe_response(profile, None, "일반 레시피 추천해줘")

    assert response.recipe is None
    assert response.assessment_status == "unsupported"
    assert "지원되지 않는 알레르기" in response.reasons[0]


def test_productless_recipe_skips_a_disliked_extra_ingredient():
    response = chat(ChatRequest(
        message="저염 레시피 추천해줘",
        profile=Profile(
            profile_name="나", allergies=[], disliked_ingredients=["오이"],
        ),
    ))

    assert response.recipe is not None
    assert all(item.name != "오이" for item in response.recipe.ingredients)
    assert response.preference_match == "matched"


def test_recipe_discards_llm_draft_with_an_allergen():
    calls = []
    set_llm_client(lambda prompt: calls.append(prompt) or """
    {
      "title": "땅콩 토핑 샐러드",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [
        {"name": "저염 두부스낵", "amount": "30g"},
        {"name": "땅콩", "amount": "한 줌"}
      ],
      "steps": ["모든 재료를 섞어요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(profile_name="나", allergies=["땅콩"]),
        ))
    finally:
        set_llm_client(None)

    assert calls
    assert response.recipe is not None
    assert all(item.name != "땅콩" for item in response.recipe.ingredients)
    assert response.answer_source == "rules"
    assert "recipe_llm_allergen_conflict" in response.validation_failure_types


def test_recipe_accepts_a_validated_llm_draft_but_keeps_rule_owned_safety():
    set_llm_client(lambda _prompt: """
    {
      "title": "두부스낵 채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [
        {"name": "저염 두부스낵", "amount": "30g"},
        {"name": "오이", "amount": "1/2개"}
      ],
      "steps": ["오이를 썰고 제품을 올려요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(profile_name="나", allergies=[]),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.recipe.title == "두부스낵 채소 컵"
    assert response.recipe.allergen_check
    assert response.recipe.packaged_item_cautions
    assert response.answer_source == "hybrid"


def test_recipe_discards_llm_draft_that_does_not_use_selected_product():
    set_llm_client(lambda _prompt: """
    {
      "title": "채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [{"name": "오이", "amount": "1/2개"}],
      "steps": ["오이를 써어요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(profile_name="나", allergies=[]),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_product_missing_from_ingredients" in response.validation_failure_types


def test_recipe_checks_diet_conflicts_in_llm_substitutions_and_steps():
    set_llm_client(lambda _prompt: """
    {
      "title": "두부스낵 채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [
        {"name": "저염 두부스낵", "amount": "30g"},
        {"name": "오이", "amount": "1/2개"}
      ],
      "steps": ["오이를 써고 제품을 올려요."],
      "substitutions": ["오이 대신 돼지고기를 써도 돼요."]
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 채식 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(
                profile_name="나",
                allergies=[],
                special_diets=["채식"],
            ),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_diet_conflict" in response.validation_failure_types


def test_productless_recipe_accepts_only_a_validated_llm_draft():
    set_llm_client(lambda _prompt: """
    {
      "title": "병아리콩 오이 볼",
      "servings": "1인분",
      "product_name": "기본 식재료",
      "ingredients": [
        {"name": "삶은 병아리콩", "amount": "1/2컵"},
        {"name": "오이", "amount": "1/3개"}
      ],
      "steps": ["병아리콩과 오이를 섞어요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="저염 레시피 추천해줘",
            profile=Profile(profile_name="나", allergies=[]),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.recipe.title == "병아리콩 오이 볼"
    assert response.recipe.requested_diets == ["저염식"]
    assert response.recipe.applied_diets == []
    assert response.answer_source == "hybrid"
    assert "영양수치" in response.answer


def test_productless_recipe_rejects_unlisted_packaged_food_in_steps():
    set_llm_client(lambda _prompt: """
    {
      "title": "병아리콩 오이 볼",
      "servings": "1인분",
      "product_name": "기본 식재료",
      "ingredients": [
        {"name": "삶은 병아리콩", "amount": "1/2컵"},
        {"name": "오이", "amount": "1/3개"}
      ],
      "steps": ["오이를 썰고 시판 소스를 넣어요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="저염 레시피 추천해줘",
            profile=Profile(profile_name="나", allergies=[]),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_unlisted_packaged_ingredient" in response.validation_failure_types


def test_recipe_rejects_llm_draft_with_zero_selected_product_amount():
    set_llm_client(lambda _prompt: """
    {
      "title": "두부스낵 채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [
        {"name": "저염 두부스낵", "amount": "0g"},
        {"name": "오이", "amount": "1/2개"}
      ],
      "steps": ["오이를 썰고 제품을 올려요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(profile_name="나", allergies=[]),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_invalid_draft" in response.validation_failure_types


def test_recipe_rejects_llm_step_that_breaks_a_liquid_product():
    product = Product(
        product_name="흰우유", prdlst_dcnm="우유", rawmtrl_nm="원유",
        allergy=["우유"], product_found=True, facts_verified=True,
    )
    profile = Profile(profile_name="나", allergies=[])
    set_llm_client(lambda _prompt: """
    {
      "title": "흰우유 샐러드",
      "servings": "1인분",
      "product_name": "흰우유",
      "ingredients": [{"name": "흰우유", "amount": "150ml"}],
      "steps": ["흰우유를 부숴 샐러드에 뿌려요."],
      "substitutions": []
    }
    """)
    try:
        response = build_recipe_response(profile, product, "이 제품 레시피 추천해줘")
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_product_method_conflict" in response.validation_failure_types


def test_recipe_rejects_llm_step_that_adds_ambiguous_vegetarian_egg():
    set_llm_client(lambda _prompt: """
    {
      "title": "두부스낵 채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [{"name": "저염 두부스낵", "amount": "30g"}],
      "steps": ["계란을 섞어 제품에 곁들여요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 채식 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(
                profile_name="나", allergies=[], special_diets=["채식"],
            ),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert response.answer_source == "rules"
    assert "recipe_llm_diet_conflict" in response.validation_failure_types


def test_recipe_rejects_llm_disliked_ingredient_mentioned_only_in_steps():
    set_llm_client(lambda _prompt: """
    {
      "title": "두부스낵 채소 컵",
      "servings": "1인분",
      "product_name": "저염 두부스낵",
      "ingredients": [{"name": "저염 두부스낵", "amount": "30g"}],
      "steps": ["오이를 썰어 제품에 곁들여요."],
      "substitutions": []
    }
    """)
    try:
        response = chat(ChatRequest(
            message="이 제품 레시피 추천해줘",
            barcode="8801234567890",
            profile=Profile(
                profile_name="나", allergies=[], disliked_ingredients=["오이"],
            ),
        ))
    finally:
        set_llm_client(None)

    assert response.recipe is not None
    assert all(item.name != "오이" for item in response.recipe.ingredients)
    assert response.answer_source == "rules"
    assert "recipe_llm_disliked_ingredient" in response.validation_failure_types
