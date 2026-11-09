import re
from typing import List, Optional

from app.data.dictionaries import (
    EATABILITY_KEYWORDS,
    ALTERNATIVE_FOOD_KEYWORDS,
    FOOD_COMPARISON_KEYWORDS,
    GENERAL_ALLERGY_GUIDE_KEYWORDS,
    GENERAL_DIET_GUIDE_KEYWORDS,
    GENERAL_NUTRITION_GUIDE_KEYWORDS,
    HEALTH_KEYWORDS,
    INCLUSION_KEYWORDS,
    NUTRITION_EVAL_KEYWORDS,
    PROFILE_RECHECK_KEYWORDS,
    SPECIAL_DIET_KEYWORDS,
    SUMMARY_KEYWORDS,
)
from app.intent.common import (
    AMBIGUOUS_INTENT,
    AMBIGUOUS_SHORT_QUESTIONS,
    FOOD_DOMAIN_KEYWORDS,
    FOLLOWUP_CONNECTORS,
    SUITABILITY_EVAL_KEYWORDS,
    OUT_OF_DOMAIN_KEYWORDS,
    TARGET_REFERENCE_KEYWORDS,
)
from app.intent.text import (
    contains_any,
    detect_ingredient_keyword,
    detect_nutrition_keyword,
    has_product_context,
    is_missing_product,
    normalize_message,
    profile_identifiers,
)
from app.core.models import ChatHistoryItem, Product, Profile
from app.intent.explanation_topics import explanation_intent


def is_diet_or_nutrition_question(text: str) -> bool:
    has_scope_keyword = contains_any(text, HEALTH_KEYWORDS)
    has_suitability_eval = contains_any(text, SUITABILITY_EVAL_KEYWORDS)
    has_target_reference = contains_any(text, TARGET_REFERENCE_KEYWORDS)

    if has_scope_keyword and has_suitability_eval:
        return True

    if has_scope_keyword:
        return True

    if has_target_reference and has_suitability_eval:
        return True

    return False


def is_profile_recheck_question(
    text: str,
    product: Optional[Product],
    profile: Optional[Profile],
) -> bool:
    if not has_product_context(product):
        return False

    if contains_any(text, PROFILE_RECHECK_KEYWORDS):
        return True

    if "기준" in text and contains_any(text, TARGET_REFERENCE_KEYWORDS):
        return True

    identifiers = profile_identifiers(profile)
    if any(re.fullmatch(r"\s*(?:(?:그럼|그러면)\s*)?" + re.escape(name)
                        + r"(?:은|는|도)(?:요)?[^\w]*", text)
           for name in [*identifiers, *TARGET_REFERENCE_KEYWORDS]):
        return True
    if "다시" in text and identifiers and contains_any(text, identifiers):
        return True

    return False


def is_ingredient_question(text: str) -> bool:
    ingredient = detect_ingredient_keyword(text)
    has_inclusion = contains_any(text, INCLUSION_KEYWORDS)
    return ingredient is not None and has_inclusion


def is_product_required_ingredient_question(text: str) -> bool:
    if detect_ingredient_keyword(text) is not None and contains_any(text, INCLUSION_KEYWORDS + ["있나요", "있니", "있는지"]):
        return True

    return contains_any(
        text,
        [
            "이 성분",
            "그 성분",
            "이거 성분",
            "이건 성분",
            "이 제품 성분",
            "그 제품 성분",
            "현재 제품 성분",
            "성분 있어",
            "성분 있",
            "성분 들어",
            "원재료 뭐",
            "원재료 알려",
            "성분표",
            "성분표 보여",
            "알레르기 성분",
            "알러지 성분",
        ],
    )


def is_generic_ingredient_or_allergy_question(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product):
        return False

    has_generic_axis = contains_any(text, ["알레르기", "알러지", "원재료", "성분"])
    if not has_generic_axis:
        return False

    if contains_any(text, ["영양성분", "영양 성분"]):
        return False

    has_check_word = contains_any(text, INCLUSION_KEYWORDS + ["없어", "있어", "확인", "알려", "보여", "정리", "뭐", "무엇"])
    if has_check_word:
        return True

    return bool(chat_history) and contains_any(text, FOLLOWUP_CONNECTORS + ["다른"])


def is_contextual_ingredient_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product) or not chat_history:
        return False

    if contains_any(text, ["영양성분", "영양 성분", "칼로리", "열량", "나트륨", "당류", "단백질", "지방", "탄수화물"]):
        return False

    if detect_ingredient_keyword(text) is not None:
        return True

    if contains_any(text, ["알레르기", "알러지", "원재료", "성분"]):
        return True

    if contains_any(text, ["다른 건", "다른거", "또 뭐", "또 있어", "더 있어", "없어", "있어"]):
        recent_text = " ".join(item.content for item in chat_history[-4:]).lower()
        return contains_any(recent_text, ["알레르기", "알러지", "원재료", "성분", "포함", "표기"])

    return False


def is_contextual_caution_detail_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product) or not chat_history:
        return False

    if contains_any(text, ["있으면", "있는 사람", "환자", "제품 고를", "고를 때", "일반적으로"]):
        return False

    return contains_any(
        text,
        [
            "어떤 성분",
            "어떤 수치",
            "무슨 성분",
            "무슨 수치",
            "왜 조심",
            "왜 주의",
            "때문에 조심",
            "때문에 주의",
            "자세히 알려",
            "자세히 설명",
            "더 자세히",
            "왜 그런지",
            "쉬운 말로",
        ],
    )


def is_nutrition_question(text: str) -> bool:
    nutrient = detect_nutrition_keyword(text)
    if nutrient is None and contains_any(text, ["영양성분", "영양 성분", "영양정보", "영양 정보"]):
        return contains_any(text, ["알려", "설명", "확인", "보여", "정리", "뭐", "어떻게", "수치", "함량"])

    if nutrient is None:
        return False

    if is_nutrition_word_part_of_product_name(text, nutrient):
        return False

    if (re.search(r"(?:은|는|이|가|도)(?:요)?\s*[?？]?$", text)
            or re.search(r"\d+(?:\.\d+)?\s*(?:g|mg|kcal)?\s*(?:이야|인가|인거|이라는|이란)", text)):
        return True

    extra_eval_keywords = NUTRITION_EVAL_KEYWORDS + [
        "괜찮아",
        "괜찮을까",
        "어때",
        "많아",
        "높아",
        "적어",
        "알려",
        "설명",
        "확인",
        "얼마",
        "몇",
        "몇이야",
        "몇이",
        "수치",
        "함량",
        "보여",
        "정리",
    ]
    return contains_any(text, extra_eval_keywords)


def is_product_required_nutrition_question(text: str) -> bool:
    nutrient = detect_nutrition_keyword(text)
    if nutrient is None and contains_any(
        text,
        [
            "이거 영양",
            "이건 영양",
            "이 제품 영양",
            "그 제품 영양",
            "현재 제품 영양",
            "영양성분표",
            "영양 정보",
            "영양정보",
        ],
    ):
        return True

    if nutrient is None:
        return False

    return contains_any(
        text,
        [
            "함량",
            "수치",
            "얼마",
            "몇",
            "몇이야",
            "몇이",
            "알려",
            "확인",
            "들어",
            "들었",
            "있어",
        ],
    )


def is_nutrition_word_part_of_product_name(text: str, nutrient: str) -> bool:
    if nutrient != "단백질":
        return False

    product_name_terms = ["단백질바", "단백질 바", "단백질칩", "단백질 칩", "단백질드링크", "단백질 드링크"]
    if not contains_any(text, product_name_terms):
        return False

    explicit_nutrition_terms = [
        "단백질 함량",
        "단백질 수치",
        "단백질은",
        "단백질이",
        "단백질 얼마나",
        "단백질 많",
        "단백질 적",
        "단백질 들어",
    ]
    return not contains_any(text, explicit_nutrition_terms)


def is_contextual_nutrition_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product) or not chat_history:
        return False

    nutrient = detect_nutrition_keyword(text)
    if nutrient is not None and not is_nutrition_word_part_of_product_name(text, nutrient):
        return True

    if contains_any(text, ["영양성분", "영양 성분", "영양", "수치", "함량"]):
        return True

    if contains_any(text, ["높아", "낮아", "많아", "적어", "얼마", "어느 정도"]):
        recent_text = " ".join(item.content for item in chat_history[-4:]).lower()
        return contains_any(recent_text, ["영양", "나트륨", "당류", "단백질", "지방", "탄수화물", "칼로리", "열량"])

    return False


def is_contextual_eatability_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product) or not chat_history:
        return False

    if contains_any(text, EATABILITY_KEYWORDS):
        return True

    if contains_any(text, ["어때", "괜찮아", "괜찮을까", "먹어", "먹어도", "섭취", "피해야", "조심해야"]):
        return True

    return False


def is_alternative_product_request(text: str, product: Optional[Product]) -> bool:
    if not has_product_context(product):
        return False

    return contains_any(text, ["비슷한 제품", "대체 제품", "대체품", "더 나은", "나은 것도", "추천"])


def is_alternative_food_recommend_question(text: str, product: Optional[Product]) -> bool:
    has_alternative_word = contains_any(text, ALTERNATIVE_FOOD_KEYWORDS)
    if not has_alternative_word:
        return False

    if has_product_context(product) and not contains_any(text, ["대체", "대신", "대안", "비슷한 거", "뭐 먹지", "다른 거"]):
        return False

    has_condition = (
        contains_any(text, HEALTH_KEYWORDS)
        or detect_ingredient_keyword(text) is not None
        or detect_nutrition_keyword(text) is not None
        or contains_any(
            text,
            ["알레르기", "알러지", "채식", "저염", "저당", "글루텐프리", "저칼로리", "저지방", "고단백", "식단"]
        )
    )

    if has_product_context(product) and contains_any(
        text,
        ["비슷한 제품", "비슷한 거", "대체 제품", "대체품", "대체할", "대체", "더 나은", "나은 것도", "대신", "대안"],
    ):
        return True

    return has_condition and contains_any(text, ["대체", "대신", "추천", "대안", "비슷한 거", "뭐 먹지", "다른 거"])


def is_recipe_recommend_question(text: str) -> bool:
    return bool(re.search(r"요리(?:를|도)?\s*(?:추천|만들|알려)", text)) or contains_any(
        text,
        ["레시피", "조리법", "요리법", "만드는 법", "만들어 먹", "활용 요리"],
    )


def is_product_required_question_without_product(text: str) -> bool:
    if contains_any(text, EATABILITY_KEYWORDS):
        return True

    if is_product_required_ingredient_question(text):
        return True

    if is_product_required_nutrition_question(text):
        return True

    return False


def is_food_compare_question(text: str, product: Optional[Product]) -> bool:
    if not contains_any(text, FOOD_COMPARISON_KEYWORDS):
        return False

    if contains_any(text, ["브랜드", "회사", "업체"]) and detect_nutrition_keyword(text) is None:
        return False

    has_compare_axis = (
        detect_nutrition_keyword(text) is not None
        or contains_any(
            text,
            ["영양", "알레르기", "알러지", "채식", "저염", "저당", "글루텐프리", "저칼로리", "저지방", "고단백"]
        )
    )
    has_multiple_target_cue = contains_any(text, ["랑", "와", "과", "vs", "둘 중", "중에"])

    return has_compare_axis or has_multiple_target_cue or has_product_context(product)


def is_contextual_product_summary_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product) or not chat_history:
        return False

    return contains_any(text, ["이 제품", "제품", "설명", "정보", "요약", "정리", "전체", "전체적으로", "전반"]) and contains_any(
        text,
        ["설명", "알려", "요약", "정리", "뭐야", "어떤", "전체", "전체적으로", "전반"],
    )


def is_product_summary_question(text: str, product: Optional[Product]) -> bool:
    return has_product_context(product) and contains_any(text, SUMMARY_KEYWORDS)


def is_general_allergy_guide_question(text: str, product: Optional[Product]) -> bool:
    return not has_product_context(product) and contains_any(text, GENERAL_ALLERGY_GUIDE_KEYWORDS)


def is_general_allergy_person_question(text: str) -> bool:
    return (
        contains_any(text, ["알레르기가 있는 사람", "알러지가 있는 사람", "알레르기 있는 사람", "알러지 있는 사람"])
        or (
            contains_any(text, ["알레르기", "알러지"])
            and contains_any(text, ["있는 사람", "사람은", "있으면", "경우", "환자"])
        )
    )


def is_general_diet_guide_question(text: str, product: Optional[Product]) -> bool:
    return not has_product_context(product) and contains_any(text, GENERAL_DIET_GUIDE_KEYWORDS)


def is_productless_diet_recommendation(text: str, product: Optional[Product]) -> bool:
    if has_product_context(product):
        return False
    compact = "".join(text.lower().split())
    has_diet = any(
        "".join(alias.lower().split()) in compact
        for aliases in SPECIAL_DIET_KEYWORDS.values()
        for alias in aliases
    )
    return has_diet and any(cue in compact for cue in (
        "추천", "뭘먹", "뭐먹", "뭐가있", "먹을만한", "먹을수있는",
        "어떤제품", "제품알려", "골라", "찾아", "제품뭐", "식품뭐",
        "간식있", "먹지", "뭐가좋", "어떤게좋",
    ))


def is_general_nutrition_guide_question(text: str, product: Optional[Product]) -> bool:
    return not has_product_context(product) and contains_any(text, GENERAL_NUTRITION_GUIDE_KEYWORDS)


def is_label_claim_explanation(text: str, product: Optional[Product]) -> bool:
    if has_product_context(product):
        return False
    compact = "".join(text.split())
    if any(cue in compact for cue in (
        "이제품", "이거", "이음료", "그제품", "바코드", "먹어도", "마셔도",
    )):
        return False
    return any(term in compact for term in (
        "무당", "무가당", "무설탕", "설탕무첨가", "제로슈거", "감미료",
    )) and any(cue in compact for cue in (
        "기준", "차이", "뜻", "정의", "의미", "왜", "설명", "뭐", "알려",
    ))


def is_allergy_label_explanation(text: str, product: Optional[Product]) -> bool:
    if has_product_context(product):
        return False
    compact = "".join(text.split())
    if any(cue in compact for cue in (
        "이제품", "이거", "이음료", "그제품", "바코드", "먹어도", "마셔도",
    )):
        return False
    return any(term in compact for term in (
        "알레르기", "알러지", "교차접촉", "교차오염", "같은제조시설",
        "동일제조시설", "동일한제조시설", "같은제조라인",
    )) and any(cue in compact for cue in (
        "뜻", "의미", "뭐", "설명", "차이", "확인", "관리", "줄이", "줄여", "어떻게", "달라", "다르",
    ))


def is_general_nutrition_or_diet_question(text: str, product: Optional[Product]) -> bool:
    if has_product_context(product):
        return False

    has_health_or_diet = contains_any(text, HEALTH_KEYWORDS + GENERAL_DIET_GUIDE_KEYWORDS)
    has_nutrition = detect_nutrition_keyword(text) is not None or contains_any(text, GENERAL_NUTRITION_GUIDE_KEYWORDS)
    asks_for_guidance = contains_any(
        text,
        [
            "어떻게",
            "어떤",
            "무엇",
            "뭐",
            "뭘",
            "먼저",
            "확인",
            "봐야",
            "보면",
            "고를 때",
            "기준",
            "관리",
            "주의",
            "가이드",
            "알려",
            "설명",
        ],
    )

    return asks_for_guidance and (has_health_or_diet or has_nutrition)


_MEDICAL_SCOPE_PATTERN = re.compile(
    r"(?:당뇨(?:병)?|고혈압|저혈압|저혈당|신부전|신장\s*질환|콩팥|"
    r"간\s*질환|간염|지방간|심부전|부정맥|천식|통풍|셀리악|"
    r"루푸스|편두통|아토피|파킨슨|치매|암\s*환자|항암|"
    r"임신|임산부|수유|처방|진단|치료|복약|수술|금식|내시경|"
    r"환자(?:가|는|인데|에게)?|회복\s*중)"
)
_GENERAL_HEALTH_SCOPE_PATTERN = re.compile(
    r"(?:건강(?:에|을|은|한|하게|식)|몸(?:에|을)\s*(?:좋|나쁘)|면역력)"
)


def is_medical_scope_question(text: str) -> bool:
    return bool(_MEDICAL_SCOPE_PATTERN.search(text))


def is_general_health_scope_question(text: str) -> bool:
    """Reject broad wellness advice unless a supported food axis is explicit."""

    if not _GENERAL_HEALTH_SCOPE_PATTERN.search(text):
        return False
    supported_axis = (
        contains_any(
            text,
            ["알레르기", "알러지"] + GENERAL_DIET_GUIDE_KEYWORDS,
        )
        or detect_ingredient_keyword(text) is not None
        or detect_nutrition_keyword(text) is not None
    )
    return not supported_axis


def is_out_of_domain_question(text: str, product: Optional[Product]) -> bool:
    # Explicitly excluded features must not fall through to the generic
    # "먹어도 돼" product flow merely because they use the verb "먹다".
    if re.search(
        r"(?:타이레놀|진통제|항생제|처방약|복용량|몇\s*알|한\s*알\s*더|"
        r"약\s*(?:먹|복용)|영양제\s*(?:먹|복용)|"
        r"유통기한|소비기한|상한\s*것|상했|곰팡이|개봉한|냉장\s*보관)",
        text,
        flags=re.IGNORECASE,
    ):
        return True

    if is_general_health_scope_question(text):
        return True

    # This service evaluates food labels against allergies and explicit diet
    # requests; it does not decide suitability for diagnoses, pregnancy, or
    # treatment. This check intentionally applies even when a product is open.
    if is_medical_scope_question(text):
        return True

    if has_product_context(product):
        return False

    if not contains_any(text, OUT_OF_DOMAIN_KEYWORDS):
        return False

    return not contains_any(text, FOOD_DOMAIN_KEYWORDS)


def is_unknown_question(text: str, product: Optional[Product]) -> bool:
    return text in AMBIGUOUS_SHORT_QUESTIONS and not has_product_context(product)


def is_ambiguous_product_followup(
    text: str,
    product: Optional[Product],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product):
        return False

    if contains_any(text, SUMMARY_KEYWORDS):
        return False

    if detect_ingredient_keyword(text) is not None:
        return False

    if detect_nutrition_keyword(text) is not None:
        return False

    if contains_any(text, EATABILITY_KEYWORDS):
        return False

    if text in AMBIGUOUS_SHORT_QUESTIONS:
        return True

    if len(text) <= 8 and contains_any(text, ["이거", "이건", "이 제품", "어때", "괜찮아", "괜찮을까"]):
        return True

    if chat_history and len(text) <= 12 and contains_any(text, FOLLOWUP_CONNECTORS):
        return True

    return False


def is_profile_reference_or_shift_question(text: str, profile: Optional[Profile]) -> bool:
    if "기준" in text:
        return True

    if contains_any(text, TARGET_REFERENCE_KEYWORDS):
        return True

    return contains_any(text, profile_identifiers(profile))


def is_context_rich_fallback_candidate(
    text: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> bool:
    if not has_product_context(product):
        return False

    if is_diet_or_nutrition_question(text):
        return False

    if "기준" in text:
        return True

    if chat_history and contains_any(text, FOLLOWUP_CONNECTORS):
        return True

    if is_profile_reference_or_shift_question(text, profile):
        return True

    if contains_any(text, ["어때", "괜찮을까", "괜찮아", "어울려", "무난해"]):
        has_specific_axis = is_ingredient_question(text) or is_nutrition_question(text)
        if not has_specific_axis:
            return True

    return False


def is_clear_eatability_question(text: str, product: Optional[Product]) -> bool:
    return has_product_context(product) and contains_any(text, EATABILITY_KEYWORDS + ["어때"])


def is_clear_diet_or_nutrition_question(text: str, product: Optional[Product]) -> bool:
    if not has_product_context(product):
        return False

    has_health_keyword = contains_any(text, HEALTH_KEYWORDS)
    has_suitability_eval = contains_any(text, SUITABILITY_EVAL_KEYWORDS)
    has_eatability_keyword = contains_any(text, EATABILITY_KEYWORDS)
    return has_health_keyword and (has_suitability_eval or has_eatability_keyword)


def is_named_product_general_evaluation(text: str, product: Optional[Product]) -> bool:
    if not has_product_context(product) or not product.product_name:
        return False

    product_name = product.product_name.strip().lower()
    return product_name in text and contains_any(text, SUITABILITY_EVAL_KEYWORDS)


def rule_based_classify_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> str:
    from app.services.comparison_language import split_comparison_cancellation
    message, _ = split_comparison_cancellation(message)
    current_message = message
    if product is None:
        from app.intent.explanation_topics import resolve_explanation_question
        message = resolve_explanation_question(message, chat_history)
    text = normalize_message(message)

    if is_missing_product(product):
        return "missing_product"

    if text == "":
        if has_product_context(product):
            return "product_summary"
        return "unknown"

    if is_out_of_domain_question(text, product):
        return "out_of_domain"

    from app.intent.request_semantics import food_recommendation_request
    # A recommendation is requested by this turn, not by an earlier question
    # that explanation follow-ups prepend ('다른 제품이랑 제조 공장이 같대' +
    # '얼마나 섞였는지 %로 알려줘').
    if food_recommendation_request(current_message):
        return "alternative_food_recommend"

    from app.intent.request_semantics import nutrient_exclusion_question
    if product is None and nutrient_exclusion_question(message):
        return "general_nutrition_guide"

    if is_profile_recheck_question(text, product, profile):
        return "profile_based_recheck"

    from app.intent.explanation_topics import unsupported_allergy_quantity_question
    if product is None and unsupported_allergy_quantity_question(message):
        return "general_allergy_guide"

    if has_product_context(product) and re.fullmatch(r"\d+(?:\.\d+)?\s*(?:g|ml)\s*기준으로\s*(?:정리|알려|보여)\s*(?:해)?\s*줘[.!?]*", text):
        return "nutrition_explain"

    if has_product_context(product) and detect_nutrition_keyword(text) and not contains_any(text, EATABILITY_KEYWORDS):
        # A short field question or a zero-value confirmation still asks for
        # label facts; it does not require a fixed request verb.
        if (re.search(r"(?:은|는|도)\s*[?？]*$", text)
                or re.search(r"(?:안\s*보이|없|누락|모르).*?(?:0|영)\s*(?:g|mg)?", text)
                or re.search(r"0\s*(?:g|mg)?\s*(?:이|라|인|이라는|이란).*(?:뜻|거|건)", text)):
            return "nutrition_explain"

    if product is None and any(cue in text for cue in ("방법", "기준량", "뜻", "의미", "정의", "관계", "차이")):
        explanation = explanation_intent(text)
        if explanation:
            return explanation

    if is_food_compare_question(text, product):
        return "food_compare"

    if is_recipe_recommend_question(text):
        return "recipe_recommend"

    if product is None:
        explanation = explanation_intent(text)
        if explanation:
            return explanation

    if is_alternative_food_recommend_question(text, product):
        return "alternative_food_recommend"

    if is_productless_diet_recommendation(text, product):
        return "general_diet_guide"

    if not has_product_context(product) and is_general_allergy_person_question(text):
        return "general_allergy_guide"

    if (
        not has_product_context(product)
        and contains_any(text, HEALTH_KEYWORDS + GENERAL_DIET_GUIDE_KEYWORDS)
        and is_general_nutrition_or_diet_question(text, product)
    ):
        if contains_any(text, GENERAL_DIET_GUIDE_KEYWORDS):
            return "general_diet_guide"
        if detect_nutrition_keyword(text) is not None or contains_any(text, GENERAL_NUTRITION_GUIDE_KEYWORDS):
            return "general_nutrition_guide"
        return "out_of_domain"

    if is_label_claim_explanation(text, product):
        return "general_nutrition_guide"

    if is_allergy_label_explanation(text, product):
        return "general_allergy_guide"

    if not has_product_context(product) and is_product_required_question_without_product(text):
        return "missing_product"

    if is_ingredient_question(text):
        return "ingredient_check"

    if is_general_allergy_guide_question(text, product):
        return "general_allergy_guide"

    if is_general_nutrition_or_diet_question(text, product):
        if contains_any(text, GENERAL_DIET_GUIDE_KEYWORDS):
            return "general_diet_guide"
        if detect_nutrition_keyword(text) is not None or contains_any(text, GENERAL_NUTRITION_GUIDE_KEYWORDS):
            return "general_nutrition_guide"
        return "out_of_domain"

    if not has_product_context(product) and contains_any(text, EATABILITY_KEYWORDS):
        return "missing_product"

    if is_contextual_caution_detail_followup(text, product, chat_history):
        return "can_i_eat"

    if is_generic_ingredient_or_allergy_question(text, product, chat_history):
        return "ingredient_check"

    if has_product_context(product) and contains_any(text, EATABILITY_KEYWORDS) and (
        detect_nutrition_keyword(text) is not None or contains_any(text, ["수치", "영양"])
    ):
        # Asking about intake after a nutrition explanation still requires an
        # assessment; the contextual nutrition branch must not swallow it.
        return "can_i_eat"

    if is_contextual_ingredient_followup(text, product, chat_history):
        return "ingredient_check"

    if is_nutrition_question(text):
        return "nutrition_explain"

    if is_contextual_nutrition_followup(text, product, chat_history):
        return "nutrition_explain"

    if (
        has_product_context(product)
        and contains_any(text, ["알레르기 기준", "알러지 기준"])
        and contains_any(text, EATABILITY_KEYWORDS)
    ):
        return "can_i_eat"

    if is_contextual_eatability_followup(text, product, chat_history):
        if is_diet_or_nutrition_question(text) or is_profile_reference_or_shift_question(text, profile):
            if is_diet_or_nutrition_question(text):
                return "can_i_eat"
            return AMBIGUOUS_INTENT
        return "can_i_eat"

    if is_alternative_product_request(text, product):
        return "alternative_food_recommend"

    if is_contextual_product_summary_followup(text, product, chat_history):
        return "product_summary"

    if is_ambiguous_product_followup(text, product, chat_history):
        return AMBIGUOUS_INTENT

    if is_product_summary_question(text, product):
        return "product_summary"

    if is_named_product_general_evaluation(text, product):
        return "can_i_eat"

    if is_clear_eatability_question(text, product):
        if is_diet_or_nutrition_question(text) or is_profile_reference_or_shift_question(text, profile):
            if is_diet_or_nutrition_question(text):
                return "can_i_eat"
            return AMBIGUOUS_INTENT
        return "can_i_eat"

    if is_context_rich_fallback_candidate(text, product, profile, chat_history):
        return AMBIGUOUS_INTENT

    if is_clear_diet_or_nutrition_question(text, product):
        return "can_i_eat" if has_product_context(product) else "general_diet_guide"

    if is_general_diet_guide_question(text, product):
        return "general_diet_guide"

    if is_general_nutrition_guide_question(text, product):
        return "general_nutrition_guide"

    if is_unknown_question(text, product):
        return "unknown"

    return "unknown"
