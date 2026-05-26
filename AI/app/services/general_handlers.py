from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.services.recommended_questions import pick_recommended_questions
from app.services.response_helpers import build_response, format_general_answer


def handle_general_food_health(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="general_food_health",
        answer=(
            "건강 기준으로 제품을 볼 때는 당류, 나트륨, 알레르기 유발 성분 표시를 먼저 같이 확인하는 편이 좋아요. "
            "특정 제품 기준으로 보고 싶다면 제품명을 입력하거나 스캔해 주세요."
        ),
        risk_level="unknown",
        reasons=["일반 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_food_health", "unknown", product),
    )


def handle_general_allergy_guide(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="general_allergy_guide",
        answer=format_general_answer(
            "알레르기가 있다면 제품을 고를 때 원재료명과 알레르기 표시를 가장 먼저 보는 것이 좋아요.",
            [
                "알레르기 유발 성분은 원재료명에 다른 이름으로 적혀 있을 수 있어요.",
                "우유, 대두, 땅콩처럼 자주 문제 되는 성분은 동의어까지 함께 확인하는 편이 안전해요.",
            ],
            ["개인 민감도나 제조공정 중 혼입 가능성은 제품 정보만으로 모두 판단하기 어려워요."],
            ["특정 제품이 궁금하면 제품명이나 바코드를 보내주면 더 구체적으로 설명할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["일반 알레르기 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_allergy_guide", "unknown", product),
    )


def handle_general_diet_guide(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="general_diet_guide",
        answer=format_general_answer(
            "식이습관에 맞는 제품을 고를 때는 원재료명과 영양성분을 같이 보는 것이 좋아요.",
            [
                "비건은 우유, 계란, 유청, 카제인 같은 동물성 유래 성분을 우선 확인해야 해요.",
                "저염식은 나트륨, 저당식은 당류, 체중관리는 열량과 지방을 먼저 보는 편이 좋아요.",
            ],
            ["같은 수치라도 제품 유형이나 한 번에 먹는 양에 따라 체감이 달라질 수 있어요."],
            ["특정 제품을 보내주면 현재 기준에 맞는지 제품별로 설명할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["일반 식이 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_diet_guide", "unknown", product),
    )


def handle_general_nutrition_guide(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="general_nutrition_guide",
        answer=format_general_answer(
            "영양성분은 하나만 보기보다 함께 해석하는 것이 좋아요.",
            [
                "당류와 탄수화물은 혈당 관리에서 같이 보는 편이 좋아요.",
                "나트륨은 저염식에서, 열량과 지방은 체중 관리에서 자주 중요하게 봐요.",
                "단백질은 많을수록 좋다기보다 전체 열량과 다른 성분과의 균형도 중요해요.",
            ],
            ["정확한 비교를 하려면 1회 제공량 기준인지 총 내용량 기준인지도 함께 확인해야 해요."],
            ["특정 영양성분이나 제품을 보내주면 더 구체적으로 설명할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["일반 영양 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_nutrition_guide", "unknown", product),
    )


def handle_unknown(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="unknown",
        answer=format_general_answer(
            "질문 뜻을 아직 정확히 잡지 못했어요.",
            [],
            ["제품명, 성분명, 영양소명, 또는 건강 기준이 더 구체적으로 필요해요."],
            ["제품명을 입력하거나 바코드를 스캔하면 더 정확하게 안내할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["질문이 모호함"],
        profile=profile,
        task_type="product_chat" if product else "general_chat",
        recommended_questions=[
            "이 제품 먹어도 괜찮아?",
            "이 제품에 특정 성분이 들어 있어?",
            "영양성분을 설명해줘",
        ],
    )
