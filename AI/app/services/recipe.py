"""Deterministic, allergen-checked recipe composition for the food chatbot."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import (
    ChatHistoryItem,
    ChatResponse,
    Product,
    Profile,
    RecipeResult,
)
from app.core.public_product import public_product_payload
from app.services.recipe_validation import (
    valid_amount, unsupported_claim, unlisted_additions, grounded_draft, cooking_operations,
)
from app.services.recipe_templates import _general_recipe_variants, _product_recipe_variants
from app.services.recipe_composition import composition_issues, product_method_conflict
from app.services.reviewed_recipes import (
    reviewed_recipe_variants, reviewed_recipe_condition_conflicts, finalize_reviewed_recipe,
)
from app.services.product import (
    recommend_catalog_product_records,
    recommend_catalog_products,
)
from app.infrastructure.llm import generate_text
from app.services.response_helpers import (
    build_response,
    evaluate_special_diets,
    find_cross_contamination_allergies,
    find_triggered_allergies,
    object_particle,
    resolve_profile_diets,
    scan_disliked_ingredients,
    topic_particle,
    unsupported_profile_allergies,
)


class _RecipeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    servings: str = Field(min_length=1, max_length=64)
    product_name: str = Field(min_length=1, max_length=256)
    ingredients: list[dict[str, str]] = Field(min_length=1, max_length=30)
    steps: list[str] = Field(min_length=1, max_length=20)
    substitutions: list[str] = Field(default_factory=list, max_length=10)


def _recipe_output_schema(product_name: str) -> dict:
    """Provider format constraints supplement, never replace, food validation."""
    schema = _RecipeDraft.model_json_schema()
    schema["required"] = list(schema["properties"])
    schema["properties"]["servings"]["enum"] = ["1인분"]
    schema["properties"]["product_name"]["enum"] = [product_name]
    schema["properties"]["ingredients"]["items"] = {
        "type": "object", "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "amount": {"type": "string"}},
        "required": ["name", "amount"],
    }
    schema["properties"]["substitutions"].pop("default", None)
    schema["properties"]["substitutions"]["maxItems"] = 0
    return schema


_PACKAGED_FOOD_MARKERS = (
    "소스", "드레싱", "육수", "스프", "통조림", "파우더",
    "페이스트", "두유", "두부", "햄", "치즈", "요거트",
    "간장", "케첩", "마요네즈", "시판",
)


def _amount_is_zero(amount: str) -> bool:
    numeric = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*", amount)
    if numeric is None:
        return False
    return float(numeric.group(1).replace(",", ".")) == 0


def _recipe_prompt(
    message: str,
    product: Product,
    profiles: Sequence[Profile],
) -> str:
    allergens = list(dict.fromkeys(
        allergy
        for profile in profiles
        for allergy in profile.resolved_allergies()
    ))
    diets = list(dict.fromkeys(
        diet
        for profile in profiles
        for diet in profile.resolved_special_diets()
    ))
    return (
        "식품 앱의 레시피 초안만 작성하세요. 제품 선택과 안전 판정은 하지 마세요. "
        "반드시 JSON 객체 하나만 반환하세요. 키는 title, servings, product_name, "
        "ingredients, steps, substitutions입니다. ingredients는 name과 amount 문자열을 "
        "가진 객체 배열입니다. 요청 제품명은 바꾸지 마세요.\n"
        f"사용자 요청: {message}\n"
        f"사용할 제품명: {product.product_name}\n"
        f"제외할 알레르기: {', '.join(allergens) or '없음'}\n"
        f"참고 식이 조건: {', '.join(diets) or '없음'}"
    )


def _productless_recipe_prompt(
    message: str,
    allowed_ingredients: Sequence[str],
    diets: Sequence[str],
    profiles: Sequence[Profile],
) -> str:
    allergies = list(dict.fromkeys(
        allergy for profile in profiles for allergy in profile.resolved_allergies()
    ))
    return (
        "식품 앱의 일반 레시피 초안만 JSON으로 작성하세요. "
        "키는 title, servings, product_name, ingredients, steps, substitutions입니다. "
        "product_name은 반드시 '기본 식재료'로 두고, 재료명은 허용 목록의 정확한 이름만 "
        "사용하세요. 모든 재료에 실제 사용량을 적고, 목록 밖의 소스·포장식품이나 "
        "알레르기 성분을 조리 단계·대체 재료에도 추가하지 마세요. "
        "식이 조건의 영양 수치나 인증을 확정하지 마세요.\n"
        f"사용자 요청: {message}\n"
        f"식이 목표: {', '.join(diets) or '없음'}\n"
        f"제외 알레르기: {', '.join(allergies) or '없음'}\n"
        f"허용 재료명: {', '.join(allowed_ingredients)}"
    )


def _parse_recipe_draft(raw: Optional[str]) -> Optional[_RecipeDraft]:
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        draft = _RecipeDraft.model_validate(payload)
    except Exception:
        return None
    if any(
        set(item) != {"name", "amount"}
        or not isinstance(item.get("name"), str)
        or not isinstance(item.get("amount"), str)
        or not item["name"].strip()
        or not item["amount"].strip()
        or not valid_amount(item["amount"])
        or len(item["name"]) > 256
        or len(item["amount"]) > 128
        for item in draft.ingredients
    ):
        return None
    if any(not step.strip() or len(step) > 1000 for step in draft.steps):
        return None
    if any(not item.strip() or len(item) > 1000 for item in draft.substitutions):
        return None
    return draft


def _draft_allergen_conflicts(
    draft: _RecipeDraft,
    profiles: Sequence[Profile],
    product: Optional[Product],
) -> list[str]:
    generated_text = ", ".join([
        draft.title, draft.servings,
        *(item["amount"] for item in draft.ingredients),
        *(item["name"] for item in draft.ingredients if product is None or item["name"] != product.product_name),
        *draft.steps,
        *draft.substitutions,
    ])
    generated_product = Product(
        product_name="생성 레시피",
        rawmtrl_nm=generated_text,
        allergy=[],
        product_found=True,
        facts_verified=True,
    )
    return _profile_conflicts(profiles, generated_product)


def _draft_disliked_ingredients(
    draft: _RecipeDraft,
    profiles: Sequence[Profile],
    selected_product: Optional[Product],
) -> list[str]:
    selected_name = selected_product.product_name if selected_product else None
    generated_text = ", ".join([
        *(item["name"] for item in draft.ingredients if item["name"] != selected_name),
        *(step.replace(selected_name, "") if selected_name else step for step in draft.steps),
        *(item.replace(selected_name, "") if selected_name else item for item in draft.substitutions),
    ])
    return _profile_dislikes(profiles, Product(
        product_name="생성 레시피 추가 재료",
        rawmtrl_nm=generated_text,
        product_found=True,
        facts_verified=True,
    ))


def _draft_uses_selected_product(
    draft: _RecipeDraft,
    product_name: str,
) -> bool:
    normalized_product = "".join(product_name.lower().split())
    return any(
        normalized_product in "".join(item["name"].lower().split())
        and not _amount_is_zero(item["amount"])
        for item in draft.ingredients
    )


def _draft_validation_product(draft: _RecipeDraft) -> Product:
    return Product(
        product_name="생성 레시피",
        rawmtrl_nm=", ".join([
            draft.title, draft.servings,
            *(item["amount"] for item in draft.ingredients),
            *(item["name"] for item in draft.ingredients),
            *draft.steps,
            *draft.substitutions,
        ]),
        allergy=[],
        product_found=True,
        facts_verified=True,
    )


def _draft_packaged_ingredients(
    draft: _RecipeDraft,
    selected_product_name: str,
) -> list[str]:
    names = [
        item["name"].strip()
        for item in draft.ingredients
        if (
            selected_product_name in item["name"]
            or any(marker in item["name"] for marker in _PACKAGED_FOOD_MARKERS)
        )
    ]
    return list(dict.fromkeys(names))


def _draft_unlisted_packaged_mentions(draft: _RecipeDraft) -> list[str]:
    """Reject a new packaged food mentioned only in cooking or swap text."""

    listed_names = [item["name"] for item in draft.ingredients]
    other_text = " ".join([*draft.steps, *draft.substitutions])
    return [
        marker for marker in _PACKAGED_FOOD_MARKERS
        if marker in other_text and not any(marker in name for name in listed_names)
    ]


def _draft_product_method_conflict(draft: _RecipeDraft, product: Product) -> bool:
    return product_method_conflict([*draft.steps, *draft.substitutions], product, draft.ingredients)


def _diet_conflicts(
    profiles: Sequence[Profile],
    product: Product,
    message: str,
) -> list[str]:
    evaluators: Sequence[Optional[Profile]] = profiles or [None]
    return list(dict.fromkeys(
        f"{active_profile.profile_name if active_profile else '이번 요청'}: {conflict}"
        for active_profile in evaluators
        for conflict in evaluate_special_diets(
            active_profile,
            product,
            message,
        )[0]
    ))


def _diet_findings(
    profiles: Sequence[Profile], product: Product, message: str
) -> tuple[list[str], list[str], list[str]]:
    conflicts, cautions, limitations = [], [], []
    for active_profile in profiles or [None]:
        found = evaluate_special_diets(active_profile, product, message)
        name = active_profile.profile_name if active_profile else "이번 요청"
        for target, values in zip((conflicts, cautions, limitations), found):
            target.extend(f"{name}: {value}" for value in values)
    return tuple(list(dict.fromkeys(items)) for items in (conflicts, cautions, limitations))


def _recipe_fingerprint(recipe: RecipeResult) -> str:
    ingredient_names = sorted({
        "".join((item.name if hasattr(item, "name") else item["name"]).lower().split())
        for item in recipe.ingredients
    })
    operations = cooking_operations(recipe.steps)
    # A title is untrusted presentation, not evidence of a different dish.
    # Deliberately ignore amounts too: a cosmetic portion change is not a new recipe.
    payload = [recipe.product_barcode or recipe.product_name, ingredient_names, operations]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _with_recipe_history(response: ChatResponse, prior: Sequence[str]) -> ChatResponse:
    if response.recipe is None:
        return response
    fingerprint = _recipe_fingerprint(response.recipe)
    history = list(dict.fromkeys([*prior, fingerprint]))[-20:]
    return response.model_copy(update={
        "conversation_state": {"version": 1, "recipe_fingerprints": history},
    })


def _profile_conflicts(profiles: Sequence[Profile], product: Product) -> list[str]:
    return list(dict.fromkeys(
        f"{profile.profile_name}: {allergy}"
        for profile in profiles
        for allergy, _ in find_triggered_allergies(profile, product)
    ))


def _profile_dislikes(profiles: Sequence[Profile], product: Product) -> list[str]:
    return list(dict.fromkeys(
        f"{profile.profile_name}: {ingredient}"
        for profile in profiles
        for ingredient in scan_disliked_ingredients(profile, product)
    ))


def _cross_contact_findings(
    profiles: Sequence[Profile],
    product: Product,
) -> list[str]:
    return list(dict.fromkeys(
        f"{profile.profile_name}: {allergy}"
        for profile in profiles
        for allergy, _ in find_cross_contamination_allergies(profile, product)
    ))


def _recipe_profile_results(
    profiles: Sequence[Profile],
    *,
    source_product: Optional[Product],
    selected_product: Optional[Product],
    ingredients: list[dict[str, str]],
    message: str,
) -> tuple[list[dict], str, str, str]:
    if not profiles:
        return [], "unknown", "check_required", "unknown"

    generated_product = Product(
        product_name="생성 레시피",
        rawmtrl_nm=", ".join(item["name"] for item in ingredients),
        allergy=[],
        product_found=True,
        facts_verified=True,
    )
    status_priority = {
        "conflict": 3,
        "cross_contact": 2,
        "unknown": 1,
        "no_known_match": 0,
    }
    results: list[dict] = []
    statuses: list[str] = []
    decisions: list[str] = []
    risks: list[str] = []
    for active_profile in profiles:
        assessed_product = source_product or generated_product
        direct = find_triggered_allergies(active_profile, assessed_product)
        cross_contact = find_cross_contamination_allergies(
            active_profile,
            assessed_product,
        )
        generated_direct = find_triggered_allergies(
            active_profile,
            generated_product,
        )
        diet_product = source_product or generated_product
        diet_conflicts, diet_cautions, diet_limitations = _diet_findings(
            [active_profile], diet_product, message
        )
        requested_diets = resolve_profile_diets(active_profile, message)
        diet_status = (
            "not_match" if diet_conflicts else
            "caution" if diet_cautions else
            "unknown" if diet_limitations or requested_diets else "unknown"
        )
        preference_product = selected_product or source_product
        dislikes = list(dict.fromkeys([
            *(
                scan_disliked_ingredients(active_profile, preference_product)
                if preference_product is not None else []
            ),
            *scan_disliked_ingredients(active_profile, generated_product),
        ]))
        preference_status = (
            "disliked_found" if dislikes else
            "matched" if active_profile.disliked_ingredients else "unknown"
        )

        if not assessed_product.facts_verified:
            status, decision, risk = "unknown", "check_required", "unknown"
            answer = "DB에서 확인된 제품 정보가 없어 판정을 보류했어요."
        elif direct or generated_direct:
            status = "conflict"
            decision = "avoid"
            risk = "high"
            names = [
                allergy
                for allergy, _ in [*direct, *generated_direct]
            ]
            answer = (
                f"원 제품의 {', '.join(dict.fromkeys(names))} 충돌을 확인했고, "
                f"해당 제품은 레시피에 사용하지 않았어요."
                if source_product is not None and selected_product is not source_product
                else f"생성 재료에서 {', '.join(dict.fromkeys(names))} 충돌이 확인됐어요."
            )
        elif cross_contact:
            status = "cross_contact"
            decision = "caution"
            risk = "medium"
            names = [allergy for allergy, _ in cross_contact]
            answer = (
                f"{', '.join(dict.fromkeys(names))} 교차접촉 표시가 있어 "
                "포장지 재확인이 필요해요."
            )
        elif not active_profile.allergy_context_provided or (
            source_product is not None and not (
                source_product.rawmtrl_nm or source_product.allergy or source_product.allergy_warning
            )
        ):
            status = "unknown"
            decision = "check_required"
            risk = "unknown"
            answer = "프로필 또는 제품의 원재료·알레르기 정보가 부족해 개인별 판정을 보류했어요."
        else:
            status = "no_known_match"
            decision = (
                "caution" if diet_status in {"not_match", "caution"}
                else "check_required" if requested_diets and diet_status == "unknown"
                else "no_known_conflict"
            )
            risk = "medium" if decision == "caution" else "unknown" if decision == "check_required" else "low"
            answer = (
                "요청한 식이 조건과 충돌하는 재료가 있어 주의가 필요해요."
                if diet_conflicts
                else "식이 조건의 허용 범위나 표시 근거를 추가 확인해야 해요."
                if diet_cautions or (requested_diets and diet_limitations)
                else "생성 재료에서 등록 알레르기의 직접 충돌을 찾지 못했어요."
            )

        reasons = [
            *(
                [f"직접 충돌: {allergy}" for allergy, _ in direct]
                if direct
                else []
            ),
            *(
                [f"교차접촉: {allergy}" for allergy, _ in cross_contact]
                if cross_contact
                else []
            ),
            *diet_conflicts,
            *diet_cautions,
            *diet_limitations,
            *([f"비선호 성분: {', '.join(dislikes)}"] if dislikes else []),
        ] or (["프로필 또는 제품의 알레르기 정보 부족"] if status == "unknown"
              else ["제공된 알레르기와 생성 재료 대조 완료"])
        if dislikes:
            answer += f" 비선호 성분({', '.join(dislikes)})이 포함돼 있어요."
        results.append({
            "profile_id": active_profile.profile_id or 0,
            "profile_name": active_profile.profile_name,
            "target_type": active_profile.target_type,
            "intent": "recipe_recommend",
            "risk_level": risk,
            "overall_decision": decision,
            "allergy_status": status,
            "diet_match": diet_status,
            "preference_match": preference_status,
            "assessment_status": (
                "incomplete"
                if status == "unknown" or (requested_diets and diet_status == "unknown")
                else "complete"
            ),
            "answer": answer,
            "reasons": reasons,
        })
        statuses.append(status)
        decisions.append(decision)
        risks.append(risk)

    aggregate_status = max(statuses, key=lambda item: status_priority[item])
    aggregate_decision = (
        "avoid"
        if "avoid" in decisions
        else "caution"
        if "caution" in decisions
        else "check_required"
        if "check_required" in decisions
        else "no_known_conflict"
    )
    risk_priority = {"high": 3, "medium": 2, "unknown": 1, "low": 0}
    aggregate_risk = max(risks, key=lambda item: risk_priority[item])
    return results, aggregate_status, aggregate_decision, aggregate_risk


def _requested_diets(
    profiles: Sequence[Profile],
    message: str,
    chat_history: Optional[Sequence[ChatHistoryItem]] = None,
    conversation_state: Optional[dict] = None,
) -> list[str]:
    from app.services.diet_goal_context import read_diet_goal_update
    update = read_diet_goal_update(message)
    additive = bool(re.search(r"(?:도\s*(?:같이|함께|추가|적용)|추가(?:해|로)|포함|더한|더해|유지|까지|(?:같이|함께)\s*적용)", message))
    current_diets = update.apply((conversation_state or {}).get("recipe_requested_diets", []) if update.removed or additive else ())
    if not update.touched and not current_diets and any(cue in message for cue in ("다른", "더", "또", "하나")):
        current_diets = list((conversation_state or {}).get("recipe_requested_diets", []))
        for item in reversed(chat_history or []):
            if current_diets or (conversation_state or {}).get("recipe_goals_initialized"):
                break
            if item.role != "user" or "레시피" not in item.content:
                continue
            current_diets = resolve_profile_diets(None, item.content)
            # The most recent recipe request is authoritative, even if empty.
            if current_diets or not any(cue in item.content for cue in ("다른", "더", "또", "하나")):
                break
    return list(dict.fromkeys([
        *(
            diet
            for active_profile in profiles
            for diet in active_profile.resolved_special_diets()
        ),
        *current_diets,
    ]))


def _plain_ingredient_conflicts(
    profiles: Sequence[Profile],
    ingredients: list[dict[str, str]],
    product: Optional[Product],
) -> list[str]:
    plain_names = [
        item["name"]
        for item in ingredients
        if product is None or item["name"] != (product.product_name or "선택한 제품")
    ]
    ingredient_product = Product(
        product_name="레시피 기본 재료",
        rawmtrl_nm=", ".join(plain_names),
        allergy=[],
        product_found=True,
        facts_verified=True,
    )
    return _profile_conflicts(profiles, ingredient_product)


def _plain_ingredient_dislikes(
    profiles: Sequence[Profile],
    ingredients: list[dict[str, str]],
    product: Optional[Product],
) -> list[str]:
    extra_names = [
        item["name"] for item in ingredients
        if product is None or item["name"] != product.product_name
    ]
    return _profile_dislikes(profiles, Product(
        product_name="레시피 추가 재료",
        rawmtrl_nm=", ".join(extra_names),
        product_found=True,
        facts_verified=True,
    ))


def build_recipe_response(
    profile: Optional[Profile],
    product: Optional[Product],
    message: str,
    *,
    profiles: Optional[Sequence[Profile]] = None,
    chat_history: Optional[Sequence[ChatHistoryItem]] = None,
    conversation_state: Optional[dict] = None,
) -> ChatResponse:
    active_profiles = [item for item in (profiles or ([profile] if profile else [])) if item.is_active]
    from app.services.product_search import recipe_source_query
    if (product is None and recipe_source_query(message)) or (product is not None and product.product_found is False):
        from app.services.recipe_request_context import missing_recipe_source_response
        return missing_recipe_source_response(message, conversation_state)
    from app.services.recipe_request_context import unsupported_recipe_request
    unsupported_request = unsupported_recipe_request(message) if product is None else None
    if unsupported_request:
        answer, reason = unsupported_request
        return build_response(
            intent="recipe_recommend", answer=answer, risk_level="unknown",
            assessment_status="unsupported", reasons=[reason], profile=profile, task_type="general_chat",
        )
    prior_fingerprints = [
        item for item in (conversation_state or {}).get("recipe_fingerprints", [])
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
    ][:20]
    requested_diets = _requested_diets(active_profiles, message, chat_history, conversation_state)
    effective_message = f"{message} {' '.join(requested_diets)}"
    unsupported = list(dict.fromkeys(
        allergy
        for active_profile in active_profiles
        for allergy in unsupported_profile_allergies(active_profile)
    ))
    if unsupported:
        return build_response(
            intent="recipe_recommend",
            answer=(
                "현재 규칙에서 확인할 수 없는 알레르기 항목이 있어 레시피를 "
                "생성하지 않았어요. 지원 여부와 원재료 표시를 먼저 확인해 주세요."
            ),
            risk_level="unknown",
            assessment_status="unsupported",
            reasons=[f"지원되지 않는 알레르기: {', '.join(unsupported[:3])}"],
            profile=profile,
            task_type="general_chat" if product is None else "product_chat",
        )

    source_allergy_conflicts = (
        _profile_conflicts(active_profiles, product) if product else []
    )
    if product is not None and not (
        product.facts_verified and (product.rawmtrl_nm or product.allergy or product.allergy_warning)
    ):
        missing = ["요청 제품의 원재료·알레르기 표시 정보가 필요해요."]
        per_profile, status, decision, risk = _recipe_profile_results(
            active_profiles, source_product=product, selected_product=None,
            ingredients=[], message=effective_message,
        )
        return build_response(
            intent="recipe_recommend", profile=profile, task_type="product_chat",
            answer="요청 제품의 원재료·알레르기 표시가 부족해 해당 제품을 사용한 레시피를 생성하지 않았어요. 최신 표시를 먼저 확인해 주세요.",
            risk_level=risk, assessment_status="incomplete", reasons=missing,
        ).model_copy(update={
            "allergy_status": status, "overall_decision": decision,
            "missing_information": missing, "per_profile_results": per_profile,
        })
    source_diet_conflicts, source_diet_cautions, source_diet_limitations = (
        _diet_findings(active_profiles, product, effective_message)
        if product else ([], [], [])
    )
    unresolved_source_diet = bool(source_diet_cautions) or (
        "글루텐프리" in requested_diets and bool(source_diet_limitations)
    )
    if product is not None and unresolved_source_diet and not source_diet_conflicts and not source_allergy_conflicts:
        reasons = [*source_diet_cautions, *source_diet_limitations]
        response = build_response(
            intent="recipe_recommend",
            answer=(
                "요청 제품의 식이 조건을 아직 확인할 수 없어 레시피를 생성하지 않았어요. "
                f"{' '.join(reasons)} 채식 유형이나 제품의 최신 표시를 먼저 확인해 주세요."
            ),
            risk_level="unknown",
            assessment_status="incomplete",
            reasons=reasons,
            profile=profile,
            task_type="product_chat",
        )
        per_profile, allergy_status, decision, _ = _recipe_profile_results(
            active_profiles, source_product=product, selected_product=None,
            ingredients=[], message=effective_message,
        )
        return response.model_copy(update={
            "allergy_status": allergy_status,
            "overall_decision": decision,
            "diet_match": "caution" if source_diet_cautions else "unknown",
            "per_profile_results": per_profile,
            "missing_information": reasons,
        })
    source_conflicts = [*source_allergy_conflicts, *source_diet_conflicts]
    source_blocked = bool(source_conflicts)
    selected_product = product
    recommendations: list[dict] = []
    if source_blocked and product is not None:
        replacement_records = recommend_catalog_product_records(
            effective_message,
            active_profiles,
            source_product=product,
            limit=3,
        )
        recommendations = recommend_catalog_products(
            effective_message,
            active_profiles,
            source_product=product,
            limit=3,
        )
        selected_product = replacement_records[0] if replacement_records else None
        if selected_product is None:
            conflict_label = (
                "알레르기 직접 충돌" if source_allergy_conflicts
                else "식이 조건 불일치"
            )
            blocked_response = build_response(
                intent="recipe_recommend",
                answer=(
                    f"{product.product_name or '요청 제품'}은 조건에 맞지 않아 레시피에 사용하지 않았어요. "
                    f"확인된 이유: {'; '.join(source_conflicts)} "
                    "현재 DB에서는 표시를 확인할 수 있는 대체 제품을 찾지 못해 "
                    "레시피도 생성하지 않았어요."
                ),
                risk_level="high" if source_allergy_conflicts else "medium",
                reasons=[f"요청 제품 {conflict_label}", *source_conflicts],
                profile=profile,
                task_type="product_chat",
            )
            (
                per_profile_results,
                aggregate_allergy_status,
                aggregate_decision,
                _aggregate_risk,
            ) = _recipe_profile_results(
                active_profiles,
                source_product=product,
                selected_product=None,
                ingredients=[],
                message=effective_message,
            )
            return blocked_response.model_copy(update={
                "overall_decision": aggregate_decision,
                "allergy_status": aggregate_allergy_status,
                "diet_match": "not_match" if source_diet_conflicts else "unknown",
                "per_profile_results": per_profile_results,
            })

    history_text = "\n".join(
        item.content
        for item in (chat_history or [])
        if item.role == "assistant"
    )
    variants = (
        _product_recipe_variants(selected_product)
        if selected_product is not None
        else [*_general_recipe_variants(requested_diets), *reviewed_recipe_variants(requested_diets)]
    )
    if selected_product is None:
        from app.services.recipe_request_context import requested_dish_name, matches_dish
        dish = requested_dish_name(message)
        named = [variant for variant in variants
                 if dish and variant.get("source_recipe_id") and matches_dish(dish, variant["title"])]
        if named:
            # A named reviewed dish must not silently fall back to another dish.
            variants = named
    selected_variant = None
    rejected_conflicts: list[str] = []
    product_name = (
        selected_product.product_name
        if selected_product and selected_product.product_name
        else "기본 식재료"
    )
    for candidate_variant in variants:
        if candidate_variant["title"] in history_text:
            continue
        candidate_recipe = RecipeResult(
            title=candidate_variant["title"], servings=candidate_variant.get("servings", "1인분"),
            product_name=product_name,
            product_barcode=selected_product.barcode if selected_product else None,
            ingredients=candidate_variant["ingredients"],
            steps=candidate_variant["steps"], allergen_check=["후보 검사 전"],
        )
        if _recipe_fingerprint(candidate_recipe) in prior_fingerprints:
            continue
        candidate_ingredients = candidate_variant["ingredients"]
        content_issues = composition_issues(candidate_ingredients, candidate_variant["steps"], product=selected_product)
        if content_issues:
            rejected_conflicts.extend(content_issues)
            continue
        candidate_conflicts = _plain_ingredient_conflicts(
            active_profiles,
            candidate_ingredients,
            selected_product,
        )
        if candidate_variant.get("source_recipe_id"):
            candidate_conflicts.extend(reviewed_recipe_condition_conflicts(candidate_variant, active_profiles))
        candidate_diet_conflicts, candidate_diet_cautions, _ = _diet_findings(
            active_profiles,
            Product(
                product_name="레시피 기본 재료",
                rawmtrl_nm=", ".join(
                    item["name"] for item in candidate_ingredients
                ),
                allergy=[],
                product_found=True,
                facts_verified=True,
            ),
            effective_message,
        )
        candidate_dislikes = _plain_ingredient_dislikes(
            active_profiles, candidate_ingredients, selected_product,
        )
        if candidate_variant.get("source_recipe_id"):
            candidate_diet_conflicts.extend(candidate_diet_cautions)
        if candidate_conflicts or candidate_diet_conflicts or candidate_dislikes:
            rejected_conflicts.extend([
                *candidate_conflicts,
                *candidate_diet_conflicts,
                *[f"비선호 재료: {item}" for item in candidate_dislikes],
            ])
            continue
        selected_variant = candidate_variant
        break

    if selected_variant is None:
        return build_response(
            intent="recipe_recommend",
            answer=(
                "현재 조건과 제품 유형에서 알레르기·식이·비선호 재료 조건을 통과하면서 "
                "이 대화에서 아직 보여주지 않은 레시피를 만들지 못했어요."
            ),
            risk_level="unknown",
            assessment_status="incomplete",
            reasons=[
                "중복 방지 또는 생성 레시피 사후 조건 검증 실패",
                *list(dict.fromkeys(rejected_conflicts)),
            ],
            profile=profile,
            task_type="general_chat" if product is None else "product_chat",
        )

    title = selected_variant["title"]
    servings = selected_variant.get("servings", "1인분")
    ingredients = selected_variant["ingredients"]
    steps = [
        *selected_variant["steps"],
        "추가 포장 식품을 쓰려면 해당 제품의 원재료·알레르기 표시를 먼저 확인해요.",
    ]

    applied_diets: list[str] = []
    allergen_check = [
        (f"{active_profile.profile_name}: 생성 재료에서 등록 알레르기 직접 충돌 없음"
         if active_profile.allergy_context_provided
         else f"{active_profile.profile_name}: 알레르기 정보 미입력으로 개인별 대조 불가")
        for active_profile in active_profiles
    ] or ["선택된 프로필이 없어 개인 알레르기는 적용하지 못함"]
    cross_contact = (
        _cross_contact_findings(active_profiles, selected_product)
        if selected_product is not None
        else []
    )
    packaged_cautions = [
        "DB 정보와 실제 포장지가 다를 수 있으므로 조리 전에 최신 원재료·알레르기 표시를 다시 확인하세요.",
        "소스·드레싱 같은 추가 포장 식품도 별도로 알레르기 표시를 확인하세요.",
    ]
    if cross_contact:
        packaged_cautions.insert(
            0,
            f"교차접촉 가능성 표시 확인: {', '.join(cross_contact)}",
        )

    recipe = RecipeResult(
        title=title,
        servings=servings,
        product_name=product_name,
        product_barcode=selected_product.barcode if selected_product else None,
        source_product_blocked=source_blocked,
        ingredients=ingredients,
        steps=steps,
        substitutions=(
            [
                f"{product.product_name or '요청 제품'}은 "
                f"{'알레르기 충돌' if source_allergy_conflicts else '식이 조건 불일치'} 때문에 "
                f"사용하지 않고, {product_name}{object_particle(product_name)} 사용했어요."
            ]
            if source_blocked
            else []
        ),
        applied_profiles=[item.profile_name for item in active_profiles],
        applied_diets=applied_diets,
        requested_diets=requested_diets,
        diet_limitations=(
            ["식이 목표를 재료와 조리 방식에 반영했지만 영양 수치·인증 적합성은 검증하지 않았어요."]
            if requested_diets else []
        ),
        allergen_check=allergen_check,
        packaged_item_cautions=packaged_cautions,
    )
    if source_blocked and product is not None:
        source_name = product.product_name or "요청 제품"
        headline = (
            f"{source_name}{topic_particle(source_name)} 조건에 맞지 않아 레시피에 사용하지 않았어요. "
            f"확인된 이유: {'; '.join(source_conflicts)} "
            f"대신 DB에서 충돌 후보를 제외한 {product_name}으로 바꿨어요."
        )
        risk = "high" if source_allergy_conflicts else "medium"
        reasons = ["요청 제품 사용 차단", *source_conflicts]
    else:
        headline = f"{title} 레시피를 만들었어요."
        risk = "low" if active_profiles else "unknown"
        reasons = [
            "제공된 알레르기와 생성 재료 대조 완료"
            if active_profiles and all(p.allergy_context_provided for p in active_profiles)
            else "알레르기 정보가 없는 프로필은 개인별 대조 불가"
        ]

    diet_limitation = (
        " 식이 조건은 재료와 조리 방식에 반영했지만, "
        "실제 영양수치나 인증 충족을 단정하지 않아요."
        if requested_diets
        else ""
    )

    (
        per_profile_results,
        aggregate_allergy_status,
        aggregate_decision,
        aggregate_profile_risk,
    ) = _recipe_profile_results(
        active_profiles,
        source_product=product,
        selected_product=selected_product,
        ingredients=ingredients,
        message=effective_message,
    )
    risk_priority = {"high": 3, "medium": 2, "unknown": 1, "low": 0}
    risk = max(
        [risk, aggregate_profile_risk],
        key=lambda item: risk_priority[item],
    )

    response = build_response(
        intent="recipe_recommend",
        answer=(
            f"{headline}\n{servings} 기준이며, 아래 재료와 순서를 확인해 주세요. "
            "'충돌 없음'은 현재 DB 표시와 등록된 알레르기 기준의 결과이지 "
            f"절대적인 안전 보장은 아니에요.{diet_limitation}"
        ),
        risk_level=risk,
        reasons=reasons,
        profile=profile,
        task_type="product_chat" if product is not None else "general_chat",
        recommended_questions=["다른 레시피도 추천해줘"],
    )
    response = response.model_copy(update={
        "recipe": recipe,
        "recommended_products": recommendations,
        "overall_decision": aggregate_decision,
        "allergy_status": aggregate_allergy_status,
        "diet_match": next(
            (status for status in ("not_match", "caution", "unknown", "match")
             if any(result["diet_match"] == status for result in per_profile_results)),
            "unknown",
        ),
        "preference_match": next(
            (status for status in ("disliked_found", "unknown", "matched")
             if any(result["preference_match"] == status for result in per_profile_results)),
            "unknown",
        ),
        "per_profile_results": per_profile_results,
        "current_product": (
            public_product_payload(product)
            if product is not None and product.facts_verified
            else None
        ),
    })
    if selected_variant.get("source_recipe_id"):
        return _with_recipe_history(
            finalize_reviewed_recipe(response, selected_variant, requested_diets), prior_fingerprints,
        )
    # Source portions must never enter the LLM's single-serving amount pool.
    variants = [variant for variant in variants if not variant.get("source_recipe_id")]
    allowed_general_ingredients = {
        item["name"]
        for variant in _general_recipe_variants(requested_diets)
        for item in variant["ingredients"]
    }
    draft_variants = [*variants, *_general_recipe_variants(requested_diets)]
    prompt = (
        _recipe_prompt(effective_message, selected_product, active_profiles)
        if selected_product is not None
        else _productless_recipe_prompt(
            effective_message,
            sorted(allowed_general_ingredients),
            requested_diets,
            active_profiles,
        )
    )
    prompt += (
        '\nservings는 숫자가 아닌 문자열 "1인분"으로 작성하세요. '
        "아래 검토된 재료명·사용량 조합만 사용하세요. "
        "제목은 재료명과 요리명, 단계는 해당 재료를 썰기·섞기·익히기 등 간단한 조리 동작만 "
        "적으세요. 안전·영양 설명, 새 재료, 임의 분량이나 조리 수치를 추가하지 마세요. "
        "제목의 요리명은 샐러드, 덮밥, 볼, 컵, 수프, 쌈, 스무디, 귀리죽, 토핑 중에서 고르세요. "
        "단계는 정확한 재료명에 조사와 동작만 붙인 짧은 문장으로 쓰세요. "
        "동작 문장은 '썰어요', '섞어요', '담아요', '올려요', '넣어요', '뿌려요', "
        "'씻어요', '끓여요', '익혀요', '부숴요', '갈아요' 같은 해요체로 끝내세요. "
        "예: '오이를 썰어요.', '오이와 양배추를 섞어요.', '레몬즙을 넣어요.' "
        "예시 재료도 실제 재료 목록에 있을 때만 사용할 수 있어요. "
        "substitutions는 빈 배열로 두세요.\n검토된 재료·양: "
        + json.dumps([v["ingredients"] for v in draft_variants], ensure_ascii=False)
    )
    from app.infrastructure.llm_trace import llm_purpose
    with llm_purpose("recipe_draft"):
        raw_draft = generate_text(prompt, json_schema=_recipe_output_schema(product_name))
    if raw_draft is None:
        return _with_recipe_history(response, prior_fingerprints)
    draft = _parse_recipe_draft(raw_draft)
    if draft is None:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_invalid_draft",
            "validation_failure_types": ["recipe_llm_invalid_draft"],
        }), prior_fingerprints)
    if draft.product_name.strip() != product_name:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_product_changed",
            "validation_failure_types": ["recipe_llm_product_changed"],
        }), prior_fingerprints)
    if selected_product is not None and not _draft_uses_selected_product(draft, product_name):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_product_missing_from_ingredients",
            "validation_failure_types": [
                "recipe_llm_product_missing_from_ingredients"
            ],
        }), prior_fingerprints)
    if selected_product is None and any(
        item["name"] not in allowed_general_ingredients
        for item in draft.ingredients
    ):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unverified_ingredient",
            "validation_failure_types": ["recipe_llm_unverified_ingredient"],
        }), prior_fingerprints)
    if selected_product is not None and _draft_product_method_conflict(draft, selected_product):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_product_method_conflict",
            "validation_failure_types": ["recipe_llm_product_method_conflict"],
        }), prior_fingerprints)
    if draft.title.strip() in history_text:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_duplicate_title",
            "validation_failure_types": ["recipe_llm_duplicate_title"],
        }), prior_fingerprints)
    if _draft_allergen_conflicts(draft, active_profiles, selected_product):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_allergen_conflict",
            "validation_failure_types": ["recipe_llm_allergen_conflict"],
        }), prior_fingerprints)
    if unsupported_claim(" ".join([
        draft.title, draft.servings, *draft.steps, *draft.substitutions,
        *(item["name"] + " " + item["amount"] for item in draft.ingredients),
    ])):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unsupported_claim",
            "validation_failure_types": ["recipe_llm_unsupported_claim"],
        }), prior_fingerprints)
    if _draft_disliked_ingredients(draft, active_profiles, selected_product):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_disliked_ingredient",
            "validation_failure_types": ["recipe_llm_disliked_ingredient"],
        }), prior_fingerprints)
    if _draft_unlisted_packaged_mentions(draft):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unlisted_packaged_ingredient",
            "validation_failure_types": ["recipe_llm_unlisted_packaged_ingredient"],
        }), prior_fingerprints)
    draft_text_product = _draft_validation_product(draft)
    draft_diet_conflicts, draft_diet_cautions, _ = _diet_findings(
        active_profiles, draft_text_product, effective_message,
    )
    if draft_diet_conflicts or draft_diet_cautions:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_diet_conflict",
            "validation_failure_types": ["recipe_llm_diet_conflict"],
        }), prior_fingerprints)

    # Product-bound drafts may use the selected, DB-grounded package plus known
    # plain ingredients. A keyword blacklist cannot identify every composite food.
    allowed_names = allowed_general_ingredients | {
        item["name"] for variant in variants for item in variant["ingredients"]
    }
    if any(item["name"] not in allowed_names for item in draft.ingredients) or unlisted_additions(
        [*draft.steps, *draft.substitutions], [item["name"] for item in draft.ingredients],
    ):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unverified_ingredient",
            "validation_failure_types": ["recipe_llm_unverified_ingredient"],
        }), prior_fingerprints)

    packaged_ingredients = _draft_packaged_ingredients(draft, product_name)
    if selected_product is not None and any(
        product_name not in ingredient for ingredient in packaged_ingredients
    ):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unverified_packaged_ingredient",
            "validation_failure_types": ["recipe_llm_unverified_packaged_ingredient"],
        }), prior_fingerprints)
    if not grounded_draft(draft, draft_variants):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_unverifiable_composition",
            "validation_failure_types": ["recipe_llm_unverifiable_composition"],
        }), prior_fingerprints)
    content_issues = composition_issues(draft.ingredients, draft.steps, draft.substitutions, selected_product)
    if content_issues:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_composition_conflict",
            "validation_failure_types": content_issues,
        }), prior_fingerprints)
    if "고단백" in requested_diets and not any(
        item["name"] in {"삶은 병아리콩", "삶은 렌틸콩", "두부", product_name}
        for item in draft.ingredients
    ):
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_diet_composition_missing",
            "validation_failure_types": ["recipe_llm_diet_composition_missing"],
        }), prior_fingerprints)
    validated_recipe = recipe.model_copy(update={
        "title": draft.title.strip(),
        "servings": draft.servings.strip(),
        "ingredients": draft.ingredients,
        "steps": [step.strip() for step in draft.steps],
        "substitutions": list(dict.fromkeys([
            *recipe.substitutions,
            *(item.strip() for item in draft.substitutions),
        ]))[:10],
        "packaged_item_cautions": list(dict.fromkeys([
            *recipe.packaged_item_cautions,
            *(
                [
                    "포장 식품 확인 대상: "
                    + ", ".join(packaged_ingredients)
                ]
                if packaged_ingredients
                else []
            ),
        ])),
    })
    if _recipe_fingerprint(validated_recipe) in prior_fingerprints:
        return _with_recipe_history(response.model_copy(update={
            "fallback_reason": "recipe_llm_duplicate_fingerprint",
            "validation_failure_types": ["recipe_llm_duplicate_fingerprint"],
        }), prior_fingerprints)
    draft_profiles, draft_status, draft_decision, draft_risk = _recipe_profile_results(
        active_profiles, source_product=product, selected_product=selected_product,
        ingredients=draft.ingredients, message=effective_message,
    )
    return _with_recipe_history(response.model_copy(update={
        "answer_source": "hybrid",
        "pipeline_stage": "llm",
        "answer": (
            f"{headline + ' ' if source_blocked else ''}"
            f"{validated_recipe.title} 레시피를 준비했어요. "
            "개인별 알레르기 대조 결과와 미확인 항목은 아래를 참고해 주세요. "
            f"포장 식품은 조리 전에 최신 원재료·알레르기 표시를 확인해 주세요.{diet_limitation}"
        ),
        "recipe": validated_recipe,
        "per_profile_results": draft_profiles,
        "allergy_status": draft_status,
        "overall_decision": draft_decision,
        "risk_level": draft_risk,
    }), prior_fingerprints)
