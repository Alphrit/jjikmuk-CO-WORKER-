from typing import List, Optional

from app.data.dictionaries import (
    EATABILITY_KEYWORDS,
    GENERAL_ALLERGY_GUIDE_KEYWORDS,
    GENERAL_DIET_GUIDE_KEYWORDS,
    GENERAL_NUTRITION_GUIDE_KEYWORDS,
    HEALTH_KEYWORDS,
    INCLUSION_KEYWORDS,
    NUTRITION_EVAL_KEYWORDS,
    PROFILE_RECHECK_KEYWORDS,
    SUMMARY_KEYWORDS,
)
from app.intent.common import (
    AMBIGUOUS_INTENT,
    AMBIGUOUS_SHORT_QUESTIONS,
    FOLLOWUP_CONNECTORS,
    HEALTH_EVAL_KEYWORDS,
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


def is_health_question(text: str) -> bool:
    has_health_keyword = contains_any(text, HEALTH_KEYWORDS)
    has_health_eval = contains_any(text, HEALTH_EVAL_KEYWORDS)
    has_target_reference = contains_any(text, TARGET_REFERENCE_KEYWORDS)

    if has_health_keyword and has_health_eval:
        return True

    if has_health_keyword:
        return True

    if has_target_reference and has_health_eval:
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
    if "다시" in text and identifiers and contains_any(text, identifiers):
        return True

    return False


def is_ingredient_question(text: str) -> bool:
    ingredient = detect_ingredient_keyword(text)
    has_inclusion = contains_any(text, INCLUSION_KEYWORDS)
    return ingredient is not None and has_inclusion


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

    has_check_word = contains_any(text, INCLUSION_KEYWORDS + ["없어", "있어", "확인", "알려", "정리", "뭐", "무엇"])
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
        ],
    )


def is_nutrition_question(text: str) -> bool:
    nutrient = detect_nutrition_keyword(text)
    if nutrient is None:
        return False

    if is_nutrition_word_part_of_product_name(text, nutrient):
        return False

    extra_eval_keywords = NUTRITION_EVAL_KEYWORDS + [
        "괜찮아",
        "괜찮을까",
        "어때",
        "많아",
        "높아",
        "적어",
    ]
    return contains_any(text, extra_eval_keywords)


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


def is_general_diet_guide_question(text: str, product: Optional[Product]) -> bool:
    return not has_product_context(product) and contains_any(text, GENERAL_DIET_GUIDE_KEYWORDS)


def is_general_nutrition_guide_question(text: str, product: Optional[Product]) -> bool:
    return not has_product_context(product) and contains_any(text, GENERAL_NUTRITION_GUIDE_KEYWORDS)


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

    if is_health_question(text):
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


def is_clear_health_question(text: str, product: Optional[Product]) -> bool:
    if not has_product_context(product):
        return False

    has_health_keyword = contains_any(text, HEALTH_KEYWORDS)
    has_health_eval = contains_any(text, HEALTH_EVAL_KEYWORDS)
    has_eatability_keyword = contains_any(text, EATABILITY_KEYWORDS)
    return has_health_keyword and (has_health_eval or has_eatability_keyword)


def is_named_product_general_evaluation(text: str, product: Optional[Product]) -> bool:
    if not has_product_context(product) or not product.product_name:
        return False

    product_name = product.product_name.strip().lower()
    return product_name in text and contains_any(text, HEALTH_EVAL_KEYWORDS)


def is_explicit_general_food_health_question(text: str, product: Optional[Product]) -> bool:
    if has_product_context(product):
        return False

    if contains_any(text, HEALTH_KEYWORDS) and contains_any(
        text,
        ["고를 때", "골라", "고르는", "기준", "뭘 봐", "뭐 봐", "어떻게", "간식", "식단", "추천"],
    ):
        return True

    return contains_any(text, HEALTH_KEYWORDS) and contains_any(text, HEALTH_EVAL_KEYWORDS)


def rule_based_classify_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    chat_history: List[ChatHistoryItem],
) -> str:
    text = normalize_message(message)

    if is_missing_product(product):
        return "missing_product"

    if text == "":
        if has_product_context(product):
            return "product_summary"
        return "unknown"

    if is_profile_recheck_question(text, product, profile):
        return "profile_based_recheck"

    if is_ingredient_question(text):
        return "ingredient_check"

    if is_contextual_caution_detail_followup(text, product, chat_history):
        return "can_i_eat"

    if is_generic_ingredient_or_allergy_question(text, product, chat_history):
        return "ingredient_check"

    if is_contextual_ingredient_followup(text, product, chat_history):
        return "ingredient_check"

    if is_nutrition_question(text):
        return "nutrition_explain"

    if is_contextual_nutrition_followup(text, product, chat_history):
        return "nutrition_explain"

    if is_contextual_eatability_followup(text, product, chat_history):
        if is_health_question(text) or is_profile_reference_or_shift_question(text, profile):
            if is_health_question(text):
                return "health_risk_check"
            return AMBIGUOUS_INTENT
        return "can_i_eat"

    if is_contextual_product_summary_followup(text, product, chat_history):
        return "product_summary"

    if is_ambiguous_product_followup(text, product, chat_history):
        return AMBIGUOUS_INTENT

    if is_product_summary_question(text, product):
        return "product_summary"

    if is_named_product_general_evaluation(text, product):
        return "can_i_eat"

    if is_clear_eatability_question(text, product):
        if is_health_question(text) or is_profile_reference_or_shift_question(text, profile):
            if is_health_question(text):
                return "health_risk_check"
            return AMBIGUOUS_INTENT
        return "can_i_eat"

    if is_context_rich_fallback_candidate(text, product, profile, chat_history):
        return AMBIGUOUS_INTENT

    if is_clear_health_question(text, product):
        return "health_risk_check"

    if is_general_allergy_guide_question(text, product):
        return "general_allergy_guide"

    if is_general_diet_guide_question(text, product):
        return "general_diet_guide"

    if is_general_nutrition_guide_question(text, product):
        return "general_nutrition_guide"

    if is_explicit_general_food_health_question(text, product):
        return "general_food_health"

    if is_unknown_question(text, product):
        return "unknown"

    return "unknown"
