import re
from typing import Optional, Sequence

from app.core.models import ChatResponse, Product, Profile
from app.core.nutrition_basis import (
    display_basis_label,
    has_explicit_nutrition_basis,
    nutrition_basis_label,
    products_have_comparable_nutrition_basis,
)
from app.core.product_provenance import (
    label_provenance_limitation_reason,
    label_provenance_supports,
)
from app.data.dictionaries import NUTRITION_KEYWORDS
from app.services.product import find_product_candidates_from_message
from app.services.product_status_handlers import build_unverified_product_response
from app.services.recommended_questions import pick_recommended_questions
from app.services.response_helpers import (
    build_response,
    evaluate_special_diets,
    find_cross_contamination_allergies,
    find_triggered_allergies,
    format_general_answer,
    format_measurement,
    resolve_profile_diets,
    scan_disliked_ingredients,
    topic_particle,
)
from app.services.result_semantics import _diet_match, _allergy_status
from app.core.public_product import public_product_payload


PRODUCT_COMPARE_AXES = {
    "칼로리": ("kcal", "열량", "kcal", "lower"),
    "단백질": ("protein", "단백질", "g", "higher"),
    "지방": ("fat", "지방", "g", "lower"),
    "포화지방": ("saturated_fat", "포화지방", "g", "lower"),
    "탄수화물": ("carbohydrate", "탄수화물", "g", "lower"),
    "당류": ("sugars", "당류", "g", "lower"),
    "나트륨": ("sodium", "나트륨", "mg", "lower"),
    "콜레스테롤": ("cholesterol", "콜레스테롤", "mg", "lower"),
}


def _comparison_axis_match(message: str) -> tuple[Optional[str], Optional[str]]:
    aliases = {alias: nutrient for nutrient, values in NUTRITION_KEYWORDS.items()
               if nutrient in PRODUCT_COMPARE_AXES for alias in values}
    # Longest non-overlapping terms keep 포화지방 separate from 지방.
    pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    matches = list(re.finditer(pattern, message))
    if not matches:
        return None, None
    alias = matches[-1][0]
    return aliases[alias], alias


def _comparison_axis(message: str) -> Optional[tuple[str, str, str, str]]:
    latest_match, _ = _comparison_axis_match(message)
    if latest_match:
        return PRODUCT_COMPARE_AXES.get(latest_match)
    diet_axes = (
        (r"저염식?\s*기준", "나트륨"),
        (r"저당식?\s*기준", "당류"),
        (r"저칼로리\s*기준", "칼로리"),
        (r"저지방\s*기준", "지방"),
        (r"고단백\s*기준", "단백질"),
    )
    return next(
        (PRODUCT_COMPARE_AXES[key] for pattern, key in diet_axes if re.search(pattern, message)),
        None,
    )


def _comparison_axes(message: str) -> list[tuple[str, str, str, str]]:
    from app.services.comparison_language import comparison_axis_names
    names = comparison_axis_names(message)
    axes = [_comparison_axis(name) for name in names] if names else [_comparison_axis(message)]
    return list(dict.fromkeys(axis for axis in axes if axis is not None))


def _product_identity(product: Product) -> tuple:
    return (
        product.barcode,
        product.report_number,
        product.product_name,
    )


def _message_product_segments(message: str) -> list[str]:
    message = re.sub(r"^(?:(?:아까|이전|방금)\s*)?(?:두\s*(?:개|제품)|그\s*둘)(?:은|는|을|를)?\s*말고\s*", "", message)
    from app.services.comparison_language import comparison_axis_suffix
    suffix = comparison_axis_suffix(message)
    if suffix:
        message = re.sub(r"의$", "", message[:suffix.start()].strip())
    _, axis_alias = _comparison_axis_match(message)
    comparison_diet = (
        "저염식", "저당식", "채식", "글루텐프리",
        "저칼로리", "저지방", "고단백",
    )
    cleaned = re.sub(
        rf"\s+(?:{'|'.join(comparison_diet)})\s*기준으로\s*",
        " ",
        message,
    )
    cleaned = re.sub(
        r"(비교해\s*(?:줄래(?:요)?|주세요|주실래요|줄\s*수\s*있어(?:요)?|봐(?:\s*줘)?)|"
        r"해\s*(?:줄래(?:요)?|주세요|주실래요)|"
        r"(?:뭐가|어느\s*게|어떤\s*게|어느\s*쪽이?)\s*(?:더|덜)\s*\S+|(?:뭐가|어느\s*게|어떤\s*게|어느\s*쪽이?)\s*(?:나아|나을까|낫|좋아|괜찮)\S*|"
        r"비교해\s*줘|비교해줘|비교|해\s*줘|알려\s*줘|중에|중|뭐야|어느\s*게|어떤\s*게|"
        r"더\s*낮은|더\s*높은|더\s*많은|더\s*적은|더\s*낮아|더\s*높아|"
        r"더\s*많아|더\s*적어|(?:^|\s)(?:건|거|것)(?:이야|야)?(?=\s|$))",
        " ",
        cleaned,
    )
    if axis_alias:
        cleaned = re.sub(
            rf"\s*{re.escape(axis_alias)}(?:이|가|은|는|을|를|도)?\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    segments = [
        # A request ending such as '비교해 줄래요?' leaves punctuation behind.
        re.sub(r"[\s?？!.~]+$", "", segment.strip())
        for segment in re.split(r"\s*(?:이랑|랑|와|과|vs|VS|,|/)\s*", cleaned)
        if segment.strip()
    ]
    return [segment for segment in segments if len(segment) >= 2]


def _find_products_to_compare(message: str, product: Optional[Product]) -> list[Product]:
    products = []
    seen = set()

    if product is not None:
        products.append(product)
        seen.add(_product_identity(product))

    for segment in _message_product_segments(message):
        # Add a local command boundary so only this product segment reaches the DB.
        for candidate in find_product_candidates_from_message(f"{segment} 찾아줘", limit=2):
            identity = _product_identity(candidate)
            if identity in seen:
                continue
            if (candidate.match_confidence or 0) < 0.45:
                continue
            products.append(candidate)
            seen.add(identity)
            break

    if len(products) >= 2:
        return products

    return products


def _format_product_comparison_answer(
    message: str,
    product: Optional[Product],
    *,
    products: Optional[Sequence[Product]] = None,
    axis: Optional[tuple[str, str, str, str]] = None,
) -> Optional[tuple[str, list[str]]]:
    products = list(products or _find_products_to_compare(message, product))
    axes = [axis] if axis is not None else _comparison_axes(message)
    if len(axes) > 1:
        comparisons = [_format_product_comparison_answer(message, product, products=products, axis=item) for item in axes]
        if any(item is None for item in comparisons):
            return None
        return ("\n\n".join(item[0] for item in comparisons),
                [reason for item in comparisons for reason in item[1]])
    axis = axes[0] if axes else None
    if len(products) < 2 or axis is None:
        return None

    key, label, unit, default_direction = axis
    comparable = [
        (candidate, candidate.nutrition.get(key))
        for candidate in products
        if candidate.nutrition.get(key) is not None
    ]
    if len(comparable) != len(products):
        available_lines = [
            f"{candidate.product_name or '이 제품'}: "
            f"{format_measurement(value, unit)}({display_basis_label(candidate)})"
            for candidate, value in comparable
        ]
        unavailable_names = [
            candidate.product_name or "제품명 미확인"
            for candidate in products
            if candidate.nutrition.get(key) is None
        ]
        return (
            format_general_answer(
                f"모든 제품의 {label} 수치가 갖춰지지 않아 우승 제품을 고르지 않았어요.",
                [f"확인된 원문 수치는 {', '.join(available_lines)}예요."],
                [f"{', '.join(unavailable_names)}의 {label} 수치가 미확인이에요."],
                ["비교할 모든 제품의 수치와 기준량을 확인한 뒤 다시 비교해 주세요."],
            ),
            [
                *[
                    f"{candidate.product_name or '이 제품'} {label}: "
                    f"{format_measurement(value, unit)} ({nutrition_basis_label(candidate)})"
                    for candidate, value in comparable
                ],
                f"{label} 정보 부족으로 우승 판정 보류",
            ],
        )

    comparable_products = [candidate for candidate, _ in comparable]
    if not products_have_comparable_nutrition_basis(comparable_products):
        lineage_missing = any(
            not label_provenance_supports(
                candidate,
                ("nutrition", "nutrition_basis"),
            )
            for candidate in comparable_products
        )
        lineage_reason = next((
            label_provenance_limitation_reason(
                candidate,
                ("nutrition", "nutrition_basis"),
            )
            for candidate in comparable_products
            if not label_provenance_supports(
                candidate,
                ("nutrition", "nutrition_basis"),
            )
        ), "")
        value_lines = [
            f"{candidate.product_name or '이 제품'}: "
            f"{format_measurement(value, unit)}({display_basis_label(candidate)})"
            for candidate, value in comparable
        ]
        primary = (
            f"{label} 수치는 있지만 {lineage_reason} 그래서 우승 제품을 "
            "고르지 않았어요."
            if lineage_missing
            else f"{label} 수치는 확인했지만 기준량을 동일하게 맞출 수 없어 우승 제품을 고르지 않았어요."
        )
        limitation = (
            lineage_reason
            if lineage_missing
            else "1회 제공량, 100g/100ml, 총 내용량 기준이 없거나 서로 다르면 수치 차이를 영양성분 차이로 해석하면 안 돼요."
        )
        next_step = (
            "차단 사유를 해결하고 수집 당시 내용 기록과 일치하는 제품 표시 정보로 다시 비교해 주세요."
            if lineage_missing
            else "각 제품의 영양성분 기준량을 확인한 뒤 같은 기준으로 다시 비교해 주세요."
        )
        from app.services.operational_policy import unverified_catalog_enabled
        if lineage_missing and unverified_catalog_enabled():
            # Team mode has no label lineage at all; the record-binding detail
            # is noise to a user, so say what it means for this comparison.
            primary = f"{label} 수치는 있지만 상품 정보의 출처를 확인할 수 없어 어느 쪽이 낫다고 단정하지 않았어요."
            limitation = (
                "괄호 안 기준량이 제품마다 달라 수치 차이를 그대로 영양성분 차이로 볼 수 없어요."
                if all(display_basis_label(candidate) != nutrition_basis_label(candidate) or has_explicit_nutrition_basis(candidate)
                       for candidate in comparable_products)
                else "기준량(1회 제공량·100g·총 내용량)이 확인되지 않은 제품이 있어 수치 차이를 그대로 영양성분 차이로 볼 수 없어요.")
            next_step = "포장지의 영양성분표에서 같은 기준량으로 확인해 주세요."
        answer = format_general_answer(
            primary,
            [f"원문 수치는 {', '.join(value_lines)}로 확인돼요."],
            [limitation],
            [next_step],
        )
        reasons = [
            f"{candidate.product_name or '이 제품'} {label}: "
            f"{format_measurement(value, unit)} ({nutrition_basis_label(candidate)})"
            for candidate, value in comparable
        ]
        reasons.append(
            "영양 수치의 출처 추적 정보 미확인으로 우승 판정 보류"
            if lineage_missing
            else "영양 기준량 비교 불가로 우승 판정 보류"
        )
        return answer, reasons

    wants_lower = any(token in message for token in ["낮", "적", "덜"])
    wants_higher = any(token in message for token in ["높", "많", "더 많은"])
    if wants_lower:
        winner_product, winner_value = min(comparable, key=lambda item: item[1])
    elif wants_higher:
        winner_product, winner_value = max(comparable, key=lambda item: item[1])
    elif default_direction == "lower":
        winner_product, winner_value = min(comparable, key=lambda item: item[1])
    else:
        winner_product, winner_value = max(comparable, key=lambda item: item[1])

    direction_label = (
        "낮게"
        if wants_lower or (not wants_higher and default_direction == "lower")
        else "높게"
    )

    value_lines = [
        f"{candidate.product_name or '이 제품'}: {format_measurement(value, unit)}"
        for candidate, value in comparable
    ]
    winner_name = winner_product.product_name or "이 제품"
    answer = format_general_answer(
        f"DB의 {nutrition_basis_label(comparable[0][0])} 영양성분으로는 "
        f"{winner_name}{topic_particle(winner_name)} {label} 수치가 비교 대상 중 "
        f"{direction_label} 확인돼요.",
        [
            f"{label} 수치는 " + ", ".join(value_lines) + "로 확인돼요.",
            "DB에 등록된 제품 영양성분만 사용해서 비교했어요.",
        ],
        ["같은 기준량으로 확인된 수치만 비교했고, 이 결과는 알레르기 적합성과는 별개예요."],
        ["비교할 제품을 더 추가하거나 다른 영양성분 기준으로 다시 물어볼 수 있어요."],
    )
    reasons = [
        f"{candidate.product_name or '이 제품'} {label}: {format_measurement(value, unit)}"
        for candidate, value in comparable
    ]
    reasons.append(f"비교 결과: {winner_name} ({format_measurement(winner_value, unit)})")
    return answer, reasons


def _allergy_with_aliases(allergy: str, aliases) -> str:
    """'새우' for the alias 새우, '우유(탈지분유)' when the label used another word."""
    others = [alias for alias in aliases if alias != allergy][:3]
    return f"{allergy}({', '.join(others)})" if others else allergy


def _comparison_allergy_status(
    product: Product,
    profile: Profile,
) -> tuple[str, str, list[str]]:
    product_name = product.product_name or "제품명 미확인"
    if _allergy_status(product, [profile]) == "unknown":
        return (
            "unknown",
            f"{product_name}: 알레르기 판정 정보가 부족해요.",
            [f"{product_name} 알레르기 판정 정보 부족"],
        )

    direct = find_triggered_allergies(profile, product)
    if direct:
        findings = [
            _allergy_with_aliases(allergy, aliases)
            for allergy, aliases in direct
        ]
        return (
            "conflict",
            f"{product_name}: {', '.join(findings)} 직접 충돌이 확인돼 피해야 해요.",
            [f"{product_name} 직접 충돌: {finding}" for finding in findings],
        )

    cross_contact = find_cross_contamination_allergies(profile, product)
    if cross_contact:
        findings = [
            _allergy_with_aliases(allergy, aliases)
            for allergy, aliases in cross_contact
        ]
        return (
            "cross_contact",
            f"{product_name}: {', '.join(findings)} 교차접촉 표시가 있어 주의가 필요해요.",
            [f"{product_name} 교차접촉: {finding}" for finding in findings],
        )

    if not (product.rawmtrl_nm or product.allergy or product.allergy_warning):
        return (
            "unknown",
            f"{product_name}: 원재료·알레르기 표시가 부족해 판정을 보류해요.",
            [f"{product_name} 원재료·알레르기 표시 부족"],
        )
    return (
        "no_known_match",
        f"{product_name}: 등록된 표시에서 알레르기와 직접 충돌하는 표시를 찾지 못했어요.",
        [f"{product_name} 현재 표시에서 알레르기 직접 충돌 미확인"],
    )


def _key_nutrient_overview(products: Sequence[Product]) -> str:
    """No axis named: list the main registered values side by side, no winner."""
    rows = []
    # Backend nutrition-v1: when every product has a 100g (or 100ml) portion,
    # compare on that common basis; the backend computed it, nothing is re-scaled.
    contract_keys = {"kcal": "energyKcal", "sodium": "sodiumMg", "sugars": "sugarG"}
    common = None
    for portion_name in ("per100g", "per100ml"):
        portions = [getattr(getattr(item, "nutrition_contract", None), portion_name, None) for item in products]
        if portions and all(portion is not None for portion in portions):
            common = (portion_name, portions)
            break
    for key, label, unit in (("kcal", "열량", "kcal"), ("sodium", "나트륨", "mg"), ("sugars", "당류", "g")):
        if common is not None:
            values = [f"{item.product_name or '이 제품'} {format_measurement(round(portion.values[contract_keys[key]], 1), unit)}"
                      for item, portion in zip(products, common[1]) if portion.values.get(contract_keys[key]) is not None]
        else:
            values = [f"{item.product_name or '이 제품'} {format_measurement(item.nutrition[key], unit)}"
                      + (f"({display_basis_label(item)})" if display_basis_label(item) != "영양 수치 기준 미확인" else "")
                      for item in products if item.nutrition.get(key) is not None]
        if values:
            rows.append(f"- {label}: {', '.join(values)}")
    if not rows:
        return "비교할 영양 수치가 등록되어 있지 않아요. 포장지의 영양성분표를 확인해 주세요."
    if common is not None:
        basis = "100g" if common[0] == "per100g" else "100ml"
        intro = f"주요 영양 수치를 {basis}당 환산값으로 맞춰 봤어요. 환산값이라 포장지 표기와 소수점이 다를 수 있어요.\n"
    else:
        intro = ("주요 영양 수치예요. 괄호 안 기준량이 제품마다 달라 수치 차이를 그대로 비교하면 안 될 수 있어요.\n"
                 if all(display_basis_label(item) != "영양 수치 기준 미확인" for item in products)
                 else "주요 영양 수치예요. 기준량이 확인되지 않아 수치 차이를 그대로 비교하면 안 될 수 있어요.\n")
    # The app does not send the previous turn, so the hint names both products.
    names = [item.product_name for item in products[:2] if item.product_name]
    def with_rang(name):
        hangul = [ch for ch in name if "가" <= ch <= "힣"]
        return name + ("이랑" if hangul and (ord(hangul[-1]) - 0xAC00) % 28 else "랑")
    example = f"{with_rang(names[0])} {names[1]} 나트륨 비교해줘" if len(names) == 2 else "나트륨 기준으로 비교해줘"
    return (intro + "\n".join(rows) + f"\n특정 기준으로 자세히 보려면 '{example}'처럼 제품 이름과 함께 물어봐 주세요.")


def _comparison_allergy_summary(
    products: Sequence[Product],
    profiles: Sequence[Profile],
    message: str,
) -> tuple[str, list[str], list[dict], str, str, str, str, str, list[str]]:
    if not profiles:
        return (
            "선택된 프로필이 없어 개인 알레르기 비교는 하지 못했어요.",
            ["활성 프로필 없음"],
            [],
            "unknown",
            "check_required",
            "unknown",
            "unknown",
            "unknown",
            ["활성 프로필의 알레르기 정보가 필요해요."],
        )

    if not any(profile.resolved_allergies() or profile.resolved_special_diets() or profile.disliked_ingredients
               or profile._conversation_exclusions for profile in profiles):
        # No allergy, diet or exclusion to check against: say so instead of
        # 'label information is missing', and show each product's allergy labels.
        labels = " / ".join(f"{item.product_name or '이 제품'}: {', '.join(item.resolved_allergies()) or '표시 없음'}"
                            for item in products)
        return (
            f"등록된 알레르기가 없어 개인 알레르기 비교는 하지 않았어요.\n제품별 알레르기 표시 — {labels}",
            ["프로필 알레르기 미입력"], [], "unknown", "check_required", "unknown", "unknown", "unknown",
            ["프로필의 알레르기 정보가 필요해요."],
        )

    priority = {"conflict": 3, "cross_contact": 2, "unknown": 1, "no_known_match": 0}
    profile_results = []
    all_reasons: list[str] = []
    all_statuses: list[str] = []
    diet_statuses: list[str] = []
    preference_statuses: list[str] = []
    summary_lines: list[str] = []
    missing_information: list[str] = []
    for active_profile in profiles:
        product_results = [
            _comparison_allergy_status(candidate, active_profile)
            for candidate in products
        ]
        statuses = [status for status, _, _ in product_results]
        status = max(statuses, key=lambda item: priority[item])
        decision = {
            "conflict": "avoid",
            "cross_contact": "caution",
            "unknown": "check_required",
            "no_known_match": "no_known_conflict",
        }[status]
        risk = {
            "conflict": "high",
            "cross_contact": "medium",
            "unknown": "unknown",
            "no_known_match": "low",
        }[status]
        lines = [line for _, line, _ in product_results]
        reasons = [
            reason
            for _, _, product_reasons in product_results
            for reason in product_reasons
        ]
        requested_diets = resolve_profile_diets(active_profile, message)
        has_diet_conditions = bool(requested_diets or active_profile._conversation_exclusions)
        diet_lines = []
        profile_diet_statuses = []
        profile_preference_statuses = []
        for candidate in products:
            candidate_name = candidate.product_name or "제품명 미확인"
            if has_diet_conditions:
                conflicts, cautions, limitations = evaluate_special_diets(
                    active_profile, candidate, message
                )
                if conflicts:
                    candidate_diet_status = "not_match"
                    detail = "; ".join(conflicts)
                elif cautions:
                    candidate_diet_status = "caution"
                    detail = "; ".join(cautions)
                elif limitations:
                    candidate_diet_status = "unknown"
                    detail = "; ".join(limitations)
                else:
                    diet_profile = active_profile.model_copy(update={
                        "special_diets": requested_diets,
                        "special_diet": requested_diets,
                    })
                    candidate_diet_status = _diet_match(candidate, [diet_profile])
                    detail = (
                        (
                            f"제품명·표시에서 {', '.join(requested_diets)} 관련 근거를 찾았어요. "
                            "실제 영양 수치·기준량과 최신 포장지도 확인해 주세요."
                            if any(diet != "채식" for diet in requested_diets)
                            else ("현재 원재료 표시에서 채식 제한 성분을 찾지 못했어요. 최신 포장지도 확인해 주세요."
                                  if requested_diets else "현재 원재료 표시에서 이번 대화의 제외 재료를 찾지 못했어요. 최신 포장지도 확인해 주세요.")
                        )
                        if candidate_diet_status == "match"
                        else "요청 조건의 적합성을 확정할 근거가 부족해요."
                    )
                profile_diet_statuses.append(candidate_diet_status)
                diet_lines.append(f"{candidate_name} 식이 조건: {detail}")
                if candidate_diet_status != "match":
                    reasons.append(f"{candidate_name} 식이 조건: {detail}")
                if candidate_diet_status == "unknown":
                    missing_information.append(
                        f"{candidate_name}의 식이 조건 적합성을 확인할 표시 근거가 부족해요."
                    )
            if active_profile.disliked_ingredients:
                disliked = scan_disliked_ingredients(active_profile, candidate)
                if disliked:
                    profile_preference_statuses.append("disliked_found")
                    diet_lines.append(
                        f"{candidate_name} 비선호 성분: {', '.join(disliked)}"
                    )
                elif candidate.rawmtrl_nm:
                    profile_preference_statuses.append("matched")
                else:
                    profile_preference_statuses.append("unknown")
                    missing_information.append(
                        f"{candidate_name}의 비선호 성분 비교에 필요한 원재료 정보가 부족해요."
                    )
        profile_diet = next(
            (item for item in ("not_match", "caution", "unknown", "match")
             if item in profile_diet_statuses),
            "unknown",
        )
        profile_preference = next(
            (item for item in ("disliked_found", "unknown", "matched")
             if item in profile_preference_statuses),
            "unknown",
        )
        if decision == "no_known_conflict" and profile_diet in {"not_match", "caution"}:
            decision, risk = "caution", "medium"
        elif decision == "no_known_conflict" and profile_diet == "unknown" and has_diet_conditions:
            decision, risk = "check_required", "unknown"
        if "unknown" in statuses:
            missing_information.append(
                f"{active_profile.profile_name} 프로필의 제품별 알레르기 판정 정보가 부족해요."
            )
        profile_answer = " ".join([*lines, *diet_lines])
        profile_results.append({
            "profile_id": active_profile.profile_id or 0,
            "profile_name": active_profile.profile_name,
            "target_type": active_profile.target_type,
            "intent": "food_compare",
            "risk_level": risk,
            "overall_decision": decision,
            "allergy_status": status,
            "diet_match": profile_diet,
            "preference_match": profile_preference,
            "assessment_status": (
                "incomplete"
                if "unknown" in statuses
                or "unknown" in profile_diet_statuses
                or "unknown" in profile_preference_statuses
                else "complete"
            ),
            "answer": profile_answer,
            "reasons": reasons,
        })
        # One person needs no name tag; several are told apart by it.
        summary_lines.append(f"[{active_profile.profile_name}] {profile_answer}" if len(profiles) > 1 else profile_answer)
        all_reasons.extend(reasons)
        all_statuses.extend(statuses)
        diet_statuses.append(profile_diet)
        preference_statuses.append(profile_preference)

    overall_status = max(all_statuses, key=lambda item: priority[item])
    overall_decision = {
        "conflict": "avoid",
        "cross_contact": "caution",
        "unknown": "check_required",
        "no_known_match": "no_known_conflict",
    }[overall_status]
    risk_level = {
        "conflict": "high",
        "cross_contact": "medium",
        "unknown": "unknown",
        "no_known_match": "low",
    }[overall_status]
    overall_diet = next(
        (item for item in ("not_match", "caution", "unknown", "match")
         if item in diet_statuses),
        "unknown",
    )
    overall_preference = next(
        (item for item in ("disliked_found", "unknown", "matched")
         if item in preference_statuses),
        "unknown",
    )
    if overall_decision == "no_known_conflict" and overall_diet in {"not_match", "caution"}:
        overall_decision = "caution"
        risk_level = "medium"
    elif overall_decision == "no_known_conflict" and overall_diet == "unknown" and any(
        resolve_profile_diets(profile, message) or profile._conversation_exclusions for profile in profiles
    ):
        overall_decision = "check_required"
        risk_level = "unknown"
    headline = {
        "conflict": "알레르기 기준으로 먼저 보면, 비교 제품 중 피해야 할 제품이 있어요.",
        "cross_contact": "알레르기 기준으로 먼저 보면, 교차접촉 주의가 필요한 제품이 있어요.",
        "unknown": "알레르기 기준으로 먼저 보면, 판정에 필요한 표시 정보가 부족해요.",
        "no_known_match": "알레르기 기준으로 먼저 보면, 등록된 표시에서 알레르기와 직접 충돌하는 표시를 찾지 못했어요.",
    }[overall_status]
    return (
        "\n".join([headline, *summary_lines]),
        list(dict.fromkeys(all_reasons)),
        profile_results,
        overall_status,
        overall_decision,
        risk_level,
        overall_diet,
        overall_preference,
        list(dict.fromkeys(missing_information)),
    )


def handle_general_allergy_guide(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    from app.intent.explanation_topics import unsupported_allergy_quantity_question
    if product is None and unsupported_allergy_quantity_question(message):
        return build_response(
            intent="general_allergy_guide",
            answer=("현재 확인한 자료에는 실제 혼입 확률이나 혼입량을 계산할 근거가 없어요. "
                    "따라서 숫자나 퍼센트로 추정해서 안내하지 않아요."),
            risk_level="unknown",
            reasons=["혼입 확률·혼입량의 정량 근거 부족"],
            profile=profile,
        ).model_copy(update={"fallback_reason": "rag_question_outside_evidence_scope"})
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
            "식이 조건에 맞는 제품을 고를 때는 원재료명, 알레르기 표시, 제품의 명시된 영양 특성을 같이 확인해야 해요.",
            [
                "채식은 육류·어패류를, 글루텐프리는 밀·보리·호밀과 글루텐프리 표시를 확인해요.",
                "저당·저염·저칼로리·저지방·고단백은 영양 수치와 표시 기준량, 제품에 명시된 특성을 함께 봐요.",
            ],
            ["서비스 내부 임의 수치만으로 적합하다고 판정하지 않아요. 채식은 유제품·달걀 허용 범위를 함께 알려 주세요."],
            ["특정 제품을 보내주면 현재 기준에 맞는지 제품별로 설명할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["일반 식이 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_diet_guide", "unknown", product),
    )


def handle_general_nutrition_guide(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    from app.intent.request_semantics import nutrient_exclusion_question
    if product is None and nutrient_exclusion_question(message):
        return build_response(
            intent="general_nutrition_guide", profile=profile, risk_level="unknown",
            answer="앞의 설명만으로 특정 영양소를 모두 없애야 한다고 결론 내릴 수는 없어요. 현재 검토 자료는 영양소의 종류와 표시를 설명하는 범위이며, 개인의 섭취량이나 식단에서 완전히 제외할지를 판단할 근거는 없어요.",
            reasons=["영양소 전체 제외 판단은 검토 자료 범위 밖"],
        ).model_copy(update={"fallback_reason": "rag_question_outside_evidence_scope"})
    return build_response(
        intent="general_nutrition_guide",
        answer=format_general_answer(
            "영양성분은 하나만 보기보다 함께 해석하는 것이 좋아요.",
            [
                "저당은 당류를, 저염은 나트륨을, 저칼로리는 열량을 먼저 확인해요.",
                "저지방은 지방을, 고단백은 단백질을 확인하되 제품의 표시 기준량도 같이 봐야 해요.",
            ],
            ["1회 제공량·100g·총 내용량처럼 기준이 다르면 수치를 그대로 비교하지 않아요."],
            ["특정 영양성분이나 제품을 보내주면 더 구체적으로 설명할 수 있어요."],
        ),
        risk_level="unknown",
        reasons=["일반 영양 가이드 제공"],
        profile=profile,
        recommended_questions=pick_recommended_questions("general_nutrition_guide", "unknown", product),
    )


def handle_alternative_food_recommend(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    if product is not None and not product.facts_verified:
        return build_unverified_product_response("alternative_food_recommend", profile)

    if product:
        primary = f"{product.product_name or '이 제품'} 기준으로 대체 식품을 찾을 때는 알레르기 충돌 여부와 영양성분 차이를 같이 봐야 해요."
        next_steps = ["현재 DB에서 같은 유형의 후보가 있으면 알레르기와 당류, 나트륨, 열량 기준으로 비교해볼 수 있어요."]
    else:
        primary = "대체 식품은 피해야 하는 성분과 원하는 기준을 같이 정해야 추천 정확도가 올라가요."
        next_steps = ["예를 들어 '우유 알레르기 대신 먹을 간식 추천해줘'처럼 알레르기나 식단 조건을 함께 알려주세요."]

    return build_response(
        intent="alternative_food_recommend",
        answer=format_general_answer(
            primary,
            [
                "우선 알레르기 유발 성분이 없는지 확인하고, 그 다음 당류, 나트륨, 열량 같은 영양 기준을 비교하는 흐름이 좋아요.",
                "활성 프로필의 알레르기와 명시한 식단 조건을 후보별로 다시 확인해요.",
            ],
            ["제품 DB나 영양성분 정보가 부족하면 추천 후보를 확정하기 어려울 수 있어요."],
            next_steps,
        ),
        risk_level="unknown",
        reasons=["대체 식품 추천 의도"],
        profile=profile,
        task_type="product_chat" if product else "general_chat",
        recommended_questions=pick_recommended_questions("alternative_food_recommend", "unknown", product),
    )


def handle_food_compare(
    profile: Optional[Profile],
    product: Optional[Product],
    message: str,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    conversation_state: Optional[dict] = None,
) -> ChatResponse:
    from app.services.operational_policy import unverified_catalog_enabled
    if product is not None and not product.facts_verified and not unverified_catalog_enabled():
        # Team mode compares unverified labels with its source note (Stage B),
        # as it already does when no product is selected.
        return build_unverified_product_response("food_compare", profile)

    from app.services.comparison_context import resolve_comparison
    compared_products, confirmation, comparison_state, effective_message = resolve_comparison(
        message, product, conversation_state,
    )
    if confirmation is not None:
        return confirmation
    message = effective_message
    if len(compared_products) >= 2:
        effective_profiles = list(profiles or ([profile] if profile else []))
        (
            allergy_answer,
            allergy_reasons,
            per_profile_results,
            allergy_status,
            overall_decision,
            risk_level,
            diet_match,
            preference_match,
            missing_information,
        ) = _comparison_allergy_summary(compared_products, effective_profiles, message)
        comparison = _format_product_comparison_answer(
            message,
            product,
            products=compared_products,
        )
        axes = _comparison_axes(message)
        allergy_only = not axes and "알레르기" in message
        if allergy_only:
            comparison = ("", [])
        elif not axes:
            missing_information.append("비교할 영양 기준을 선택해 주세요.")
        else:
            for key, label, _, _ in axes:
                for candidate in compared_products:
                    if candidate.nutrition.get(key) is None:
                        missing_information.append(f"{candidate.product_name}의 {label} 수치가 필요해요.")
            if not products_have_comparable_nutrition_basis(compared_products):
                missing_information.append("비교 제품의 동일한 영양 기준량과 해당 표시의 출처 정보가 필요해요.")
        if comparison is None:
            nutrition_answer = _key_nutrient_overview(compared_products)
            nutrition_reasons = ["영양 비교 기준 미선택: 주요 수치만 표시"]
        else:
            nutrition_answer, nutrition_reasons = comparison

        picked = comparison_state.pop("comparison_auto_picked", None)
        used = (f"비교에 사용한 제품: {', '.join(item.product_name or '이 제품' for item in compared_products)}. "
                "다른 제품이면 이름을 더 정확히 알려 주세요.\n") if picked else ""
        response = build_response(
            intent="food_compare",
            answer=f"{used}{allergy_answer}\n\n{nutrition_answer}",
            risk_level=risk_level,
            assessment_status=(
                "incomplete" if missing_information else "complete"
            ),
            reasons=[*allergy_reasons, *nutrition_reasons],
            profile=profile,
            task_type="product_chat" if product else "general_chat",
            recommended_questions=pick_recommended_questions("food_compare", "unknown", product),
        )
        return response.model_copy(update={
            "overall_decision": overall_decision,
            "allergy_status": allergy_status,
            "diet_match": diet_match,
            "preference_match": preference_match,
            "missing_information": missing_information,
            "per_profile_results": per_profile_results,
            "comparison_products": [public_product_payload(item) for item in compared_products],
            "conversation_state": comparison_state,
        })

    return build_response(
        intent="food_compare",
        answer=format_general_answer(
            "음식 비교는 비교할 대상과 기준이 함께 있어야 정확하게 볼 수 있어요.",
            [
                "칼로리, 단백질, 당류, 나트륨처럼 비교 기준을 하나 이상 정하면 더 명확해요.",
                "알레르기나 식단 조건이 있다면 성분 충돌 여부를 먼저 보고 영양성분을 비교하는 순서가 좋아요.",
            ],
            ["제품별 1회 제공량이나 총 내용량 기준이 다르면 단순 수치 비교가 달라질 수 있어요."],
            ["비교할 음식이나 제품 2개와 기준을 함께 보내주면 그 기준으로 정리할 수 있어요."],
        ),
        risk_level="unknown",
        assessment_status="incomplete",
        reasons=["음식 비교 의도"],
        profile=profile,
        task_type="product_chat" if product else "general_chat",
        recommended_questions=pick_recommended_questions("food_compare", "unknown", product),
    ).model_copy(update={"missing_information": ["DB에서 확인된 비교 대상 제품 두 개가 필요해요."]})


def handle_unknown(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="unknown",
        answer=format_general_answer(
            "질문 뜻을 아직 정확히 잡지 못했어요.",
            [],
            ["제품명, 성분명, 영양소명, 또는 식단 조건이 더 구체적으로 필요해요."],
            ["제품명을 입력하거나 바코드를 스캔한 뒤 '이거 먹어도 돼?', '대두 들어 있어?', '나트륨이 얼마야?'처럼 물어보면 더 정확하게 안내할 수 있어요."],
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


def handle_out_of_domain(profile: Optional[Profile], product: Optional[Product], message: str) -> ChatResponse:
    return build_response(
        intent="out_of_domain",
        answer=format_general_answer(
            "저는 식품 성분, 알레르기, 영양성분, 식단 조건을 기준으로 제품 섭취 가능 여부를 안내하는 챗봇이에요.",
            [],
            ["질환·임신·약물에 맞는 섭취 판단과 진단·치료 안내 및 식품과 무관한 범용 질문은 지원하지 않아요."],
            ["제품명을 입력하거나 바코드를 스캔한 뒤 '이거 먹어도 돼?', '대두 들어 있어?', '나트륨이 얼마야?'처럼 물어봐 주세요."],
        ),
        risk_level="unknown",
        reasons=["서비스 범위 밖 질문"],
        profile=profile,
        task_type="general_chat",
        recommended_questions=[
            "이 제품 먹어도 괜찮아?",
            "이 제품에 특정 성분이 들어 있어?",
            "영양성분을 설명해줘",
        ],
    )
