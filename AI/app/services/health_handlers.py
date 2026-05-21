from typing import Optional

from app.core.models import ChatResponse, Product, Profile
from app.services.recommended_questions import pick_recommended_questions
from app.services.response_helpers import (
    build_response,
    format_health_answer,
    get_profile_prefix,
    message_mentions_other_target,
)


def _build_weight_control_response(profile: Optional[Profile], product: Product, prefix: str, missing_next_step: str) -> ChatResponse:
    kcal = product.nutrition.get("kcal")
    fat = product.nutrition.get("fat")

    if kcal is None and fat is None:
        return build_response(
            intent="health_risk_check",
            answer=format_health_answer(
                f"{prefix} 체중 관리 관점으로 보려면 열량이나 지방 정보가 더 필요해요.",
                [],
                ["현재 제품 정보만으로는 열량과 지방 수치를 확인하지 못했어요."],
                [missing_next_step],
            ),
            risk_level="unknown",
            reasons=["열량/지방 정보 부족"],
            profile=profile,
            recommended_questions=pick_recommended_questions("health_risk_check", "unknown", product),
        )

    reasons = []
    if kcal is not None:
        reasons.append(f"열량 {kcal}")
    if fat is not None:
        reasons.append(f"지방 {fat}")

    if (kcal is not None and kcal >= 300) or (fat is not None and fat >= 20):
        return build_response(
            intent="health_risk_check",
            answer=format_health_answer(
                f"{prefix} 다이어트나 체중 관리 목적이라면 조금 주의해서 보는 게 좋아요.",
                [f"확인된 수치 기준으로 {' ,'.join(reasons)}이 부담이 될 수 있어요.".replace(" ,", ", ")],
                [],
                ["한 번에 먹는 양을 줄이거나 다른 간식과 비교해 보는 것이 좋아요."],
            ),
            risk_level="medium",
            reasons=reasons if reasons else ["체중 관리 주의 필요"],
            profile=profile,
            recommended_questions=pick_recommended_questions("health_risk_check", "medium", product),
        )

    return build_response(
        intent="health_risk_check",
        answer=format_health_answer(
            f"{prefix} 다이어트 관점에서 아주 부담이 큰 편으로 보이진 않아요.",
            [f"현재 확인된 수치 기준으로 {' ,'.join(reasons)}이 아주 높지는 않았어요.".replace(" ,", ", ")],
            ["총 섭취량이나 함께 먹는 음식에 따라 체감은 달라질 수 있어요."],
            ["같은 종류 제품과 열량, 지방을 비교하면 더 도움이 돼요."],
        ),
        risk_level="low",
        reasons=reasons if reasons else ["열량/지방 수치가 아주 높지는 않음"],
        profile=profile,
        recommended_questions=pick_recommended_questions("health_risk_check", "low", product),
    )


def _build_blood_sugar_response(profile: Optional[Profile], product: Product, prefix: str, missing_primary: str, missing_next_step: str) -> ChatResponse:
    sugar = product.nutrition.get("sugars")
    carbs = product.nutrition.get("carbohydrate")

    if sugar is None and carbs is None:
        return build_response(
            intent="health_risk_check",
            answer=format_health_answer(
                f"{prefix} {missing_primary}",
                [],
                ["현재 제품 정보만으로는 당류와 탄수화물 수치를 확인하지 못했어요."],
                [missing_next_step],
            ),
            risk_level="unknown",
            reasons=["당류/탄수화물 정보 부족"],
            profile=profile,
            recommended_questions=pick_recommended_questions("health_risk_check", "unknown", product),
        )

    reasons = []
    if sugar is not None:
        reasons.append(f"당류 {sugar}")
    if carbs is not None:
        reasons.append(f"탄수화물 {carbs}")

    if (sugar is not None and sugar >= 15) or (carbs is not None and carbs >= 30):
        return build_response(
            intent="health_risk_check",
            answer=format_health_answer(
                f"{prefix} 혈당 관리 측면에서는 조금 주의하는 게 좋아요.",
                [f"확인된 수치 기준으로 {' ,'.join(reasons)}이 높은 편일 수 있어요.".replace(" ,", ", ")],
                [],
                ["한 번에 먹는 양과 함께 먹는 음식도 같이 조절하는 편이 좋아요."],
            ),
            risk_level="medium",
            reasons=reasons if reasons else ["혈당 관리 주의 필요"],
            profile=profile,
            recommended_questions=pick_recommended_questions("health_risk_check", "medium", product),
        )

    return build_response(
        intent="health_risk_check",
        answer=format_health_answer(
            f"{prefix} 혈당 관리 기준에서 바로 위험해 보이진 않아요.",
            [f"현재 확인된 수치 기준으로 {' ,'.join(reasons)}이 아주 높은 편은 아니었어요.".replace(" ,", ", ")],
            ["개인별 혈당 반응 차이까지는 제품 정보만으로 판단하기 어려워요."],
            ["민감하다면 섭취량을 조절하면서 확인하는 편이 안전해요."],
        ),
        risk_level="low",
        reasons=reasons if reasons else ["당류/탄수화물 수치가 아주 높지는 않음"],
        profile=profile,
        recommended_questions=pick_recommended_questions("health_risk_check", "low", product),
    )


def handle_health_risk_check(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    prefix = get_profile_prefix(profile)
    text = message.strip()

    if message_mentions_other_target(text, profile):
        profile_name = profile.profile_name if profile else "현재 선택된"
        return build_response(
            intent="health_risk_check",
            answer=(
                f"지금은 '{profile_name}' 프로필 기준으로 보고 있어요. "
                "해당 대상 기준으로 정확히 보려면 그 프로필로 바꾼 뒤 다시 확인해 주세요."
            ),
            risk_level="unknown",
            reasons=["질문 대상과 현재 프로필 불일치"],
            profile=profile,
        )

    if not profile:
        return build_response(
            intent="health_risk_check",
            answer=format_health_answer(
                "지금은 프로필 정보가 없어서 일반 기준으로만 설명드릴 수 있어요.",
                ["질환, 알레르기, 식이습관 정보가 없으면 개인 기준 판단이 제한돼요."],
                ["같은 제품도 건강 상태에 따라 주의 포인트가 달라질 수 있어요."],
                ["프로필 정보를 함께 보내주면 더 개인화해서 볼 수 있어요."],
            ),
            risk_level="unknown",
            reasons=["프로필 정보 없음"],
            profile=profile,
            recommended_questions=pick_recommended_questions("health_risk_check", "unknown", product),
        )

    if "다이어트" in text or "체중관리" in text:
        return _build_weight_control_response(
            profile,
            product,
            prefix,
            "영양성분표가 보이면 다시 더 정확히 볼 수 있어요.",
        )

    if "당뇨" in text or "혈당" in text:
        return _build_blood_sugar_response(
            profile,
            product,
            prefix,
            "혈당 관리 관점으로 보려면 당류나 탄수화물 정보가 더 필요해요.",
            "영양성분표가 보이면 다시 더 정확히 볼 수 있어요.",
        )

    if "다이어트" in profile.conditions:
        return _build_weight_control_response(
            profile,
            product,
            prefix,
            "영양성분표가 보이면 다시 판단해 드릴게요.",
        )

    if "당뇨" in profile.conditions:
        return _build_blood_sugar_response(
            profile,
            product,
            prefix,
            "혈당 관리 관점에서 판단하려면 당류나 탄수화물 정보가 더 필요해요.",
            "영양성분표가 보이면 다시 판단해 드릴게요.",
        )

    return build_response(
        intent="health_risk_check",
        answer=format_health_answer(
            f"{prefix} 눈에 띄는 위험 신호는 크지 않아 보여요.",
            ["질문에서 직접 언급된 건강 기준과 충돌하는 수치나 성분은 뚜렷하게 보이지 않았어요."],
            ["개인별 민감도나 섭취량 차이까지는 제품 정보만으로 모두 반영하기 어려워요."],
            ["원하는 기준이 있으면 당뇨, 다이어트, 저염식처럼 조금 더 구체적으로 물어봐 주세요."],
        ),
        risk_level="low",
        reasons=["명확한 위험 신호 미확인"],
        profile=profile,
        recommended_questions=pick_recommended_questions("health_risk_check", "low", product),
    )
