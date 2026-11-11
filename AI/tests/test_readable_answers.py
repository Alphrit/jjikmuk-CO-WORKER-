"""Readable answers: label-text basis, comparison wording, polished layout."""

import pytest

from app.core.models import ChatResponse, Product, Profile
from app.core.nutrition_basis import display_basis_label, label_text_serving
from app.infrastructure import llm
from app.services import answer_polish
from app.services.general_handlers import _comparison_allergy_summary, _key_nutrient_overview
from app.services.product_facts import product_facts_text

SHRIMP_LABEL = ("총내용량 90g 30g당 160kcal, 30g당 1일 영양성분 기준치에 대한 비율 총 내용량당 "
                "나트륨 200mg 10% 610mg 31%, 탄수화물 20g 6% 60g 19%")


def _saeukkang(**fields):
    return Product.model_validate({"barcode": "8801043035989", "productName": "새우깡", "energyKcal": 160.0,
                                   "sodiumMg": 200.0, "nutrientText": SHRIMP_LABEL,
                                   "allergyInfo": ["대두", "새우", "우유"], **fields})


def test_label_text_states_the_basis_only_when_its_kcal_matches():
    assert label_text_serving(_saeukkang()) == ("30g", "90g")
    assert display_basis_label(_saeukkang()) == "30g당 표시 기준"
    other = _saeukkang(energyKcal=325.2)  # the label text is of another record
    assert label_text_serving(other) is None and display_basis_label(other) == "영양 수치 기준 미확인"


@pytest.mark.parametrize("label, kcal, expected", [
    ("총내용량600g(120g×5봉지) 1봉지(120g)당 500kcal, 나트륨1,790mg 90%", 500.0, ("120g", "600g")),
    ("1회 제공량 1봉지(120g), 총 30회 제공량(3,600g) 1회 제공량당 함량 *%영양성분 기준치 열량 500kcal 탄수화물 80%",
     500.0, ("120g", "3600g")),
    ("1회 제공량 1봉지(120g) 열량 500kcal", 417.0, None),  # another record's label
])
def test_other_label_layouts_state_the_basis(label, kcal, expected):
    product = Product.model_validate({"barcode": "r", "productName": "라면", "energyKcal": kcal, "nutrientText": label})
    assert label_text_serving(product) == expected


def test_nutrition_answer_uses_the_label_basis_instead_of_unknown():
    text = product_facts_text(_saeukkang(), "100g당 칼로리 알려줘", "nutrition_explain")
    assert "열량: 160kcal (30g당)" in text and "(총 내용량 90g)" in text
    assert "기준량이 전달되지 않아" not in text and "30g당 수치로 안내했어요" in text


def test_comparison_shows_label_basis_names_allergen_once_and_skips_a_lone_name():
    honey = Product.model_validate({"barcode": "h", "productName": "허니버터칩", "energyKcal": 325.2,
                                    "rawmtrl_nm": "감자, 혼합식용유"})
    overview = _key_nutrient_overview([_saeukkang(), honey])
    assert "새우깡 160kcal(30g당 표시 기준)" in overview and "허니버터칩 325.2kcal\n" in overview
    me = Profile(profile_name="웹테스트", profile_id=31, allergies=["새우"])
    summary = _comparison_allergy_summary([_saeukkang(), honey], [me], "새우깡이랑 허니버터칩 비교해줘")[0]
    assert "새우(새우)" not in summary and "[웹테스트]" not in summary and "새우" in summary
    mom = Profile(profile_name="엄마", profile_id=32, allergies=["우유"])
    both = _comparison_allergy_summary([_saeukkang(), honey], [me, mom], "새우깡이랑 허니버터칩 비교해줘")[0]
    assert "[웹테스트]" in both and "[엄마]" in both


RULE = "등록된 영양정보 — 열량: 160kcal (30g당), 나트륨: 200mg (30g당), 당류: 1.6g (30g당). 포장지 영양성분 표시의 30g당 수치예요."


@pytest.fixture
def polish_on(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_POLISH_PRODUCT_ANSWERS", "true")
    yield lambda reply: llm.set_llm_client(lambda _prompt: reply)
    llm.set_llm_client(None)


def _response():
    return ChatResponse(intent="nutrition_explain", answer=RULE, answer_source="backend", risk_level="unknown")


def test_paragraphs_and_dot_bullets_are_kept(polish_on):
    polish_on("포장지 영양성분 표시의 30g당 수치예요.\n\n\n• 열량: 160kcal\n• 나트륨: 200mg\n• 당류: 1.6g")
    answer = answer_polish.polish_answer(_response()).answer
    assert answer == "포장지 영양성분 표시의 30g당 수치예요.\n\n• 열량: 160kcal\n• 나트륨: 200mg\n• 당류: 1.6g"


@pytest.mark.parametrize("reply", [
    "**30g당** 수치예요. 포장지 기준이에요.\n• 열량: 160kcal\n• 나트륨: 200mg\n• 당류: 1.6g",  # markdown
    "포장지 영양성분 표시의 30g당 수치예요.\n- 열량: 160kcal\n- 나트륨: 200mg\n- 당류: 1.6g",    # dash list
])
def test_markdown_or_other_list_markers_keep_the_rule_text(polish_on, reply):
    polish_on(reply)
    assert answer_polish.polish_answer(_response()).answer == RULE


def test_product_chips_name_the_product_because_the_app_sends_no_barcode():
    from app.services.followup_questions import tidy_followups
    response = ChatResponse(intent="can_i_eat", answer="새우깡은 피하시는 게 좋아요.", answer_source="backend",
                            risk_level="high",
                            recommended_questions=["이 상품의 원재료를 알려줘", "이 상품의 영양정보를 알려줘"])
    from app.core.public_product import public_product_payload
    response = response.model_copy(update={"current_product": public_product_payload(_saeukkang())})
    chips = tidy_followups(response, "새우깡의 원재료를 알려줘", team_mode=True).recommended_questions
    assert chips == ["새우깡의 영양정보를 알려줘"]  # the question just asked is not offered again


def test_a_day_amount_question_is_not_a_product_name():
    from app.services.product_search import extract_product_search_query
    assert extract_product_search_query("하루에 얼마나 먹어도 돼?") == ""
    assert extract_product_search_query("커피 하루에 몇 잔까지 괜찮아?") == "커피"
    assert extract_product_search_query("비건인데 먹어도 돼?") == ""
    assert extract_product_search_query("나 채식주의자야. 새우깡 먹어도 돼?") == "새우깡"


def test_rag_answers_are_polished_too():
    rag = ChatResponse(intent="general_nutrition_guide", answer="콜레스테롤은 몸의 모든 세포에 있는 밀랍 같은 지방 유사 물질입니다. 음식으로도 섭취해요.",
                       answer_source="rag", risk_level="unknown")
    assert answer_polish._eligible(rag)


def test_only_quoted_example_questions_must_stay_verbatim():
    original = "원재료 목록에는 새우가 보이지 않아요. '새우 함유' 같은 표시는 없어요. '새우깡 나트륨 알려줘'처럼 물어봐 주세요."
    terms = answer_polish._required_terms(original, ChatResponse(intent="ingredient_check", answer=original,
                                                                 answer_source="backend", risk_level="unknown"))
    assert "새우깡 나트륨 알려줘" in terms and "새우 함유" not in terms and "새우" in terms


def test_an_elaboration_follow_up_takes_the_previous_topic():
    from app.services.conversation_controls import message_with_previous_topic
    history = [{"role": "user", "content": "콜레스테롤이 뭐야?"}, {"role": "assistant", "content": "콜레스테롤은 …"}]
    assert message_with_previous_topic("조금만 더 자세하게 알려줘", history) == "콜레스테롤이 뭐야 자세히 알려줘"
    assert message_with_previous_topic("그게 무슨 말이야?", history) == "콜레스테롤이 뭐야 자세히 알려줘"
    assert message_with_previous_topic("조금만 더 자세하게 알려줘", []) == "조금만 더 자세하게 알려줘"
    assert message_with_previous_topic("나는 먹어도 돼?", history) == "나는 먹어도 돼?"  # not an elaboration


def test_a_person_singled_out_is_not_part_of_the_product_name():
    from app.services.product_search import extract_product_search_query
    assert extract_product_search_query("포키 엄마만 먹어도 돼?") == "포키"


def test_digits_inside_ingredient_names_are_not_values():
    assert answer_polish._numbers("등록된 원재료: 제3인산나트륨, 설탕. 나트륨1,790mg, 30g당 160kcal") == {"1790", "30", "160"}
