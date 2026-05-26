from typing import Dict, List, Optional

from app.core.models import Profile, Product, ChatResponse
from app.intent.text import detect_ingredient_keyword, detect_nutrition_keyword
from app.services.recommended_questions import pick_recommended_questions
from app.services.general_handlers import (
    handle_general_allergy_guide,
    handle_general_diet_guide,
    handle_general_food_health,
    handle_general_nutrition_guide,
    handle_unknown,
)


NUTRITION_LABELS = {
    "kcal": "열량",
    "carbohydrate": "탄수화물",
    "sugars": "당류",
    "protein": "단백질",
    "fat": "지방",
    "saturated_fat": "포화지방",
    "sodium": "나트륨",
}

DETAIL_REQUEST_KEYWORDS = [
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
]
from app.services.health_handlers import handle_health_risk_check
from app.services.product_status_handlers import handle_missing_product, handle_product_summary
from app.services.response_helpers import (
    allergy_terms_from_product_name,
    build_response,
    evaluate_special_diets,
    find_matching_aliases,
    find_triggered_allergies,
    format_binary_check_answer,
    format_can_i_eat_answer,
    format_nutrition_answer,
    format_structured_answer,
    get_profile_prefix,
    has_allergy_label_data,
    has_ingredient_data,
    has_meaningful_product_data,
    message_mentions_other_target,
    scan_disliked_ingredients,
)


def build_can_i_eat_result(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    prefix = get_profile_prefix(profile)

    if not has_meaningful_product_data(product):
        return build_response(
            intent="can_i_eat",
            answer=format_can_i_eat_answer(
                "지금 정보만으로는 먹어도 되는지 확실하게 판단하기 어려워요.",
                [],
                ["원재료명, 알레르기 표시, 영양정보가 충분하지 않아요."],
                ["바코드 스캔이나 영양성분표 이미지를 추가하면 더 정확히 볼 수 있어요."],
            ),
            risk_level="unknown",
            reasons=["제품 정보 부족"],
            profile=profile,
            recommended_questions=pick_recommended_questions("can_i_eat", "unknown", product),
        )

    if message_mentions_other_target(message, profile):
        profile_name = profile.profile_name if profile else "현재 선택된"
        return build_response(
            intent="can_i_eat",
            answer=format_can_i_eat_answer(
                f"지금은 '{profile_name}' 프로필 기준으로 보고 있어요.",
                ["질문에서 현재 프로필이 아닌 다른 대상이 언급됐어요."],
                ["대상 프로필 정보가 현재 요청에 포함되어 있지 않아요."],
                ["해당 대상 프로필로 전환한 뒤 다시 확인하면 더 정확히 안내할 수 있어요."],
            ),
            risk_level="unknown",
            reasons=["질문 대상과 현재 프로필 불일치"],
            profile=profile,
            recommended_questions=[
                f"{profile_name} 프로필 기준으로 다시 알려줘",
                "다른 가족 프로필 기준으로 다시 확인해줘",
                "이 제품의 알레르기 성분부터 먼저 알려줘",
            ],
        )

    reasons = []
    limitations = []
    next_steps = []
    risk_level = "low"

    if not has_allergy_label_data(product):
        limitations.append("알레르기 표시 정보가 없어 표시 대상 성분을 완전히 확인하지 못했어요.")

    if not has_ingredient_data(product):
        limitations.append("원재료명 정보가 부족해 세부 성분까지 확인하기 어려워요.")

    if profile:
        triggered = find_triggered_allergies(profile, product)
        if triggered:
            for allergy, matched_aliases in triggered:
                alias_preview = ", ".join(matched_aliases[:2])
                reasons.append(f"{allergy} 관련 성분({alias_preview})이 확인됐어요.")
            risk_level = "high"
            next_steps.append("알레르기 반응 이력이 있으면 소량도 주의하는 편이 안전해요.")

    diet_conflicts, diet_cautions, diet_limitations = evaluate_special_diets(profile, product, message)
    if diet_conflicts:
        risk_level = "high"
        reasons.extend(diet_conflicts)
        next_steps.append("제품 유형이나 원재료명을 다시 확인하면 대체 가능한 제품을 찾기 쉬워요.")

    if diet_cautions:
        if risk_level == "low":
            risk_level = "medium"
        reasons.extend(diet_cautions)
    limitations.extend(diet_limitations)

    disliked = scan_disliked_ingredients(profile, product)
    if disliked:
        risk_level = "medium" if risk_level == "low" else risk_level
        reasons.append(f"선호상 피하고 싶은 성분({', '.join(disliked[:3])})이 포함돼 있어요.")

    if profile and "당뇨" in profile.conditions:
        sugar = product.nutrition.get("sugars")
        carbs = product.nutrition.get("carbohydrate")
        if sugar is None and carbs is None:
            limitations.append("혈당 관리 관점에서 필요한 당류/탄수화물 정보가 부족해요.")
        elif (sugar is not None and sugar >= 15) or (carbs is not None and carbs >= 30):
            if risk_level == "low":
                risk_level = "medium"
            reasons.append(
                f"혈당 관리 기준에서 당류 {sugar if sugar is not None else '미확인'}, "
                f"탄수화물 {carbs if carbs is not None else '미확인'}로 주의가 필요해요."
            )

    if profile and "다이어트" in profile.conditions:
        kcal = product.nutrition.get("kcal")
        fat = product.nutrition.get("fat")
        if kcal is None and fat is None:
            limitations.append("체중 관리 관점에서 필요한 열량/지방 정보가 부족해요.")
        elif (kcal is not None and kcal >= 300) or (fat is not None and fat >= 20):
            if risk_level == "low":
                risk_level = "medium"
            reasons.append(
                f"체중 관리 기준에서 열량 {kcal if kcal is not None else '미확인'}, "
                f"지방 {fat if fat is not None else '미확인'}로 부담이 있을 수 있어요."
            )

    if not reasons:
        if has_allergy_label_data(product) and has_ingredient_data(product):
            reasons.append("현재 확인된 원재료명과 알레르기 표시 기준에서는 직접 충돌하는 요소가 보이지 않았어요.")
        elif has_ingredient_data(product):
            reasons.append("현재 확인된 원재료명 기준에서는 직접 충돌하는 요소가 보이지 않았어요.")
        else:
            limitations.append("원재료명과 알레르기 표시 정보가 모두 제한적이에요.")

    if limitations and risk_level == "low" and not has_allergy_label_data(product):
        risk_level = "unknown"

    if not next_steps:
        next_steps.append("민감한 알레르기나 질환이 있다면 섭취 전 원재료명과 영양성분표를 함께 확인해 주세요.")

    product_name = product.product_name or "이 제품"
    conclusion_map = {
        "low": f"{prefix} {product_name}은 크게 걱정할 만한 부분은 두드러지지 않아요.",
        "medium": f"{prefix} {product_name}은 먹을 수는 있지만 몇 가지만 조심해서 보면 좋아요.",
        "high": f"{prefix} {product_name}은 피하는 편이 더 안전해 보여요.",
        "unknown": f"{prefix} {product_name}은 아직은 확실하게 판단하기 어려워요.",
    }

    if any(keyword in message for keyword in DETAIL_REQUEST_KEYWORDS):
        caution_points = reasons if reasons else ["현재 제품 정보에서 뚜렷하게 충돌하는 성분이나 수치는 확인되지 않았어요."]
        detail_lines = []
        if product.resolved_allergies():
            detail_lines.append(f"알레르기 표시: {', '.join(product.resolved_allergies())}")
        for key in ["sodium", "sugars", "carbohydrate", "kcal", "fat", "protein"]:
            if key in product.nutrition and product.nutrition[key] is not None:
                detail_lines.append(f"{NUTRITION_LABELS.get(key, key)} {product.nutrition[key]}")

        answer = format_structured_answer(
            f"{product.product_name or '이 제품'}에서 조심해서 볼 포인트를 나눠서 정리해드릴게요.",
            caution_points,
            detail_lines[:6] + limitations,
            next_steps,
        )
        return build_response(
            intent="can_i_eat",
            answer=answer,
            risk_level=risk_level,
            reasons=reasons if reasons else ["세부 주의 포인트 정리"],
            profile=profile,
            recommended_questions=[
                "당류나 나트륨 같은 건강 기준으로도 설명해줘",
                "알레르기 성분만 따로 정리해줘",
                "비슷한 제품이랑 비교해서 설명해줘",
            ],
        )

    return build_response(
        intent="can_i_eat",
        answer=format_can_i_eat_answer(conclusion_map[risk_level], reasons, limitations, next_steps),
        risk_level=risk_level,
        reasons=reasons if reasons else ["판단 근거 제한적"],
        profile=profile,
        recommended_questions=pick_recommended_questions("can_i_eat", risk_level, product),
    )


def handle_can_i_eat(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    return build_can_i_eat_result(profile, product, message)


def handle_profile_based_recheck(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    profile_name = profile.profile_name if profile else "현재 선택된"
    return build_response(
        intent="profile_based_recheck",
        answer=format_structured_answer(
            f"현재는 '{profile_name}' 프로필 기준으로만 다시 해석할 수 있어요.",
            ["질문에서 다른 대상 기준 재확인이 요청됐어요."],
            ["다른 대상의 알레르기나 식이 정보가 현재 요청에 포함되어 있지 않아요."],
            ["해당 대상 프로필로 전환하거나 그 기준 정보를 함께 보내주면 다시 판단할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["다른 프로필 기준 재확인 요청"],
        profile=profile,
        recommended_questions=[
            "보고 싶은 프로필로 바꿔서 다시 알려줘",
            f"일단 {profile_name} 프로필 기준 결과부터 알려줘",
        ],
    )


def handle_ingredient_check(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    ingredient = detect_ingredient_keyword(message)

    if not ingredient:
        if product.resolved_allergies():
            allergens = product.resolved_allergies()
            excluded = allergy_terms_from_product_name(product) if "다른" in message else []
            visible_allergens = [allergen for allergen in allergens if allergen not in excluded]
            if excluded and not visible_allergens:
                conclusion = (
                    f"{product.product_name or '이 제품'} 기준으로 {', '.join(excluded)} 외에 "
                    "추가로 표시된 알레르기 성분은 확인되지 않아요."
                )
                risk_level = "low"
            elif excluded and visible_allergens:
                conclusion = (
                    f"{product.product_name or '이 제품'} 기준으로 "
                    f"{', '.join(excluded)} 외에도 {', '.join(visible_allergens)} 알레르기 성분이 확인돼요."
                )
                risk_level = "medium"
            else:
                visible_allergens = allergens
                conclusion = (
                    f"{product.product_name or '이 제품'} 기준으로 확인되는 알레르기 유발 성분은 "
                    f"{', '.join(visible_allergens)}예요."
                )
                risk_level = "medium"

            return build_response(
                intent="ingredient_check",
                answer=format_binary_check_answer(
                    conclusion,
                    ["원재료명 또는 알레르기 표시 정보를 기준으로 확인했어요."],
                    ["알레르기 반응 이력이 있는 성분은 양이 많지 않아도 주의하는 편이 좋아요."],
                    ["포장지의 알레르기 표시와 제조공정 안내 문구도 함께 확인해 주세요."],
                ),
                risk_level=risk_level,
                reasons=[f"알레르기 표시: {', '.join(allergens)}"],
                profile=profile,
                recommended_questions=pick_recommended_questions("ingredient_check", "medium", product),
            )

        return build_response(
            intent="ingredient_check",
            answer="궁금한 성분을 한 가지만 더 구체적으로 알려주면 바로 확인해볼게요.",
            risk_level="unknown",
            reasons=["성분 키워드 미확인"],
            profile=profile,
            recommended_questions=[
                "우유, 대두, 땅콩 같은 성분 기준으로 다시 확인해줘",
                "이 제품이 전체적으로 먹어도 괜찮은지도 알려줘",
            ],
        )

    matched_aliases = find_matching_aliases(product, ingredient)

    if matched_aliases:
        alias_preview = ", ".join(matched_aliases[:2])
        return build_response(
            intent="ingredient_check",
            answer=format_binary_check_answer(
                f"이 제품에는 {ingredient} 관련 성분이 포함되어 있어요.",
                [f"원재료명 또는 알레르기 표시에서 {alias_preview} 표기가 확인됐어요."],
                [],
                ["알레르기 반응이 있는 성분이라면 양이 많지 않아도 주의하는 편이 좋아요."],
            ),
            risk_level="medium",
            reasons=[f"{ingredient} 관련 표기 확인", f"매칭 성분: {alias_preview}"],
            profile=profile,
            recommended_questions=pick_recommended_questions("ingredient_check", "medium", product),
        )

    # 제품 정보는 있으나 특정 성분이 확인되지 않은 경우
    if has_meaningful_product_data(product):
        return build_response(
            intent="ingredient_check",
            answer=format_binary_check_answer(
                f"지금 확인되는 정보로는 {ingredient} 관련 성분이 보이지 않아요.",
                [f"원재료명과 알레르기 표시에서 {ingredient} 관련 표기가 확인되지 않았어요."],
                ["미량 혼입 가능성이나 제조공정 정보까지는 확인되지 않을 수 있어요."],
                ["민감한 알레르기가 있다면 제조공정 안내 문구도 함께 확인해 주세요."],
            ),
            risk_level="low",
            reasons=[f"{ingredient} 관련 표기 미확인"],
            profile=profile,
            recommended_questions=pick_recommended_questions("ingredient_check", "low", product),
        )

    # 데이터 자체가 부족한 경우
    return build_response(
        intent="ingredient_check",
        answer=format_binary_check_answer(
            f"현재는 {ingredient} 포함 여부를 판단하기 어려워요.",
            [],
            ["원재료명이나 알레르기 표시 정보가 충분하지 않아요."],
            ["제품 상세 정보나 바코드를 다시 확인해 주세요."],
        ),
        risk_level="unknown",
        reasons=["제품 정보 부족"],
        profile=profile,
        recommended_questions=pick_recommended_questions("ingredient_check", "unknown", product),
    )


def handle_nutrition_explain(profile: Optional[Profile], product: Product, message: str) -> ChatResponse:
    nutrient = detect_nutrition_keyword(message)
    multi_nutrition_request = (
        ("당류" in message and "나트륨" in message)
        or "건강 기준" in message
        or "영양 기준" in message
    )

    if not nutrient or multi_nutrition_request:
        if product.nutrition and ("영양" in message or "전체" in message or "전반" in message or "다른" in message):
            nutrition_lines = [
                f"{label} {product.nutrition[key]}"
                for key, label in NUTRITION_LABELS.items()
                if key in product.nutrition and product.nutrition[key] is not None
            ]
            primary = f"{product.product_name or '이 제품'}의 확인 가능한 영양성분은 {', '.join(nutrition_lines)}예요."
            cautions = []
            if product.nutrition.get("sodium") is not None and product.nutrition["sodium"] >= 400:
                cautions.append("나트륨은 비교적 높은 편이라 저염식 기준에서는 주의해서 보는 게 좋아요.")
            if product.nutrition.get("sugars") is not None and product.nutrition["sugars"] >= 12:
                cautions.append("당류도 섭취 빈도에 따라 함께 확인하는 편이 좋아요.")
            if not cautions:
                cautions.append("다만 1회 제공량 기준인지 총 내용량 기준인지에 따라 체감은 달라질 수 있어요.")
            return build_response(
                intent="nutrition_explain",
                answer=format_nutrition_answer(
                    primary,
                    "특정 항목이 궁금하면 나트륨, 당류, 단백질처럼 항목명을 붙여서 더 자세히 볼 수 있어요.",
                    cautions,
                    ["먹어도 괜찮은지 판단하려면 알레르기 성분과 함께 보는 것이 좋아요."],
                ),
                risk_level="medium" if product.nutrition.get("sodium", 0) >= 400 or product.nutrition.get("sugars", 0) >= 12 else "low",
                reasons=nutrition_lines or ["영양성분 요약 제공"],
                profile=profile,
                recommended_questions=pick_recommended_questions("nutrition_explain", "low", product),
            )

        if product.nutrition and multi_nutrition_request:
            points = []
            sodium = product.nutrition.get("sodium")
            sugars = product.nutrition.get("sugars")
            carbs = product.nutrition.get("carbohydrate")
            kcal = product.nutrition.get("kcal")
            fat = product.nutrition.get("fat")

            if sugars is not None:
                points.append(f"당류는 {sugars}로 확인돼요.")
            if carbs is not None:
                points.append(f"탄수화물은 {carbs}로 확인돼요.")
            if sodium is not None:
                points.append(f"나트륨은 {sodium}로 확인돼요.")
            if kcal is not None:
                points.append(f"열량은 {kcal}로 확인돼요.")
            if fat is not None:
                points.append(f"지방은 {fat}로 확인돼요.")

            cautions = []
            if sodium is not None and sodium >= 400:
                cautions.append("저염식 기준에서는 나트륨이 높은 편이라 섭취량을 조절하는 게 좋아요.")
            if sugars is not None and sugars >= 12:
                cautions.append("저당식이나 혈당 관리 기준에서는 당류를 같이 확인하는 게 좋아요.")
            if carbs is not None and carbs >= 30:
                cautions.append("혈당 관리 관점에서는 탄수화물도 함께 보는 편이 좋아요.")
            if not cautions:
                cautions.append("현재 확인된 수치만 보면 크게 튀는 항목은 많지 않지만, 기준에 따라 해석은 달라질 수 있어요.")

            return build_response(
                intent="nutrition_explain",
                answer=format_nutrition_answer(
                    f"{product.product_name or '이 제품'}을 건강 기준으로 보면 당류와 나트륨을 우선 같이 보면 좋아요.",
                    " ".join(points),
                    cautions,
                    ["알레르기나 저염식 같은 개인 기준과 함께 보면 더 정확해요."],
                ),
                risk_level="medium" if (sodium is not None and sodium >= 400) or (sugars is not None and sugars >= 12) else "low",
                reasons=points or ["건강 기준 영양성분 설명"],
                profile=profile,
                recommended_questions=pick_recommended_questions("nutrition_explain", "medium", product),
            )

        return build_response(
            intent="nutrition_explain",
            answer="궁금한 영양성분을 알려주면 그 항목부터 바로 설명해드릴게요.",
            risk_level="unknown",
            reasons=["영양성분 키워드 미확인"],
            profile=profile,
            recommended_questions=[
                "당류, 나트륨, 칼로리 같은 항목 기준으로 다시 설명해줘",
                "이 제품이 먹어도 괜찮은지도 알려줘",
            ],
        )

    nutrition_key_map = {
        "당류": "sugars",
        "탄수화물": "carbohydrate",
        "단백질": "protein",
        "지방": "fat",
        "포화지방": "saturated_fat",
        "나트륨": "sodium",
        "칼로리": "kcal",
    }

    key = nutrition_key_map.get(nutrient)
    value = product.nutrition.get(key)

    if value is None:
        return build_response(
            intent="nutrition_explain",
            answer=format_nutrition_answer(
                f"현재는 이 제품의 {nutrient} 정보를 확인하지 못했어요.",
                f"DB에 {nutrient} 수치가 없어 높고 낮음을 판단하기 어려워요.",
                [],
                ["영양성분표 사진이나 제품 상세 정보가 있으면 다시 확인해드릴게요."],
            ),
            risk_level="unknown",
            reasons=[f"{nutrient} 정보 없음"],
            profile=profile,
            recommended_questions=pick_recommended_questions("nutrition_explain", "unknown", product),
        )

    if nutrient == "당류":
        if value >= 15:
            answer = "당류가 높은 편이라 간식처럼 자주 먹기에는 주의가 필요해요."
            risk = "medium"
        else:
            answer = "당류가 특별히 높은 편으로 보이진 않아요."
            risk = "low"

    elif nutrient == "나트륨":
        if value >= 500:
            answer = "나트륨이 높은 편이라 자주 먹는 음식으로는 주의가 필요해요."
            risk = "medium"
        else:
            answer = "나트륨이 특별히 높은 편으로 보이진 않아요."
            risk = "low"

    elif nutrient == "칼로리":
        if value >= 300:
            answer = "열량이 낮은 편은 아니어서 한 번에 많이 먹는 것은 주의가 필요할 수 있어요."
            risk = "medium"
        else:
            answer = "열량이 아주 부담스러운 수준으로 보이진 않아요."
            risk = "low"

    elif nutrient == "단백질":
        if value >= 10:
            answer = "단백질은 어느 정도 들어 있는 편이에요. 다만 다른 영양성분과 같이 봐야 더 균형 있게 판단할 수 있어요."
            risk = "low"
        else:
            answer = "단백질이 아주 많은 편은 아니에요."
            risk = "low"

    elif nutrient == "탄수화물":
        if value >= 30:
            answer = "탄수화물이 높은 편이라 한 번에 많이 먹는 것은 주의가 필요해요."
            risk = "medium"
        else:
            answer = "탄수화물이 특별히 높은 편으로 보이진 않아요."
            risk = "low"

    elif nutrient == "지방":
        if value >= 20:
            answer = "지방이 높은 편이라 섭취량을 조절하는 것이 좋아요."
            risk = "medium"
        else:
            answer = "지방이 특별히 높은 편으로 보이진 않아요."
            risk = "low"

    elif nutrient == "포화지방":
        if value >= 5:
            answer = "포화지방이 적지 않은 편이라 자주 먹는 용도로는 주의가 필요해요."
            risk = "medium"
        else:
            answer = "포화지방이 특별히 높은 편으로 보이진 않아요."
            risk = "low"

    else:
        answer = f"{nutrient} 수치는 {value}로 확인돼요."
        risk = "low"

    return build_response(
        intent="nutrition_explain",
        answer=format_nutrition_answer(
            f"{product.product_name or '이 제품'} 기준으로 보면 {answer}",
            f"현재 확인된 {nutrient} 수치는 {value}예요.",
            ["1회 제공량 기준인지 총 내용량 기준인지에 따라 체감이 달라질 수 있어요."],
            ["다른 영양성분과 함께 비교해 보면 더 정확하게 해석할 수 있어요."],
        ),
        risk_level=risk,
        reasons=[f"{nutrient} 수치: {value}"],
        profile=profile,
        recommended_questions=pick_recommended_questions("nutrition_explain", risk, product),
    )
