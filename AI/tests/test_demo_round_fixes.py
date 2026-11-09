"""Fixes from the 2026-11-09 exploratory round of realistic questions (presentation prep)."""

import pytest

from app.core.models import ChatResponse, Product
from app.intent.explanation_topics import requested_topics
from app.intent.rules import rule_based_classify_intent
from app.services.conversation_router import _explicit_named_product_query
from app.services.product_search import extract_product_search_query


@pytest.mark.parametrize("message, expected", [
    ("새우깡 몇 칼로리야", "새우깡"), ("오레오 성분", "오레오"), ("진라면 순한맛 나트륨", "진라면 순한맛"),
    ("새우깡 먹어두 돼??", "새우깡"), ("새우깡머거도돼?", "새우깡"), ("새우깡 칼로리야?", "새우깡"),
])
def test_product_name_is_cut_before_trailing_fields_and_typo_verbs(message, expected):
    assert extract_product_search_query(message) == expected


@pytest.mark.parametrize("message, expected", [
    ("진라면 순한맛 나트륨", "진라면 순한맛"),  # '라면' in a name is not '~라면'
    ("새우깡 먹어두 돼??", "새우깡"),
    ("저염 두부스낵 찾아줘", "저염 두부스낵"),
    ("제로콜라 칼로리 얼마야?", "제로콜라"),
    ("제로 음료는 칼로리가 0이야?", None),  # a claim on a kind of food
    ("저당 과자는 당류가 없어?", None),
])
def test_named_product_query(message, expected):
    assert _explicit_named_product_query(message, None) == expected


def test_zero_drink_energy_question_is_a_general_claim_topic():
    assert "zero_energy" in requested_topics("제로 음료는 칼로리가 0이야?")


@pytest.mark.parametrize("message", ["다이어트 간식 추천", "아무거나 추천해줘", "마실 거 추천해줘"])
def test_vague_food_requests_are_recommendations(message):
    assert rule_based_classify_intent(message, None, None, []) == "alternative_food_recommend"


def test_recipe_requests_stay_recipes():
    assert rule_based_classify_intent("간식 레시피 추천해줘", None, None, []) == "recipe_recommend"


def test_team_help_offers_only_available_features(monkeypatch):
    from app.services.conversation_controls import conversation_control_response
    monkeypatch.setenv("FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG", "true")
    for message in ("도움말", "넌 누구야?"):
        reply = conversation_control_response(message, {})
        assert "레시피" not in reply.answer and "추천" not in reply.answer
        assert not any("레시피" in question for question in reply.recommended_questions)


def _product(name, barcode, confidence=0.82):
    return Product(product_name=name, barcode=barcode, report_number=barcode, product_found=True,
                   match_confidence=confidence)


def test_comparison_resolves_exact_names_and_the_tapped_barcode_without_state(monkeypatch):
    from app.services import comparison_context
    catalog = {
        "신라면": [_product("신라면", "1", 1.0), _product("신라면컵", "2")],
        "진라면": [_product("진라면(순한맛)", "3"), _product("진라면 매운맛", "4")],
    }
    monkeypatch.setattr(comparison_context, "find_product_candidates_from_message",
                        lambda query, limit=3: catalog[query.replace(" 찾아줘", "")])
    products, confirmation, _, _ = comparison_context.resolve_comparison(
        "신라면이랑 진라면 나트륨 비교", _product("진라면(순한맛)", "3"), {})
    assert confirmation is None
    assert [product.product_name for product in products] == ["신라면", "진라면(순한맛)"]
    _, confirmation, _, _ = comparison_context.resolve_comparison("신라면이랑 진라면 나트륨 비교", None, {})
    assert confirmation is not None  # '진라면' is ambiguous until a candidate is tapped


def test_team_filter_keeps_a_no_conflict_comparison_with_a_neutral_verdict():
    from app.core.public_product import public_product_payload
    from app.services.team_catalog import COMPARISON_NO_CONFLICT_NOTE, restrict_team_catalog_response
    response = ChatResponse(
        intent="food_compare", answer="신라면: 현재 DB 표시에서 등록 알레르기의 직접 충돌을 찾지 못했어요.",
        answer_source="rules", risk_level="low", overall_decision="no_known_conflict", allergy_status="no_known_match",
        comparison_products=[public_product_payload(_product("신라면", "1"), view="candidate"),
                             public_product_payload(_product("진라면", "3"), view="candidate")],
    )
    bounded = restrict_team_catalog_response(response)
    assert COMPARISON_NO_CONFLICT_NOTE in bounded.answer
    assert bounded.overall_decision == "check_required" and bounded.risk_level == "unknown"
    assert bounded.comparison_products


def test_comparison_answer_gets_no_single_product_allergy_alert():
    from app.services.chat import attach_profile_allergy_alert
    from app.core.models import Profile
    response = ChatResponse(intent="food_compare", task_type="product_chat", answer="비교", risk_level="unknown")
    product = Product(product_name="쌀새우깡", barcode="5", allergy=["새우"], product_found=True)
    assert attach_profile_allergy_alert(response, product, Profile(name="검증", allergies=["새우"])) is response


def test_ingredient_in_the_product_name_is_never_reported_as_absent():
    from app.services.product_facts import ingredient_label_text
    product = Product(product_name="땅콩빼빼로", barcode="9", allergy=["밀", "우유"], rawmtrl_nm="밀가루, 설탕")
    text = ingredient_label_text(product, "땅콩")
    assert "제품명에 '땅콩'이 들어 있어요" in text and "드시지 마세요" in text
    assert "찾지 못했어요" not in text


@pytest.mark.parametrize("message, expected", [
    ("새우깡 포화지방 얼마야?", "새우깡"), ("새우깡 트랜스지방은?", "새우깡"), ("오레오 식이섬유 알려줘", "오레오"),
])
def test_fat_and_fiber_fields_end_the_product_name(message, expected):
    assert extract_product_search_query(message) == expected


@pytest.mark.parametrize("message, expected", [
    ("새우깡은?", "새우깡"), ("진라면은", "진라면"), ("우유는?", None), ("칼로리는?", None), ("엄마는?", None),
])
def test_short_question_switches_only_to_a_product_name(message, expected):
    assert _explicit_named_product_query(message, None) == expected


def test_product_name_words_do_not_turn_an_eat_question_into_an_ingredient_question(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import main as api
    from app.infrastructure.repository import InMemoryActivityRepository, InMemoryProductRepository
    from app.services import activity, product as product_service
    from backend_v2_helpers import TOKEN, request_payload, headers
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setattr(product_service, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    try:
        with TestClient(api.app) as test_client:
            payload = request_payload(statuses=("DANGER",))
            payload["message"] = "바나나맛우유 먹어도 돼?"
            payload["product"]["productName"] = "바나나맛우유"
            data = test_client.post("/chat", json=payload, headers=headers(payload)).json()
    finally:
        activity.set_activity_repository(old)
    assert data["intent"] == "can_i_eat"


def test_comparison_against_the_selected_product_in_a_stateless_request(monkeypatch):
    from app.core.backend_analysis import backend_analysis_active
    from app.services import comparison_context
    catalog = {"진라면": [_product("진라면(순한맛)", "3"), _product("진라면 매운맛", "4")]}
    monkeypatch.setattr(comparison_context, "find_product_candidates_from_message",
                        lambda query, limit=3: catalog[query.replace(" 찾아줘", "")])
    token = backend_analysis_active.set(True)
    try:
        products, confirmation, state, _ = comparison_context.resolve_comparison(
            "진라면이랑 비교해줘", _product("신라면", "1", 1.0), {})
    finally:
        backend_analysis_active.reset(token)
    assert confirmation is None
    assert [product.product_name for product in products] == ["신라면", "진라면(순한맛)"]
    assert state["comparison_auto_picked"] == ["진라면(순한맛)"]


def test_comparison_with_a_selected_product_is_not_a_product_switch():
    from app.services.conversation_router import find_explicit_product_override
    assert find_explicit_product_override("진라면이랑 비교해줘", _product("신라면", "1", 1.0)) == (None, None)


def test_naming_the_selected_product_never_asks_to_select_it_again(monkeypatch):
    # '새우깡은?' right after tapping 새우깡's card looped on the same choice.
    from fastapi.testclient import TestClient
    from app.api import main as api
    from app.infrastructure.repository import InMemoryActivityRepository, InMemoryProductRepository
    from app.services import activity, product as product_service
    from backend_v2_helpers import TOKEN, request_payload, headers
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_EXTERNAL_AI", "false")
    monkeypatch.setattr(product_service, "_default_repository", InMemoryProductRepository())
    old = activity.get_activity_repository()
    activity.set_activity_repository(InMemoryActivityRepository())
    try:
        with TestClient(api.app) as test_client:
            payload = request_payload(statuses=("DANGER",))
            payload["product"]["productName"] = "새우깡"
            payload["message"] = "새우깡은?"
            data = test_client.post("/chat", json=payload, headers=headers(payload)).json()
    finally:
        activity.set_activity_repository(old)
    assert not data["product_candidates"]
    assert data["answer"].startswith("새우깡 기준으로 확인했어요.")
    assert data["answer_source"] == "backend"


def test_comparison_without_profile_allergies_says_so_and_shows_labels():
    from app.core.models import Profile
    from app.services.general_handlers import _comparison_allergy_summary
    products = [Product(product_name="새우깡", barcode="1", allergy=["대두", "새우"]),
                Product(product_name="신라면컵", barcode="2", allergy=["밀"])]
    answer, *_ , missing = _comparison_allergy_summary(products, [Profile(name="웹테스트")], "새우깡이랑 신라면컵 비교")
    assert answer.startswith("등록된 알레르기가 없어 개인 알레르기 비교는 하지 않았어요.")
    assert "새우깡: 대두, 새우" in answer and "신라면컵: 밀" in answer
    assert "판정 정보가 부족" not in answer


def test_comparison_without_an_axis_lists_main_values_instead_of_asking():
    from app.services.general_handlers import _key_nutrient_overview
    text = _key_nutrient_overview([Product(product_name="신라면컵", nutrition={"kcal": 300.3, "sodium": 1290.25}),
                                   Product(product_name="진라면", nutrition={"kcal": 500.4, "sodium": 1880.4})])
    assert "- 열량: 신라면컵 300.3kcal, 진라면 500.4kcal" in text
    assert "- 나트륨:" in text and "정하지 않았어요" not in text


@pytest.mark.parametrize("message, expected", [
    ("진라면이랑 비교해줄래?", ["진라면"]), ("진라면이랑 비교해주세요", ["진라면"]),
    ("새우깡이랑 쌀새우깡 비교해 줄래요?", ["새우깡", "쌀새우깡"]), ("새우깡과 포카칩 비교해 줄 수 있어?", ["새우깡", "포카칩"]),
])
def test_comparison_targets_drop_polite_request_endings(message, expected):
    from app.services.general_handlers import _message_product_segments
    assert _message_product_segments(message) == expected


@pytest.mark.parametrize("message, urgency", [
    ("아이가 우유 마시고 온몸에 두드러기가 났어", "urgent"), ("땅콩 먹고 가려워", "urgent"),
    ("우유 마시고 두드러기가 안 없어져", "urgent"), ("방금 새우깡 먹었는데 입술이 부었어", "emergency"),
])
def test_reaction_after_eating_always_gets_safety_guidance(message, urgency):
    from app.services.emergency_triage import build_emergency_triage_response
    response = build_emergency_triage_response(message)
    assert response is not None and response.safety_guidance.urgency == urgency
    assert "119" in response.answer


@pytest.mark.parametrize("message", ["새우깡 먹었는데 두드러기는 안 났어", "두드러기가 뭐야?", "예전에 우유 먹고 두드러기 난 적 있어"])
def test_no_reaction_guidance_without_a_current_symptom(message):
    from app.services.emergency_triage import build_emergency_triage_response
    assert build_emergency_triage_response(message) is None


def test_unknown_allergy_verdict_gets_a_concrete_next_step_per_person():
    from app.core.models import Profile
    from app.services.product_facts import unknown_allergy_guidance
    snack = Product(product_name="농심 새우깡90G", barcode="1", allergy=[])
    assert unknown_allergy_guidance(snack, Profile(name="나", allergies=["shrimp"])).startswith("피하는 게 좋아요. 제품명에 '새우'가")
    assert unknown_allergy_guidance(snack, Profile(name="엄마", allergies=["milk"])) == (
        "확인한 결과 등록된 정보에서는 우유 성분이 보이지 않아요. 다만 이 상품은 알레르기 표시가 등록되어 "
        "있지 않으니, 불안하시면 드시기 전에 제품 포장지를 확인해 주세요.")
    labelled = Product(product_name="새우깡", barcode="2", allergy=["새우"])
    assert unknown_allergy_guidance(labelled, Profile(name="나", allergies=["shrimp"])) is None
    chips = Product(product_name="포카칩오리지널", barcode="3", allergy=["대두", "소고기", "우유"])
    assert unknown_allergy_guidance(chips, Profile(name="나", allergies=["shrimp"])) == (
        "확인한 결과 등록된 알레르기 표시(대두, 소고기, 우유)에는 새우가 보이지 않아요. "
        "다만 표시 정보가 완전한지는 확인되지 않으니, 불안하시면 드시기 전에 제품 포장지를 확인해 주세요.")


def test_common_vowel_typos_are_retried_and_curiosity_ends_a_name():
    from app.services.product import _common_vowel_typos_fixed
    assert _common_vowel_typos_fixed("빼뺴로") == "빼빼로"
    assert extract_product_search_query("새우깡 영양성분 알고 싶어") == "새우깡"
    assert "궁금" not in extract_product_search_query("초코파이 성분 궁금해")



def test_every_product_answer_ends_with_the_reference_note_unless_brevity_was_asked():
    from app.core.public_product import public_product_payload
    from app.services.followup_questions import PRODUCT_REFERENCE_NOTE, tidy_followups
    product = public_product_payload(Product(product_name="새우깡", barcode="1"))
    answer = ChatResponse(intent="ingredient_check", answer="새우깡 기준으로 확인했어요.", risk_level="unknown",
                          current_product=product)
    assert tidy_followups(answer, "원재료 알려줘", team_mode=True).answer.endswith(PRODUCT_REFERENCE_NOTE)
    assert PRODUCT_REFERENCE_NOTE not in tidy_followups(answer, "한 줄로 요약해줘", team_mode=True).answer
    general = ChatResponse(intent="general_nutrition_guide", answer="당류는 탄수화물의 일부예요.", risk_level="unknown")
    assert PRODUCT_REFERENCE_NOTE not in tidy_followups(general, "당류랑 탄수화물 차이", team_mode=True).answer


@pytest.mark.parametrize("message, expected", [
    ("오레오 오즈 엄마랑 같이 먹어도 돼?", "오레오 오즈"), ("허니버터칩 한 봉지 칼로리 얼마야?", "허니버터칩"),
    ("코카콜라 당 얼마나 있어?", "코카콜라"), ("두유에 대두 들어있지?", "두유"),
])
def test_round3_product_names(message, expected):
    assert extract_product_search_query(message) == expected


def test_round3_comparison_facility_and_tag_question():
    from app.services.general_handlers import _message_product_segments
    assert _message_product_segments("칸쵸랑 빼빼로 뭐가 덜 달아?") == ["칸쵸", "빼빼로"]
    assert rule_based_classify_intent("칸쵸랑 빼빼로 뭐가 덜 달아?", None, None, []) == "food_compare"
    assert "allergy_facility_warning" in requested_topics("같은 시설에서 만든다는 표시는 무슨 뜻이야?")
    assert rule_based_classify_intent("에 대두 들어있지?", Product(product_name="두유", barcode="1", product_found=True),
                                      None, []) == "ingredient_check"


def test_unknown_verdict_with_raw_materials_only_names_what_was_checked():
    from app.core.models import Profile
    from app.services.product_facts import unknown_allergy_guidance
    snack = Product(product_name="꼬깔콘고소한맛", barcode="4", rawmtrl_nm="옥수수, 식물성유지")
    assert unknown_allergy_guidance(snack, Profile(name="나", allergies=["shrimp"])).startswith(
        "확인한 결과 등록된 원재료에서는 새우가 보이지 않아요.")


@pytest.mark.parametrize("message", [
    # The Android product-detail button sends '<상품명>의 성분과 알레르기 주의사항을 알려줘'.
    "허니버터칩의 성분과 알레르기 주의사항을 알려줘", "허니버터칩 성분이랑 알레르기 알려줘",
    "허니버터칩의 알레르기 주의사항 알려줘", "허니버터칩 알레르기 알려줘",
])
def test_app_detail_button_question_is_not_a_new_allergy(message):
    from app.services.message_constraint_guard import _message_allergens
    parsed = _message_allergens(message)
    assert not parsed.uncertain and not parsed.confirmed


def test_personal_allergy_statements_still_count():
    from app.services.message_constraint_guard import _message_allergens
    assert _message_allergens("나 새우 알레르기 있는데 새우깡 먹어도 돼?").confirmed == {"새우"}
    assert _message_allergens("나는 키위 알레르기").uncertain == {"키위"}
