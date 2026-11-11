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
