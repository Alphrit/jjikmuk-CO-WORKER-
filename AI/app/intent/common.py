from dataclasses import dataclass


VALID_INTENTS = {
    "missing_product",
    "profile_based_recheck",
    "health_risk_check",
    "ingredient_check",
    "nutrition_explain",
    "can_i_eat",
    "product_summary",
    "general_allergy_guide",
    "general_diet_guide",
    "general_nutrition_guide",
    "general_food_health",
    "unknown",
}

AMBIGUOUS_INTENT = "ambiguous"
LLM_FALLBACK_INTENTS = {"unknown", AMBIGUOUS_INTENT}

HEALTH_EVAL_KEYWORDS = [
    "어때",
    "괜찮아",
    "괜찮을까",
    "문제 있어",
    "문제 없어",
    "주의해야 해",
    "안 좋아",
    "추천해",
    "먹어도 돼",
    "먹어도 되나",
    "먹어도 괜찮아",
    "먹어도 문제 없어",
]

TARGET_REFERENCE_KEYWORDS = [
    "우리 애",
    "아이",
    "아기",
    "자녀",
    "엄마",
    "아빠",
    "부모님",
    "할머니",
    "할아버지",
    "반려동물",
    "강아지",
    "고양이",
]

AMBIGUOUS_SHORT_QUESTIONS = [
    "이거?",
    "이건?",
    "괜찮나?",
    "어때?",
]

FOLLOWUP_CONNECTORS = [
    "그럼",
    "그러면",
    "근데",
    "그거",
    "그건",
    "이건",
    "이 제품",
]


@dataclass(frozen=True)
class IntentClassificationResult:
    intent: str
    source: str
    rule_intent: str
    llm_intent: str = "unknown"
