from typing import Optional

from app.core.models import Profile, Product, ChatResponse
from app.core.nutrition_basis import nutrition_basis_label
from app.core.product_provenance import (
    label_provenance_limitation_reason,
    label_provenance_supports,
)
from app.intent.rules import is_medical_scope_question
from app.intent.text import detect_ingredient_keyword, detect_nutrition_keyword, detect_nutrition_keywords
from app.services.recommended_questions import pick_recommended_questions
from app.services.general_handlers import (
    handle_alternative_food_recommend,
    handle_food_compare,
    handle_general_allergy_guide,
    handle_general_diet_guide,
    handle_general_nutrition_guide,
    handle_out_of_domain,
    handle_unknown,
)


NUTRITION_LABELS = {
    "sodium": "나트륨",
    "sugars": "당류",
    "carbohydrate": "탄수화물",
    "kcal": "열량",
    "fat": "지방",
    "saturated_fat": "포화지방",
    "protein": "단백질",
    "cholesterol": "콜레스테롤",
}
NUTRITION_UNITS = {
    "sodium": "mg",
    "sugars": "g",
    "carbohydrate": "g",
    "kcal": "kcal",
    "fat": "g",
    "saturated_fat": "g",
    "protein": "g",
    "cholesterol": "mg",
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

_WHOLE_PACKAGE_REQUEST_KEYWORDS = (
    "한 봉지",
    "한봉지",
    "한 팩",
    "한팩",
    "한 통",
    "한통",
    "한 상자",
    "한상자",
    "한 개 다",
    "전부",
    "전체",
    "다 먹",
    "다 마시",
)
from app.services.product_status_handlers import (
    build_unverified_product_response,
    handle_missing_product,
    handle_product_summary,
)
from app.services.response_helpers import (
    allergy_terms_from_product_name,
    build_response,
    evaluate_special_diets_with_policy,
    find_cross_contamination_allergies,
    find_cross_contamination_aliases,
    find_matching_aliases,
    find_triggered_allergies,
    format_binary_check_answer,
    format_can_i_eat_answer,
    format_measurement,
    format_nutrition_answer,
    format_structured_answer,
    get_profile_prefix,
    has_allergy_label_data,
    has_ingredient_data,
    has_meaningful_product_data,
    assess_product_data_confidence,
    object_particle,
    scan_disliked_ingredients,
    topic_particle,
    unsupported_profile_allergies,
    unsupported_profile_diets,
)


__all__ = [
    "build_can_i_eat_result",
    "handle_alternative_food_recommend",
    "handle_can_i_eat",
    "handle_food_compare",
    "handle_general_allergy_guide",
    "handle_general_diet_guide",
    "handle_general_nutrition_guide",
    "handle_ingredient_check",
    "handle_missing_product",
    "handle_nutrition_explain",
    "handle_out_of_domain",
    "handle_product_summary",
    "handle_profile_based_recheck",
    "handle_unknown",
]


def build_can_i_eat_result(
    profile: Optional[Profile],
    product: Product,
    message: str,
) -> ChatResponse:
    """Compare one verified food label with allergy, diet, and preference data."""

    if is_medical_scope_question(message):
        return handle_out_of_domain(profile, product, message)

    if not product.facts_verified:
        return build_unverified_product_response("can_i_eat", profile)
    if not has_meaningful_product_data(product):
        return build_response(
            intent="can_i_eat",
            answer=(
                "제품명은 찾았지만 원재료·알레르기 표시를 확인할 정보가 부족해요. "
                "바코드나 품목보고번호로 제품을 다시 확인해 주세요."
            ),
            risk_level="unknown",
            assessment_status="incomplete",
            reasons=["제품 표시 정보 부족"],
            profile=profile,
            recommended_questions=["이 제품의 원재료 정보를 확인해줘"],
        )
    reasons: list[str] = []
    limitations: list[str] = []
    next_steps: list[str] = []
    risk_level = "low"
    incomplete = False
    unsupported = False
    confidence, confidence_reasons = assess_product_data_confidence(product)

    composition_supported = label_provenance_supports(
        product,
        ("ingredients", "allergens"),
    )
    if not composition_supported:
        incomplete = True
        limitations.append(
            label_provenance_limitation_reason(
                product,
                ("ingredients", "allergens"),
            )
        )

    if profile is None:
        incomplete = True
        limitations.append(
            "선택된 프로필이 없어 개인 알레르기·식이·비선호 기준은 적용하지 못했어요."
        )
    elif not profile.allergy_context_provided:
        incomplete = True
        limitations.append("프로필의 알레르기 입력 여부를 확인할 수 없어요.")

    unsupported_allergies = unsupported_profile_allergies(profile)
    unsupported_diets = unsupported_profile_diets(profile)
    if unsupported_allergies:
        unsupported = True
        incomplete = True
        limitations.append(
            f"현재 해석하지 못하는 알레르기({', '.join(unsupported_allergies)})가 있어요."
        )
    if unsupported_diets:
        unsupported = True
        incomplete = True

    direct = find_triggered_allergies(profile, product) if profile else []
    direct_names = {allergy for allergy, _aliases in direct}
    cross_contact = [
        (allergy, aliases)
        for allergy, aliases in (
            find_cross_contamination_allergies(profile, product)
            if profile
            else []
        )
        if allergy not in direct_names
    ]
    if direct:
        risk_level = "high"
        for allergy, aliases in direct:
            reasons.append(
                f"{profile.profile_name}: 등록 알레르기 '{allergy}'와 "
                f"직접 관련된 표시({', '.join(aliases[:3])})가 확인됐어요."
            )
        next_steps.append(
            "직접 알레르기 충돌이 있으므로 이 제품은 피하고, 아래 대체 제품의 최신 포장지도 확인해 주세요."
        )
    elif cross_contact:
        risk_level = "medium"
        for allergy, aliases in cross_contact:
            reasons.append(
                f"{profile.profile_name}: '{allergy}' 교차접촉 가능 표시"
                f"({', '.join(aliases[:3])})가 확인됐어요."
            )
        next_steps.append(
            "교차접촉 문구와 본인의 민감도를 확인한 뒤 섭취 여부를 결정해 주세요."
        )

    diet_conflicts, diet_cautions, diet_limitations, _ = (
        evaluate_special_diets_with_policy(profile, product, message)
    )
    if diet_conflicts or diet_cautions:
        if risk_level == "low":
            risk_level = "medium"
        reasons.extend(diet_conflicts)
        reasons.extend(diet_cautions)
    if diet_limitations:
        incomplete = True
        limitations.extend(diet_limitations)

    disliked = scan_disliked_ingredients(profile, product)
    if disliked:
        reasons.append(
            f"비선호 성분({', '.join(disliked[:3])})이 확인됐어요. "
            "이는 알레르기 충돌과는 별도예요."
        )

    has_composition = has_ingredient_data(product) or has_allergy_label_data(product)
    if not has_composition:
        incomplete = True
        limitations.append("원재료·알레르기 표시 정보가 부족해요.")
    elif not reasons and composition_supported:
        reasons.append(
            "현재 DB 원재료·알레르기 표시와 선택된 프로필 사이에 "
            "알려진 직접 충돌은 찾지 못했어요."
        )

    if risk_level == "low" and incomplete:
        risk_level = "unknown"

    if risk_level == "high":
        product_name = product.product_name or "이 제품"
        conclusion = (
            f"{product_name}{topic_particle(product_name)} 선택된 프로필의 "
            "알레르기와 직접 충돌하므로 피하는 것이 좋아요."
        )
    elif risk_level == "medium":
        product_name = product.product_name or "이 제품"
        conclusion = (
            f"{product_name}{topic_particle(product_name)} 확인이 필요한 항목이 있어 "
            "바로 적합하다고 판단하지 않았어요."
        )
    elif risk_level == "low":
        conclusion = (
            f"{product.product_name or '이 제품'}에서 현재 등록 정보와 직접 충돌하는 "
            "알레르기는 찾지 못했어요. 이것은 절대적인 안전 보장이 아니에요."
        )
    else:
        product_name = product.product_name or "이 제품"
        conclusion = (
            f"{product_name}{topic_particle(product_name)} 정보가 부족해 "
            "섭취 가능 여부를 확정하지 않았어요."
        )

    if not next_steps:
        next_steps.append(
            "섭취 전 실제 포장지의 최신 원재료·알레르기 표시를 다시 확인해 주세요."
        )
    assessment_status = (
        "unsupported" if unsupported else "incomplete" if incomplete else "complete"
    )
    return build_response(
        intent="can_i_eat",
        answer=format_can_i_eat_answer(
            conclusion,
            list(dict.fromkeys(reasons)),
            list(dict.fromkeys(limitations)),
            next_steps,
        ),
        risk_level=risk_level,
        reasons=list(dict.fromkeys(reasons)) or ["판단 근거가 부족해요."],
        profile=profile,
        confidence=confidence,
        confidence_reasons=confidence_reasons,
        assessment_status=assessment_status,
        recommended_questions=pick_recommended_questions(
            "can_i_eat",
            risk_level,
            product,
        ),
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
    if not product.facts_verified:
        return build_unverified_product_response("ingredient_check", profile)

    ingredient = detect_ingredient_keyword(message)
    composition_lineage_supported = label_provenance_supports(
        product,
        ("ingredients", "allergens"),
    )

    if not ingredient:
        if product.resolved_allergies():
            allergens = product.resolved_allergies()
            excluded = allergy_terms_from_product_name(product) if "다른" in message else []
            visible_allergens = [allergen for allergen in allergens if allergen not in excluded]
            if excluded and not visible_allergens and composition_lineage_supported:
                conclusion = (
                    f"{product.product_name or '이 제품'} 기준으로 {', '.join(excluded)} 외에 "
                    "추가로 표시된 알레르기 성분은 확인되지 않아요."
                )
                risk_level = "low"
            elif excluded and not visible_allergens:
                conclusion = (
                    f"{product.product_name or '이 제품'}에 {', '.join(excluded)} 외의 "
                    "추가 알레르기 표시가 없는지는 아직 판정하기 어려워요."
                )
                risk_level = "unknown"
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

    cross_contamination_aliases = find_cross_contamination_aliases(product, ingredient)
    if cross_contamination_aliases:
        alias_preview = ", ".join(cross_contamination_aliases[:2])
        direct_inclusion_text = (
            f"{ingredient} 성분의 직접 함유는 확인되지 않지만"
            if composition_lineage_supported
            else f"{ingredient} 성분의 직접 함유 여부는 확정할 수 없고"
        )
        return build_response(
            intent="ingredient_check",
            answer=format_binary_check_answer(
                f"{direct_inclusion_text} 교차오염 가능성 표시가 있어요.",
                [f"제조공정 안내에서 {alias_preview} 관련 표기가 확인됐어요."],
                ["알레르기 민감도와 반응 이력 없이 섭취 가능을 단정할 수 없어요."],
                ["중증 반응 이력이 있다면 섭취를 피하고 제품 표시를 확인해 주세요."],
            ),
            risk_level="medium",
            reasons=[f"{ingredient} 관련 교차오염 표시: {alias_preview}"],
            profile=profile,
            assessment_status="incomplete",
            recommended_questions=pick_recommended_questions("ingredient_check", "medium", product),
        )

    # 성분 부재는 검증된 원재료명/알레르기 표시가 있을 때만 판정한다.
    has_composition_evidence = has_ingredient_data(product) or has_allergy_label_data(product)
    if (
        has_composition_evidence
        and product.facts_verified
        and composition_lineage_supported
    ):
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

    # 데이터 자체가 부족하거나 DB에서 검증되지 않은 경우
    return build_response(
        intent="ingredient_check",
        answer=format_binary_check_answer(
            f"현재는 {ingredient} 포함 여부를 판단하기 어려워요.",
            [],
            [
                (
                    "원재료·알레르기 표시 정보가 부족해요."
                    if not has_composition_evidence
                    else label_provenance_limitation_reason(
                        product,
                        ("ingredients", "allergens"),
                    )
                )
            ],
            ["제품 상세 정보나 바코드를 다시 확인해 주세요."],
        ),
        risk_level="unknown",
        reasons=["제품 성분 근거 부족"],
        profile=profile,
        recommended_questions=pick_recommended_questions("ingredient_check", "unknown", product),
    )



def handle_nutrition_explain(
    profile: Optional[Profile],
    product: Product,
    message: str,
) -> ChatResponse:
    """Explain verified label values without inventing health cutoffs."""

    if not product.facts_verified:
        return build_unverified_product_response("nutrition_explain", profile)

    nutrients = detect_nutrition_keywords(message)
    nutrient = nutrients[0] if nutrients else None
    nutrition_key_map = {
        "당류": "sugars",
        "탄수화물": "carbohydrate",
        "단백질": "protein",
        "지방": "fat",
        "포화지방": "saturated_fat",
        "나트륨": "sodium",
        "콜레스테롤": "cholesterol",
        "칼로리": "kcal",
    }
    key = nutrition_key_map.get(nutrient)
    product_name = product.product_name or "이 제품"
    basis_label = nutrition_basis_label(product)
    from app.intent.request_semantics import package_amount
    requested_amount = package_amount(message)
    package_count, package_unit = requested_amount or (1, "봉지")
    amount_label = "포장 전체" if package_count == 1 else f"{package_count:g}{package_unit} 분량"

    if len(nutrients) > 1 and product.nutrition:
        # Reuse the single-nutrient calculations, including whole-package
        # limitations, instead of silently choosing one requested value.
        portion = f" {package_count:g}{package_unit}" if requested_amount else (
            " 한 봉지 전체" if any(term in message for term in _WHOLE_PACKAGE_REQUEST_KEYWORDS) else " 얼마야?")
        results = [handle_nutrition_explain(profile, product, name + portion) for name in nutrients]
        lines = [item.answer.splitlines()[0] for item in results]
        return build_response(
            intent="nutrition_explain", answer="\n".join([*lines, f"표시 기준: {basis_label}. 실제 섭취량과 포장지를 함께 확인해 주세요."]),
            risk_level="unknown", assessment_status="incomplete", profile=profile,
            reasons=list(dict.fromkeys(reason for item in results for reason in item.reasons)),
            recommended_questions=pick_recommended_questions("nutrition_explain", "unknown", product),
        )

    if not product.nutrition:
        return build_response(
            intent="nutrition_explain",
            answer=format_nutrition_answer(
                f"현재는 {product_name}의 영양성분 정보를 확인하지 못했어요.",
                "DB에 확인 가능한 영양 수치가 없어요. 정보가 없다는 것은 함량이 0이라는 뜻이 아니에요.",
                [],
                ["제품의 영양성분표를 확인하거나 다른 제품을 선택해 주세요."],
            ),
            risk_level="unknown",
            reasons=["영양성분 정보 없음"],
            profile=profile,
            assessment_status="incomplete",
            recommended_questions=pick_recommended_questions(
                "nutrition_explain", "unknown", product
            ),
        )

    whole_package_requested = requested_amount is not None or any(
        phrase in message for phrase in _WHOLE_PACKAGE_REQUEST_KEYWORDS
    )
    if whole_package_requested and key and product.nutrition_basis.basis_type != "per_serving":
        basis = product.nutrition_basis
        value = product.nutrition.get(key)
        factor = None
        if basis.basis_type == "per_package":
            factor = 1
        elif basis.package_size_value is not None:
            units = {"g": 1, "kg": 1000} if basis.basis_type == "per_100g" else {"ml": 1, "l": 1000} if basis.basis_type == "per_100ml" else {}
            multiplier = units.get(str(basis.package_size_unit or "").lower())
            if multiplier is not None:
                factor = basis.package_size_value * multiplier / 100
        if factor is not None and value is not None:
            factor *= package_count
            total_text = f"{float(value) * factor:.6f}".rstrip("0").rstrip(".") + NUTRITION_UNITS.get(key, "")
            answer = (f"{product_name}의 {amount_label} {nutrient}{topic_particle(nutrient)} 약 {total_text}예요.\n"
                      f"계산 근거: {basis_label}의 수치 × {factor:g}. 실제 포장 용량과 섭취량을 확인해 주세요.")
        else:
            answer = (f"{product_name}의 포장 전체 {nutrient}은 계산하지 못했어요.\n"
                      f"현재 표시 기준은 {basis_label}이에요. 해당 영양 수치와 같은 단위의 포장 총용량을 확인해 주세요.")
        return build_response(intent="nutrition_explain", answer=answer, profile=profile,
                              risk_level="unknown", assessment_status="incomplete",
                              reasons=["포장 전체 계산" if factor is not None and value is not None else "패키지 전체 계산 근거 부족"])
    if (
        whole_package_requested
        and key
        and product.nutrition_basis.basis_type == "per_serving"
    ):
        value = product.nutrition.get(key)
        servings = product.nutrition_basis.servings_per_package
        if value is not None and servings is not None:
            total = float(value) * float(servings) * package_count
            formatted_servings = (
                str(int(servings))
                if float(servings).is_integer()
                else f"{float(servings):g}"
            )
            unit = NUTRITION_UNITS.get(key, "")
            return build_response(
                intent="nutrition_explain",
                answer=format_nutrition_answer(
                    f"{product_name}{object_particle(product_name)} {amount_label} 먹으면 "
                    f"{nutrient}{topic_particle(nutrient)} 약 {format_measurement(total, unit)}예요.",
                    f"계산 근거: 1회 제공량 {format_measurement(value, unit)} × {formatted_servings}회 제공량({basis_label}) × {package_count:g}포장.",
                    [
                        "표시된 제공 횟수를 단순 합산한 값이며, 이 수치만으로 건강 적합성을 판정하지 않아요."
                    ],
                    ["실제 먹는 양과 제품에 표시된 식이 특성을 함께 확인해 주세요."],
                ),
                risk_level="unknown",
                reasons=[
                    f"패키지 전체 {nutrient}: {format_measurement(total, unit)}",
                    f"1회 제공량 {formatted_servings}회 합산",
                ],
                profile=profile,
                assessment_status="incomplete",
                recommended_questions=pick_recommended_questions(
                    "nutrition_explain", "unknown", product
                ),
            )
        return build_response(
            intent="nutrition_explain",
            answer=format_nutrition_answer(
                f"{product_name}의 한 봉지 전체 {nutrient or '영양성분'}은 계산하지 못했어요.",
                f"현재 영양 수치 기준은 {basis_label}이에요.",
                ["총 제공 횟수 또는 해당 영양 수치가 확인되지 않았어요."],
                ["포장지의 총 제공 횟수를 확인해 주세요."],
            ),
            risk_level="unknown",
            reasons=["패키지 전체 계산 근거 부족"],
            profile=profile,
            assessment_status="incomplete",
            recommended_questions=pick_recommended_questions(
                "nutrition_explain", "unknown", product
            ),
        )

    if key:
        value = product.nutrition.get(key)
        if value is None:
            return build_response(
                intent="nutrition_explain",
                answer=format_nutrition_answer(
                    f"현재는 {product_name}의 {nutrient} 정보를 확인하지 못했어요.",
                    f"DB에 {nutrient} 수치가 없고, 확인된 기준은 {basis_label}이에요. 정보가 없다는 것은 함량이 0이라는 뜻이 아니에요.",
                    [],
                    ["제품의 영양성분표를 확인해 주세요."],
                ),
                risk_level="unknown",
                reasons=[f"{nutrient} 정보 없음", basis_label],
                profile=profile,
                assessment_status="incomplete",
                recommended_questions=pick_recommended_questions(
                    "nutrition_explain", "unknown", product
                ),
            )

        unit = NUTRITION_UNITS.get(key, "")
        asks_for_interpretation = any(
            phrase in message
            for phrase in (
                "높아", "낮아", "많아", "적어", "괜찮", "어때",
                "저염", "저당", "저칼로리", "저지방", "고단백",
            )
        )
        limitation = (
            "서비스 자체 임계치로 높음·낮음이나 건강 적합성을 판정하지 않아요. "
            "제품의 명시 표시를 확인하거나 같은 기준량의 다른 제품과 비교해 주세요."
            if asks_for_interpretation
            else "표시 수치는 실제 섭취량에 따라 합산해서 봐야 해요."
        )
        return build_response(
            intent="nutrition_explain",
            answer=format_nutrition_answer(
                f"{product_name}의 {nutrient}{topic_particle(nutrient)} {format_measurement(value, unit)}예요.",
                f"표시 기준은 {basis_label}이에요.",
                [limitation],
                ["원하면 같은 기준량으로 확인된 다른 제품과 수치를 비교해 드릴게요."],
            ),
            risk_level="unknown",
            reasons=[
                f"{nutrient}: {format_measurement(value, unit)}",
                basis_label,
            ],
            profile=profile,
            assessment_status="incomplete",
            recommended_questions=pick_recommended_questions(
                "nutrition_explain", "unknown", product
            ),
        )

    nutrition_lines = [
        f"{label} {format_measurement(product.nutrition[nutrition_key], NUTRITION_UNITS.get(nutrition_key, ''))}"
        for nutrition_key, label in NUTRITION_LABELS.items()
        if product.nutrition.get(nutrition_key) is not None
    ]
    return build_response(
        intent="nutrition_explain",
        answer=format_nutrition_answer(
            f"{product_name}의 {basis_label} 영양성분을 정리했어요.",
            ", ".join(nutrition_lines) + ".",
            [
                "이 수치만으로 높음·낮음이나 식이 적합성을 단정하지 않아요."
            ],
            [
                "저염·저당 같은 조건은 제품의 명시 표시를 확인하거나 "
                "같은 기준량의 제품끼리 비교해 주세요."
            ],
        ),
        risk_level="unknown",
        reasons=[*nutrition_lines, basis_label],
        profile=profile,
        assessment_status="incomplete",
        recommended_questions=pick_recommended_questions(
            "nutrition_explain", "unknown", product
        ),
    )
