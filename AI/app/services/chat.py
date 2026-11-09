import re
from typing import Callable, List, Optional

from app.intent.classifier import classify_intent_result
from app.intent.rules import is_productless_diet_recommendation
from app.services.hybrid import enhance_rule_response, try_general_llm_fallback, validate_final_response_grounding
from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile
from app.core.public_product import internal_product_reference, public_product_payload, public_conversation_state
from app.services.emergency_triage import build_emergency_triage_response
from app.services.result_semantics import attach_result_semantics
from app.services.diet_conversation_context import profile_key
from app.services.condition_subject import profile_is_named
from app.services.food_conditions import prepare_food_conditions
from app.services.product import (
    recommend_catalog_products,
)
from app.services.recipe import build_recipe_response
from app.services.response_assessment import attach_response_assessment
from app.services.response_helpers import (
    find_cross_contamination_allergies,
    find_triggered_allergies,
)
from app.services.profile_context import (
    active_profile_names,
    active_profiles_from_inputs,
    build_multi_profile_answer,
    highest_risk_level,
    merge_profiles,
)
from app.services.handlers import (
    handle_can_i_eat,
    handle_alternative_food_recommend,
    handle_food_compare,
    handle_general_allergy_guide,
    handle_general_diet_guide,
    handle_general_nutrition_guide,
    handle_ingredient_check,
    handle_missing_product,
    handle_nutrition_explain,
    handle_out_of_domain,
    handle_product_summary,
    handle_profile_based_recheck,
    handle_unknown,
)


IntentHandler = Callable[[Optional[Profile], Optional[Product], str], ChatResponse]

_RISK_PRIORITY = {
    "low": 0,
    "unknown": 1,
    "medium": 2,
    "high": 3,
}


def _aggregate_assessment_status(responses: List[ChatResponse], risk_level: str) -> str:
    statuses = [response.assessment_status for response in responses]
    if all(status == "complete" for status in statuses):
        return "complete"
    if risk_level == "unknown" and any(status == "unsupported" for status in statuses):
        return "unsupported"
    return "incomplete"


def _target_profile_from_message(message: str, profiles: List[Profile]) -> Optional[Profile]:
    matches = [p for p in profiles if profile_is_named(p, message)]
    return matches[0] if len(matches) == 1 else None


def extract_user_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
) -> str:
    from app.intent.request_semantics import personal_assessment_request
    from app.intent.rules import rule_based_classify_intent
    import re
    selected = active_profiles_from_inputs(profile, active_profiles)
    classification_profile = merge_profiles(selected) or profile
    rule_intent = rule_based_classify_intent(message, product, classification_profile, chat_history or [])
    if (rule_intent in {"unknown", "ambiguous"} and product is not None
            and product.product_found and personal_assessment_request(message)):
        collective = re.search(r"(?:두|세|네|모든|선택한)\s*(?:사람|명|프로필)|둘\s*다|모두|가족", message)
        if collective and selected:
            return "can_i_eat"
        if any(profile_is_named(p, message) for p in selected):
            return "can_i_eat"
    result = classify_intent_result(
        message,
        product,
        profile=classification_profile,
        chat_history=chat_history,
    )
    return result.intent


def build_rule_based_response(
    intent: str,
    profile: Optional[Profile],
    product: Optional[Product],
    message: str,
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
    conversation_state: Optional[dict] = None,
) -> ChatResponse:
    product_handlers: dict[str, IntentHandler] = {
        "missing_product": handle_missing_product,
        "ingredient_check": handle_ingredient_check,
        "can_i_eat": handle_can_i_eat,
        "nutrition_explain": handle_nutrition_explain,
        "profile_based_recheck": handle_profile_based_recheck,
        "product_summary": handle_product_summary,
    }

    general_handlers: dict[str, IntentHandler] = {
        "general_allergy_guide": handle_general_allergy_guide,
        "general_diet_guide": handle_general_diet_guide,
        "general_nutrition_guide": handle_general_nutrition_guide,
        "alternative_food_recommend": handle_alternative_food_recommend,
        "food_compare": handle_food_compare,
        "out_of_domain": handle_out_of_domain,
        "unknown": handle_unknown,
    }

    profiles = active_profiles_from_inputs(profile, active_profiles)

    if intent == "recipe_recommend":
        return build_recipe_response(
            profile,
            product,
            message,
            profiles=profiles,
            chat_history=chat_history,
            conversation_state=conversation_state,
        ).model_copy(update={
            "pipeline_stage": "facts",
            "active_profile_names": active_profile_names(profiles),
        })

    if intent == "profile_based_recheck" and product is not None:
        target_profile = _target_profile_from_message(message, profiles)
        if target_profile is not None:
            response = handle_can_i_eat(target_profile, product, message)
            target_name = target_profile.profile_name or "선택한 프로필"
            return response.model_copy(update={
                "intent": "profile_based_recheck",
                "answer": f"'{target_name}' 프로필 기준으로 다시 확인했어요.\n{response.answer}",
                "reasons": [f"{target_name} 프로필 기준 재확인", *response.reasons],
                "pipeline_stage": "facts",
                "profile_name": target_name,
                "active_profile_names": active_profile_names(profiles),
            })

    multi_profile_product_intents = {
        "can_i_eat",
        "ingredient_check",
        "nutrition_explain",
        "product_summary",
    }
    if (
        len(profiles) > 1
        and intent in multi_profile_product_intents
        and product is not None
    ):
        individual_responses = [
            attach_result_semantics(
                product_handlers[intent](active_profile, product, message),
                product=product,
                profiles=[active_profile],
                message=message,
            )
            for active_profile in profiles
        ]
        merged_profile = merge_profiles(profiles)
        base_response = product_handlers[intent](
            merged_profile,
            product,
            message,
        )
        combined_answer = (
            build_multi_profile_answer(profiles, individual_responses)
            if intent == "can_i_eat"
            else base_response.answer
        )
        combined_reasons = [
            f"{active_profile.profile_name}: {reason}"
            for active_profile, response in zip(profiles, individual_responses)
            for reason in response.reasons
        ]
        per_profile_results = [
            {
                "profile_id": active_profile.profile_id or 0,
                "profile_name": active_profile.profile_name,
                "target_type": active_profile.target_type,
                "intent": response.intent,
                "risk_level": response.risk_level,
                "overall_decision": response.overall_decision,
                "allergy_status": response.allergy_status,
                "diet_match": response.diet_match,
                "preference_match": response.preference_match,
                "assessment_status": response.assessment_status,
                "answer": response.answer,
                "reasons": response.reasons,
            }
            for active_profile, response in zip(profiles, individual_responses)
        ]
        aggregate_risk_level = highest_risk_level(individual_responses)
        recommended_questions = list(dict.fromkeys(
            question
            for response in individual_responses
            for question in response.recommended_questions
        ))[:3]
        return ChatResponse(
            task_type=base_response.task_type,
            answer_source="rules",
            pipeline_stage="facts",
            intent=intent,
            answer=combined_answer,
            risk_level=aggregate_risk_level,
            assessment_status=_aggregate_assessment_status(
                individual_responses,
                aggregate_risk_level,
            ),
            reasons=combined_reasons,
            profile_name=merged_profile.profile_name if merged_profile else None,
            active_profile_names=active_profile_names(profiles),
            per_profile_results=per_profile_results,
            recommended_questions=(
                recommended_questions
                or base_response.recommended_questions
            ),
        )

    if intent in product_handlers:
        if product is None and intent != "missing_product":
            return handle_unknown(profile, product, message)
        response = product_handlers[intent](profile, product, message)
        return response.model_copy(update={
            "pipeline_stage": "facts",
            "active_profile_names": active_profile_names(profiles),
        })

    if intent == "food_compare":
        response = handle_food_compare(
            profile,
            product,
            message,
            profiles=profiles,
            conversation_state=conversation_state,
        )
        return response.model_copy(update={
            "pipeline_stage": "facts",
            "active_profile_names": active_profile_names(profiles),
        })

    if intent in general_handlers:
        if intent == "general_allergy_guide" and product is None:
            from app.intent.explanation_topics import resolve_explanation_question
            message = resolve_explanation_question(message, chat_history)
        response = general_handlers[intent](profile, product, message)
        return response.model_copy(update={
            "pipeline_stage": "facts",
            "active_profile_names": active_profile_names(profiles),
        })

    response = handle_unknown(profile, product, message)
    return response.model_copy(update={
        "pipeline_stage": "facts",
        "active_profile_names": active_profile_names(profiles),
    })


def generate_final_response(
    rule_response: ChatResponse,
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
) -> ChatResponse:
    # Client-supplied facts are lookup hints, not grounding evidence.  Keep the
    # fail-closed rule response out of the LLM/RAG and allergy/recommendation
    # post-processing paths, all of which otherwise consume product fields.
    if product is not None and not product.facts_verified:
        return rule_response.model_copy(update={"pipeline_stage": "rules"})

    effective_profiles = active_profiles_from_inputs(profile, active_profiles)
    effective_profile = merge_profiles(effective_profiles) or profile
    grounded_rule_response = attach_profile_allergy_alert(
        rule_response,
        product=product,
        profile=effective_profile,
        profiles=effective_profiles,
    )

    if grounded_rule_response.intent == "recipe_recommend":
        return grounded_rule_response.model_copy(update={
            "pipeline_stage": (
                "llm"
                if grounded_rule_response.answer_source == "hybrid"
                else "rules"
            )
        })

    if grounded_rule_response.intent == "food_compare":
        # The handler already evaluated every compared catalog product.  The
        # generic LLM path receives only one contextual product (or none) and
        # must not rewrite or drop those allergy-first comparison findings.
        return grounded_rule_response.model_copy(update={"pipeline_stage": "rules"})

    if grounded_rule_response.task_type == "product_chat":
        final_response = enhance_rule_response(
            grounded_rule_response,
            message,
            effective_profile,
            product,
            profiles=effective_profiles,
            chat_history=chat_history,
        )
    else:
        final_response = try_general_llm_fallback(
            grounded_rule_response,
            message,
            effective_profile,
            product,
            chat_history=chat_history,
        )

    if final_response.answer_source != "rules":
        final_response = validate_final_response_grounding(
            final_response=final_response,
            rule_response=grounded_rule_response,
            product=product,
            rag_context=final_response.grounding_context,
        )
    final_response = attach_profile_allergy_alert(
        final_response,
        product=product,
        profile=effective_profile,
        profiles=effective_profiles,
    )
    final_response = attach_product_recommendations(
        final_response,
        product=product,
        profile=effective_profile,
        profiles=effective_profiles,
        message=message,
    )

    if final_response.answer_source == "rules":
        return final_response.model_copy(update={"pipeline_stage": "rules"})

    if final_response.answer_source in {"llm", "hybrid"}:
        return final_response.model_copy(update={"pipeline_stage": "llm"})

    if final_response.answer_source == "rag":
        return final_response.model_copy(update={"pipeline_stage": "rag"})

    return final_response


def attach_product_recommendations(
    response: ChatResponse,
    product: Optional[Product],
    profile: Optional[Profile],
    profiles: Optional[List[Profile]] = None,
    message: str = "",
) -> ChatResponse:
    """Attach 2-3 repository products after one shared profile filter."""

    if product is not None and not product.facts_verified:
        return response
    diet_recommendation_requested = (
        response.intent == "general_diet_guide"
        and is_productless_diet_recommendation(message, None)
    )
    if response.intent not in {"can_i_eat", "alternative_food_recommend"} and not diet_recommendation_requested:
        return response
    if (
        response.intent == "can_i_eat"
        and response.allergy_status not in {"conflict", "cross_contact"}
        and response.risk_level not in {"high", "medium"}
    ):
        return response

    effective_profiles = profiles or ([profile] if profile else [])
    recommendations = recommend_catalog_products(
        message,
        effective_profiles,
        source_product=product,
        limit=3,
    )
    if recommendations:
        answer = response.answer
        if diet_recommendation_requested:
            answer = (
                "요청한 식이 조건에 대해 현재 DB에서 확인 가능한 후보를 골랐어요. "
                "제품의 명시된 특성과 활성 프로필의 알레르기·비선호 조건을 "
                "함께 대조했으며, 실제 포장지 표시는 섭취 전 다시 확인해 주세요."
            )
        elif response.intent == "alternative_food_recommend" and product is None:
            answer = (
                "요청한 조건에 맞는 DB 제품 후보를 표시했어요. "
                "활성 프로필의 알레르기·식이·비선호 조건을 먼저 확인했고, "
                "최신 포장지 표시는 섭취 전 다시 확인해 주세요."
            )
        return response.model_copy(update={
            "answer": answer,
            "recommended_products": recommendations[:3],
        })

    no_candidate_answer = (
        "요청한 조건으로 DB 제품을 확인했지만, 현재는 활성 프로필의 "
        "알레르기·식이 조건과 제품 표시 근거를 모두 확인할 수 있는 후보를 "
        "찾지 못했어요. 조건에 맞는 식품이 없다는 뜻은 아니에요. "
        "제품명이나 바코드를 알려주시면 해당 제품의 표시를 기준으로 다시 확인할게요."
        if product is None and (
            response.intent == "alternative_food_recommend"
            or diet_recommendation_requested
        )
        else (
            f"{response.answer}\n현재 DB에서는 모든 선택 프로필의 조건을 "
            "확인할 수 있는 대체 제품을 찾지 못했어요."
        )
    )
    return response.model_copy(update={
        "answer": no_candidate_answer,
        "reasons": list(dict.fromkeys([
            *response.reasons,
            "DB에서 조건 확인 가능한 대체 제품 없음",
        ])),
        "recommended_products": [],
    })



def _profile_allergy_findings(
    profile: Profile,
    product: Product,
) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    triggered = find_triggered_allergies(profile, product)
    directly_triggered = {allergy for allergy, _ in triggered}
    cross_contamination = [
        (allergy, aliases)
        for allergy, aliases in find_cross_contamination_allergies(profile, product)
        if allergy not in directly_triggered
    ]
    return triggered, cross_contamination


def _allergy_reason_parts(
    triggered: list[tuple[str, list[str]]],
    cross_contamination: list[tuple[str, list[str]]],
) -> list[str]:
    parts = []
    for allergy, matched_aliases in triggered:
        alias_preview = ", ".join(matched_aliases[:3])
        parts.append(f"프로필 알레르기 반응: {allergy} -> {alias_preview}")
    for allergy, matched_aliases in cross_contamination:
        alias_preview = ", ".join(matched_aliases[:3])
        parts.append(f"프로필 알레르기 교차접촉 가능: {allergy} -> {alias_preview}")
    return parts


def _allergy_alert_text(
    triggered: list[tuple[str, list[str]]],
    cross_contamination: list[tuple[str, list[str]]],
    *,
    profile_name: Optional[str] = None,
) -> str:
    alert_parts = []
    for allergy, matched_aliases in triggered:
        alias_preview = ", ".join(matched_aliases[:3])
        alert_parts.append(f"{allergy} -> {alias_preview}")
    for allergy, matched_aliases in cross_contamination:
        alias_preview = ", ".join(matched_aliases[:3])
        alert_parts.append(f"{allergy} 교차오염 가능 -> {alias_preview}")

    owner = f"'{profile_name}' 프로필" if profile_name else "프로필"
    if triggered:
        return (
            f"{owner} 알레르기 확인: 사전에 입력한 알레르기 중 "
            f"{'; '.join(alert_parts)} 성분이 이 제품에서 확인돼요. "
            "해당 프로필은 섭취를 피하는 편이 좋아요."
        )
    return (
        f"{owner} 알레르기 확인: 사전에 입력한 알레르기 중 "
        f"{'; '.join(alert_parts)} 표시가 있어 교차오염 가능성에 주의해야 해요."
    )


def _answer_has_visible_allergy_warning(
    answer: str,
    triggered: list[tuple[str, list[str]]],
    cross_contamination: list[tuple[str, list[str]]],
) -> bool:
    normalized = answer.lower()
    findings = [*triggered, *cross_contamination]
    mentions_every_finding = all(
        any(term.lower() in normalized for term in [allergy, *aliases] if term)
        for allergy, aliases in findings
    )
    has_warning_language = any(term in normalized for term in [
        "피하",
        "주의",
        "조심",
        "알레르기",
        "알러지",
        "교차오염",
        "섭취하지",
        "먹지",
    ])
    return mentions_every_finding and has_warning_language


def _raised_allergy_risk(
    current_risk: str,
    triggered: list[tuple[str, list[str]]],
    cross_contamination: list[tuple[str, list[str]]],
) -> str:
    if triggered:
        return "high"
    if cross_contamination and current_risk in {"low", "unknown"}:
        return "medium"
    return current_risk


def _profile_for_result(
    result: dict,
    profiles: List[Profile],
    result_index: Optional[int] = None,
) -> Optional[Profile]:
    result_name = str(result.get("profile_name") or "").strip()
    result_profile_id = result.get("profile_id")
    identity_matches = [
        candidate
        for candidate in profiles
        if (
            not result_name
            or candidate.profile_name == result_name
        )
        and (
            result_profile_id is None
            or candidate.profile_id == result_profile_id
        )
    ]
    if len(identity_matches) == 1:
        return identity_matches[0]

    # Multi-profile rule results are created in the same order as the active
    # profiles.  Name and database ID are not sufficient identifiers when two
    # active safety contexts legitimately share both values, so use that
    # trusted positional relationship only after checking that the public
    # identity fields do not contradict the candidate profile.
    if result_index is not None and 0 <= result_index < len(profiles):
        candidate = profiles[result_index]
        if candidate in identity_matches:
            return candidate
    return None


def attach_profile_allergy_alert(
    response: ChatResponse,
    product: Optional[Product],
    profile: Optional[Profile],
    profiles: Optional[List[Profile]] = None,
) -> ChatResponse:
    if response.task_type != "product_chat":
        return response
    if response.intent in {"profile_based_recheck", "food_compare"}:
        # A comparison already states each compared product's allergy result;
        # a single-product alert would describe whichever product was selected.
        return response
    if (
        product is None
        or product.product_found is False
        or not product.facts_verified
        or profile is None
    ):
        return response

    triggered, cross_contamination = _profile_allergy_findings(profile, product)
    if not triggered and not cross_contamination:
        return response

    reason_parts = _allergy_reason_parts(triggered, cross_contamination)
    reasons = list(dict.fromkeys([*response.reasons, *reason_parts]))
    risk_level = _raised_allergy_risk(
        response.risk_level,
        triggered,
        cross_contamination,
    )

    updated_per_profile_results = []
    for result_index, result in enumerate(response.per_profile_results):
        updated_result = dict(result)
        result_profile = _profile_for_result(
            updated_result,
            profiles or [],
            result_index=result_index,
        )
        if result_profile is not None:
            result_triggered, result_cross = _profile_allergy_findings(
                result_profile,
                product,
            )
            if result_triggered or result_cross:
                result_reasons = _allergy_reason_parts(result_triggered, result_cross)
                updated_result["reasons"] = list(dict.fromkeys([
                    *(updated_result.get("reasons") or []),
                    *result_reasons,
                ]))
                updated_result["risk_level"] = _raised_allergy_risk(
                    str(updated_result.get("risk_level") or "unknown"),
                    result_triggered,
                    result_cross,
                )
                result_answer = str(updated_result.get("answer") or "")
                result_has_visible_warning = _answer_has_visible_allergy_warning(
                    result_answer,
                    result_triggered,
                    result_cross,
                )
                result_alert = _allergy_alert_text(
                    result_triggered,
                    result_cross,
                    profile_name=result_profile.profile_name,
                )
                if (
                    result_alert not in result_answer
                    and (
                        not result_has_visible_warning
                    )
                ):
                    updated_result["answer"] = f"{result_alert}\n{result_answer}".strip()
        updated_per_profile_results.append(updated_result)

    if updated_per_profile_results:
        risk_level = max(
            [risk_level, *[
                str(result.get("risk_level") or "unknown")
                for result in updated_per_profile_results
            ]],
            key=lambda value: _RISK_PRIORITY.get(value, -1),
        )

    updates = {
        "reasons": reasons,
        "risk_level": risk_level,
        "per_profile_results": updated_per_profile_results,
    }

    alert = _allergy_alert_text(triggered, cross_contamination)
    if alert in response.answer:
        return response.model_copy(update=updates)
    if response.intent == "can_i_eat" and _answer_has_visible_allergy_warning(
        response.answer,
        triggered,
        cross_contamination,
    ):
        return response.model_copy(update=updates)

    return response.model_copy(update={
        "answer": f"{alert}\n{response.answer}",
        **updates,
    })


def _attach_turn_allergy_settings_guidance(
    response: ChatResponse,
    *,
    message: str,
    profiles: List[Profile],
    added_allergens: tuple[str, ...],
) -> ChatResponse:
    if not added_allergens:
        return response
    target = _target_profile_from_message(message, profiles)
    if target is None and len(profiles) == 1:
        target = profiles[0]
    return response.model_copy(update={
        "answer": (
            f"{response.answer}\n이번 질문에 적은 알레르기만 임시로 반영했어요. "
            "계속 적용하려면 설정에서 알레르기 정보를 변경해 주세요."
        ),
        "ui_guidance": {
            "code": "UPDATE_PROFILE_ALLERGIES",
            "message": (
                "이 알레르기를 계속 적용하려면 "
                "프로필 설정에서 추가해 주세요."
            ),
            "profile_id": (
                target.profile_id
                if (
                    target is not None
                    and isinstance(target.profile_id, int)
                    and not isinstance(target.profile_id, bool)
                    and target.profile_id > 0
                )
                else None
            ),
            "profile_name": target.profile_name if target is not None else None,
        },
    })


def build_allergy_clarification_response(
    response: ChatResponse, *, message: str, product: Optional[Product],
    profiles: List[Profile], known_profiles: Optional[List[Profile]] = None,
    state: Optional[dict] = None, prepared_conditions=None,
) -> ChatResponse:
    """Keep certain conflicts visible without assigning an uncertain condition."""
    prepared = prepared_conditions or prepare_food_conditions(message, profiles, state, known_profiles)
    message, state = prepared.message, prepared.state
    turn_context = prepared.allergy_context
    effective_profiles = prepared.profiles
    product = product if product is not None and product.facts_verified else None
    from app.intent.rules import rule_based_classify_intent
    intent = rule_based_classify_intent(message, product, merge_profiles(effective_profiles), [])
    if product is not None and any(
        find_triggered_allergies(p, product) or find_cross_contamination_allergies(p, product)
        for p in effective_profiles
    ):
        warning = build_rule_based_response(
            "can_i_eat", merge_profiles(effective_profiles), product, message, effective_profiles,
        )
        warning = attach_result_semantics(warning, product=product, profiles=effective_profiles, message=message)
        response = warning.model_copy(update={
            "answer": f"{warning.answer}\n\n{response.answer}",
            "reasons": [*warning.reasons, *response.reasons],
            "missing_information": [*warning.missing_information, *response.missing_information],
            "assessment_status": "incomplete", "recommended_products": [],
            "recommended_questions": [], "recipe": None,
        })
    from app.core.conversation_context_fields import carry_task_context
    previous_state = state or {}
    state = {**previous_state, "last_intent": intent, "last_task_type": response.task_type}
    if intent == "recipe_recommend":
        from app.services.recipe import _requested_diets
        state.update(recipe_requested_diets=_requested_diets([], message, [], state), recipe_goals_initialized=True)
    if product is not None:
        reference = internal_product_reference(product)
        if reference:
            state["last_confirmed_product"] = reference
            if intent == "recipe_recommend":
                state["recipe_product"] = reference
            for key in ("pending_product_candidates", "pending_user_message", "pending_intent"):
                state.pop(key, None)
        response = response.model_copy(update={
            "current_product": public_product_payload(product), "product_reference": reference,
        })
    state = carry_task_context(previous_state, state)
    if product is None:
        # A later "this product" must not bind an unresolved new target back to
        # an old confirmed item. Keep goals/history, not a stale product pointer.
        state.pop("last_confirmed_product", None)
        state.pop("recipe_product", None)
    response = response.model_copy(update={
        "intent": intent,
        "active_profile_names": active_profile_names(effective_profiles),
        "conversation_state": public_conversation_state(state), "internal_conversation_state": state,
    })
    response = attach_response_assessment(response, profiles=effective_profiles, product=product)
    return _attach_turn_allergy_settings_guidance(
        response, message=prepared.allergy_message, profiles=effective_profiles,
        added_allergens=() if turn_context.target_ambiguous else turn_context.added_allergens,
    )


def _vegetarian_clarification_response(message, product, profiles, state, history, added_allergens):
    """Ask only about unresolved conditions; do not erase known label conflicts."""
    from app.intent.rules import rule_based_classify_intent
    profile = merge_profiles(profiles)
    intent = rule_based_classify_intent(message, product, profile, history or [])
    clarification = (
        "유제품·달걀 허용 여부 또는 누구의 조건인지 확인이 필요해요. "
        "적용할 프로필 이름과 아직 정하지 않은 허용 범위를 알려 주세요."
    )
    entries = {item["profile_key"]: item for item in state.get("vegetarian_allowances", [])}
    pending = [
        f"{p.profile_name} 프로필의 {label} 허용 여부"
        for p in profiles
        for field, label in (("dairy", "유제품"), ("egg", "달걀"))
        if entries.get(profile_key(p), {}).get(f"{field}_pending") is True
    ]
    if pending:
        clarification = f"{', '.join(pending)}를 확인해야 해요. 해당 항목을 허용할지 제외할지 알려 주세요."
    if any(item.get(field) is False for item in state.get("vegetarian_allowances", []) for field in ("dairy", "egg")):
        clarification += " 이미 명확히 말씀한 제외 조건은 유지했어요."
    response = ChatResponse(
        intent=intent, answer=clarification, risk_level="unknown", assessment_status="incomplete",
        active_profile_names=active_profile_names(profiles),
    )
    if product is not None and product.facts_verified and any(
        find_triggered_allergies(p, product) or find_cross_contamination_allergies(p, product)
        for p in profiles
    ):
        response = build_rule_based_response("can_i_eat", profile, product, message, profiles)
        response = attach_result_semantics(response, product=product, profiles=profiles, message=message)
        response = response.model_copy(update={
            "intent": intent, "answer": f"{response.answer}\n\n{clarification}",
            "recommended_products": [], "recipe": None, "assessment_status": "incomplete",
        })
    state = {**state, "last_intent": intent, "last_task_type": response.task_type}
    if intent == "recipe_recommend":
        from app.services.recipe import _requested_diets
        state.update(recipe_requested_diets=_requested_diets([], message, history, state), recipe_goals_initialized=True)
    if product is not None and product.facts_verified and product.product_found is not False:
        # The catalog selection has completed even though the dietary answer
        # has not. Keeping this list would ask the user to choose it again.
        for key in ("pending_product_candidates", "pending_user_message", "pending_intent"):
            state.pop(key, None)
        reference = {key: value for key, value in {
            "product_name": product.product_name, "barcode": product.barcode, "report_number": product.report_number,
        }.items() if value is not None}
        if product.barcode or product.report_number:
            state["last_confirmed_product"] = reference
            if intent == "recipe_recommend":
                state["recipe_product"] = reference
        response = response.model_copy(update={"current_product": public_product_payload(product)})
    response = response.model_copy(update={
        "missing_information": [*response.missing_information, *(pending or ["채식 허용 범위를 적용할 프로필"])],
        "conversation_state": state, "internal_conversation_state": state,
    })
    response = attach_response_assessment(response, profiles=profiles, product=product)
    return _attach_turn_allergy_settings_guidance(
        response, message=message, profiles=profiles, added_allergens=added_allergens,
    )


def generate_chat_response(
    profile: Optional[Profile],
    product: Optional[Product],
    message: str,
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
    conversation_state: Optional[dict] = None,
    known_profiles: Optional[List[Profile]] = None,
    prepared_conditions=None,
) -> ChatResponse:
    from app.intent.request_semantics import current_topic_message
    message = current_topic_message(message)
    known_profiles = known_profiles if known_profiles is not None else [
        *([profile] if profile is not None else []), *(active_profiles or []),
    ]
    from app.services.comparison_language import split_comparison_cancellation, clear_comparison_context
    message, comparison_cancelled = split_comparison_cancellation(message)
    if comparison_cancelled:
        conversation_state = clear_comparison_context(conversation_state)
        if not message:
            return ChatResponse(
                intent="unknown", answer="제품 비교를 종료했어요. 새로 궁금한 제품이나 레시피를 알려 주세요.",
                risk_level="unknown", conversation_state=conversation_state, internal_conversation_state=conversation_state,
            )
    effective_profiles = active_profiles_from_inputs(profile, active_profiles)
    emergency_response = build_emergency_triage_response(
        message,
        profiles=effective_profiles,
        has_food_context=bool(product or (conversation_state or {}).get("last_confirmed_product")),
    )
    if emergency_response is not None:
        # Defense in depth for callers that invoke this service directly. The
        # public API performs the same check even earlier, before product lookup.
        return attach_response_assessment(
            emergency_response,
            profiles=effective_profiles,
            product=None,
        )

    from app.services.request_language import standalone_cancellation_response, request_lifecycle, request_state_after_action
    from app.services.conversation_controls import conversation_control_response, history_after_reset
    chat_history = history_after_reset(chat_history)
    control = conversation_control_response(message, conversation_state)
    if control is not None:
        return attach_response_assessment(control, profiles=effective_profiles, product=None)
    current_condition_message = message
    lifecycle = request_lifecycle(message)
    if lifecycle.remaining_message:
        message = lifecycle.remaining_message
        conversation_state = request_state_after_action(lifecycle, conversation_state)
        if lifecycle.cancel or lifecycle.cancel_search:
            chat_history = []
    cancellation = standalone_cancellation_response(message, conversation_state,
                                                    profiles=effective_profiles, known_profiles=known_profiles)
    if cancellation is not None:
        return attach_response_assessment(cancellation, profiles=effective_profiles, product=None)
    from app.services.condition_subject import unresolved_profile_target_response
    target_response = unresolved_profile_target_response(
        message, effective_profiles, known_profiles, conversation_state,
    )
    if target_response is not None:
        return attach_response_assessment(target_response, profiles=effective_profiles, product=None)
    prepared = prepared_conditions or prepare_food_conditions(
        message, effective_profiles, conversation_state, known_profiles,
        current_condition_message=current_condition_message,
    )
    message, conversation_state = prepared.message, prepared.state
    message_constraint_response = prepared.allergy_guard
    if message_constraint_response is not None:
        return build_allergy_clarification_response(
            message_constraint_response, message=message, product=product,
            profiles=effective_profiles, known_profiles=known_profiles, state=conversation_state,
            prepared_conditions=prepared,
        )

    turn_allergy_context = prepared.allergy_context
    effective_profiles = prepared.profiles
    ambiguous_vegetarian = prepared.ambiguous_diet
    # Unresolved diet choices block personal suitability/recommendations, not
    # independent label facts. Classify locally so this boundary cannot invoke
    # an LLM with unresolved conditions.
    from app.intent.rules import rule_based_classify_intent
    clarification_intent = rule_based_classify_intent(
        message, product, merge_profiles(effective_profiles), [] if prepared.resuming_request else (chat_history or []),
    ) if ambiguous_vegetarian else None
    facts_during_clarification = ambiguous_vegetarian and clarification_intent in {
        "nutrition_explain", "ingredient_check", "product_summary",
    }
    if ambiguous_vegetarian and not facts_during_clarification:
        return _vegetarian_clarification_response(
            message, product, effective_profiles, conversation_state, chat_history,
            turn_allergy_context.added_allergens,
        )

    from app.services.recipe_followup import recipe_followup_response
    recipe_followup = recipe_followup_response(message, conversation_state, effective_profiles)
    if recipe_followup is not None and not ambiguous_vegetarian:
        return attach_response_assessment(recipe_followup, profiles=effective_profiles, product=None)

    effective_profile = merge_profiles(effective_profiles) or profile
    from app.intent.request_semantics import missing_nutrient_check, food_recommendation_request
    if product is not None and missing_nutrient_check(message):
        from app.intent.text import detect_nutrition_keywords
        if not detect_nutrition_keywords(message):
            # Only recover the user's last requested field, never a value from
            # assistant prose or an earlier product's nutritional facts.
            for item in reversed(chat_history or []):
                if item.role == "user":
                    names = detect_nutrition_keywords(item.content)
                    if names:
                        message = "와 ".join(names) + " " + message
                    break
    if not effective_profiles and food_recommendation_request(message) and re.search(r"선택된.*기준", message):
        return ChatResponse(intent="unknown", answer="현재 ON인 프로필이 없어요. 추천 기준으로 사용할 프로필을 선택한 뒤 다시 요청해 주세요.",
                            risk_level="unknown", overall_decision="check_required",
                            conversation_state=conversation_state, internal_conversation_state=conversation_state)
    from app.services.comparison_language import comparison_axis_reply
    previous_product = conversation_state.get("last_confirmed_product") or {}
    if conversation_state.get("last_intent") == "alternative_food_recommend" and food_recommendation_request(message):
        from app.services.product import recommendation_category_hint
        if not recommendation_category_hint(message):
            for item in reversed((chat_history or [])[-12:]):
                if item.role == "user":
                    category = recommendation_category_hint(item.content)
                    if category:
                        message = f"{category} {message}"
                        break
    changed_product = product is not None and any(
        previous_product.get(key) and getattr(product, key) and previous_product[key] != getattr(product, key)
        for key in ("barcode", "report_number")
    )
    if changed_product and comparison_axis_reply(message):
        conversation_state = clear_comparison_context(conversation_state)
    if (conversation_state.get("comparison_products")
            and conversation_state.get("comparison_status") != "cleared"
            and conversation_state.get("last_intent") in (None, "food_compare")):
        axis = comparison_axis_reply(message)
        if axis:
            message = f"{axis}으로 비교해줘"
    intent = clarification_intent if facts_during_clarification else extract_user_intent(
        message=message,
        product=product,
        profile=effective_profile,
        active_profiles=effective_profiles,
        # A short clarification resumes a complete request, not an implicit
        # follow-up to the assistant's ingredient/selection explanation.
        chat_history=[] if prepared.resuming_request else chat_history,
    )
    from app.services.comparison_language import comparison_relative_reply, replacement_targets
    if (product is None and not facts_during_clarification
            and conversation_state.get("comparison_status") in {"cleared", "active"}
            and (comparison_relative_reply(message) or replacement_targets(message))):
        # Keep an unfinished comparison understandable after cancellation,
        # while the comparison resolver still requires new targets.
        intent = "food_compare"
    from app.services.operational_policy import team_feature_unavailable_response
    unavailable = team_feature_unavailable_response(intent, product)
    if unavailable is not None:
        # Team mode blocks this feature: answer before the recipe or
        # recommendation search runs, instead of rewriting its reply later.
        return attach_response_assessment(unavailable, profiles=effective_profiles, product=None)
    rule_response = build_rule_based_response(
        intent=intent,
        profile=effective_profile,
        product=product,
        message=message,
        active_profiles=effective_profiles,
        chat_history=chat_history,
        conversation_state=conversation_state,
    )
    from app.services.product_answer_format import complete_product_facts, concise_product_answer
    rule_response = complete_product_facts(rule_response, product, effective_profile, message)
    if intent == "recipe_recommend":
        from app.services.recipe import _requested_diets
        recipe_state = rule_response.conversation_state.model_dump(exclude_none=True)
        # Persist message goals only, not stored profile diets or turn allergies.
        recipe_state["recipe_requested_diets"] = _requested_diets([], message, chat_history, conversation_state)
        recipe_state["recipe_goals_initialized"] = True
        if rule_response.recipe is not None:
            from app.services.recipe_followup import recipe_receipt
            recipe_state["recipe_receipt"] = recipe_receipt(rule_response.recipe, effective_profiles, recipe_state["recipe_requested_diets"])
        if product is not None and product.facts_verified and (product.barcode or product.report_number):
            recipe_state["recipe_product"] = {key: value for key, value in {
                "barcode": product.barcode, "report_number": product.report_number, "product_name": product.product_name,
            }.items() if value is not None}
        rule_response = rule_response.model_copy(update={"conversation_state": recipe_state})
    if facts_during_clarification:
        # Rules-only factual output; no recipe, substitutes or external rewrite
        # may turn an unresolved allowance into a recommendation.
        final_response = attach_profile_allergy_alert(
            rule_response, product=product, profile=effective_profile, profiles=effective_profiles,
        )
        final_response = final_response.model_copy(update={
            "answer": final_response.answer + "\n채식 허용 범위는 아직 확인 중이에요. 위 내용은 제품 표시 정보이며 개인 식이 적합성 판단은 보류했어요.",
            "assessment_status": "incomplete", "recommended_products": [], "recipe": None,
            "missing_information": [*final_response.missing_information, "미확인 채식 허용 범위"],
        })
    else:
        final_response = generate_final_response(
            rule_response=rule_response,
            message=message,
            profile=effective_profile,
            product=product,
            active_profiles=effective_profiles,
            chat_history=chat_history,
        )
    if final_response.product_candidates:
        from app.core.conversation_context_fields import carry_task_context
        next_state = carry_task_context(conversation_state, final_response.conversation_state.model_dump(exclude_none=True))
        final_response = final_response.model_copy(update={
            "conversation_state": next_state,
            "internal_conversation_state": carry_task_context(conversation_state, final_response.internal_conversation_state or next_state),
        })
        return attach_response_assessment(final_response, profiles=effective_profiles, product=None)
    previous_state = dict(conversation_state or {})
    previous_fingerprints = (
        conversation_state.get("recipe_fingerprints", [])
        if isinstance(conversation_state, dict)
        else []
    )
    conversation_state = {
        "version": 1,
        "last_intent": final_response.intent,
        "last_task_type": final_response.task_type,
    }
    task_state = final_response.conversation_state.model_dump(exclude_none=True, exclude_defaults=True)
    from app.core.conversation_context_fields import carry_task_context
    conversation_state = carry_task_context(previous_state, conversation_state)
    conversation_state = carry_task_context(conversation_state, task_state)
    conversation_state.update(last_intent=final_response.intent, last_task_type=final_response.task_type)
    if intent == "recipe_recommend":
        # An explicit fresh recipe request without goals resets old message goals.
        conversation_state["recipe_requested_diets"] = recipe_state["recipe_requested_diets"]
        # A failed/new request must not replay the preceding successful dish.
        conversation_state.pop("recipe_receipt", None)
        if recipe_state.get("recipe_receipt"):
            conversation_state["recipe_receipt"] = recipe_state["recipe_receipt"]
        if product is None:
            conversation_state.pop("recipe_product", None)
    next_fingerprints = final_response.conversation_state.recipe_fingerprints
    if next_fingerprints:
        conversation_state["recipe_fingerprints"] = next_fingerprints
    elif previous_fingerprints:
        conversation_state["recipe_fingerprints"] = previous_fingerprints
    if (
        product is not None
        and product.product_found is not False
        and product.facts_verified
    ):
        product_payload = public_product_payload(product)
        product_reference = {
            key: value
            for key, value in {
                "product_name": product.product_name,
                "barcode": product.barcode,
                "report_number": product.report_number,
            }.items()
            if value is not None
        }
        if product.barcode or product.report_number:
            conversation_state["last_confirmed_product"] = product_reference
        response = final_response.model_copy(update={
            "current_product": product_payload,
            "product_reference": internal_product_reference(product),
            "conversation_state": conversation_state,
            "internal_conversation_state": {
                **conversation_state,
                **(
                    {
                        "last_confirmed_product": internal_product_reference(
                            product
                        )
                    }
                    if internal_product_reference(product) is not None
                    else {}
                ),
            },
        })
        response = attach_response_assessment(
            response,
            profiles=effective_profiles,
            product=product,
        )
        response = attach_result_semantics(
            response,
            product=product,
            profiles=effective_profiles,
            message=message,
        )
        if response.intent == "unknown" and response.allergy_status in {"conflict", "cross_contact"}:
            response = attach_response_assessment(response, profiles=effective_profiles, product=product)
        if facts_during_clarification:
            response = attach_response_assessment(
                _defer_pending_diet_decision(response), profiles=effective_profiles, product=product,
            )
        return _attach_turn_allergy_settings_guidance(
            concise_product_answer(response, product, effective_profiles, message),
            message=prepared.allergy_message,
            profiles=effective_profiles,
            added_allergens=turn_allergy_context.added_allergens,
        )

    response = final_response.model_copy(update={"conversation_state": conversation_state})
    response = attach_response_assessment(
        response,
        profiles=effective_profiles,
        product=product,
    )
    response = attach_result_semantics(
        response,
        product=product,
        profiles=effective_profiles,
        message=message,
    )
    if facts_during_clarification:
        response = attach_response_assessment(
            _defer_pending_diet_decision(response), profiles=effective_profiles, product=product,
        )
    return _attach_turn_allergy_settings_guidance(
        response,
        message=prepared.allergy_message,
        profiles=effective_profiles,
        added_allergens=turn_allergy_context.added_allergens,
    )


def _defer_pending_diet_decision(response: ChatResponse) -> ChatResponse:
    """Label information is not confirmation of an unresolved diet choice."""
    def deferred_fields(diet_match, decision):
        return {
            "diet_match": diet_match if diet_match == "not_match" else "unknown",
            "overall_decision": decision if decision in {"avoid", "caution"} else "check_required",
        }

    return response.model_copy(update={
        **deferred_fields(response.diet_match, response.overall_decision),
        "per_profile_results": [
            {
                **dict(item), **deferred_fields(item.diet_match, item.overall_decision),
                "assessment_status": "incomplete",
            }
            for item in response.per_profile_results
        ],
    })
