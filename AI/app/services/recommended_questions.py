from typing import List, Optional

from app.core.models import Product


def pick_recommended_questions(intent: str, risk_level: str, product: Optional[Product]) -> List[str]:
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
                "당류나 나트륨 같은 건강 기준으로도 설명해줘",
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

    if intent == "health_risk_check":
        return [
            "어떤 영양성분을 먼저 보면 되는지 쉽게 정리해줘",
            "먹어도 되는지 관점으로 짧게 정리해줘",
            "비슷한 제품이 있다면 뭐가 더 나은지 비교해줘",
        ]

    if intent == "product_summary":
        return [
            "이 제품 먹어도 되는지도 확인해줘",
            "알레르기 유발 성분만 따로 정리해줘",
            "영양성분은 어떤 편인지 설명해줘",
        ]

    if intent.startswith("general_"):
        return [
            "특정 제품 기준으로도 설명해줘",
            "알레르기나 영양 기준 중에서 하나를 더 구체적으로 설명해줘",
            "다른 프로필 기준으로 바꿔서 다시 알려줘",
        ]

    return [
        "제품명이나 바코드 기준으로 다시 확인해줘",
        "성분 기준과 영양성분 기준을 나눠서 설명해줘",
    ]
