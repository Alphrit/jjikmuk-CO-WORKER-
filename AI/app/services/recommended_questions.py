from typing import List, Optional

from app.core.models import Product


def pick_recommended_questions(intent: str, risk_level: str, product: Optional[Product]) -> List[str]:
    """Follow-up chips for a reply, without features the current policy blocks."""
    from app.services.operational_policy import team_blocks_question
    return [question for question in _questions_for(intent, risk_level, product) if not team_blocks_question(question)]


def _questions_for(intent: str, risk_level: str, product: Optional[Product]) -> List[str]:
    product_name = product.product_name if product and product.product_name else "이 제품"

    if intent == "can_i_eat":
        if risk_level == "high":
            return [
                f"{product_name}에서 특히 조심해야 할 성분만 다시 정리해줘",
                "비슷한 제품 중에서 더 나은 것도 알려줘",
                "영양성분까지 포함해서 왜 주의해야 하는지 설명해줘",
            ]
        if risk_level == "medium":
            return [
                "어떤 성분이나 수치 때문에 조심해야 하는지 자세히 알려줘",
                "당류나 나트륨 같은 표시 수치도 설명해줘",
                "비슷한 제품이랑 비교해서 설명해줘",
            ]
        return [
            "알레르기 유발 성분도 같이 확인해줘",
            "나트륨이나 당류 같은 영양성분도 알려줘",
            "다른 프로필 기준으로 보면 결과가 달라지는지 알려줘",
        ]

    if intent == "ingredient_check":
        return [
            "다른 알레르기 성분도 같이 확인해줘",
            "이 제품이 전체적으로 먹어도 괜찮은지도 알려줘",
            "영양성분까지 같이 설명해줘",
        ]

    if intent == "nutrition_explain":
        return [
            "다른 영양성분도 같이 설명해줘",
            "이 수치까지 포함해서 먹어도 괜찮은지 알려줘",
            "다른 프로필 기준으로 보면 어떻게 달라지는지 알려줘",
        ]

    if intent == "product_summary":
        return [
            "이 제품 먹어도 되는지도 확인해줘",
            "알레르기 유발 성분만 따로 정리해줘",
            "영양성분을 표시 기준량과 같이 설명해줘",
        ]

    if intent == "alternative_food_recommend":
        return [
            "알레르기 기준으로 대체 식품을 추천해줘",
            "당류나 나트륨이 낮은 대체 제품을 찾아줘",
            "비슷한 제품이랑 영양성분을 비교해줘",
        ]

    if intent == "food_compare":
        return [
            "칼로리 기준으로 비교해줘",
            "단백질과 나트륨 기준으로 비교해줘",
            "알레르기 성분까지 포함해서 비교해줘",
        ]

    if intent.startswith("general_"):
        return [
            "무설탕과 무가당의 차이를 설명해줘",
            "같은 제조시설이라는 표시의 뜻을 알려줘",
            "영양성분표의 %는 무슨 뜻이야?",
        ]

    return [
        "제품명이나 바코드 기준으로 다시 확인해줘",
        "성분 기준과 영양성분 기준을 나눠서 설명해줘",
    ]
