from typing import Callable, List, Optional

from app.intent.classifier import classify_intent_result
from app.services.hybrid import enhance_rule_response, try_general_llm_fallback
from app.core.models import ChatHistoryItem, ChatResponse, Product, Profile
from app.services.product import recommend_alternative_products
from app.services.profile_context import (
    active_profile_names,
    active_profiles_from_inputs,
    build_multi_profile_answer,
    highest_risk_level,
    merge_profiles,
)
from app.services.handlers import (
    handle_can_i_eat,
    handle_general_allergy_guide,
    handle_general_diet_guide,
    handle_general_food_health,
    handle_general_nutrition_guide,
    handle_health_risk_check,
    handle_ingredient_check,
    handle_missing_product,
    handle_nutrition_explain,
    handle_product_summary,
    handle_profile_based_recheck,
    handle_unknown,
)


IntentHandler = Callable[[Optional[Profile], Optional[Product], str], ChatResponse]


def extract_user_intent(
    message: str,
    product: Optional[Product],
    profile: Optional[Profile],
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
) -> str:
    classification_profile = merge_profiles(
        active_profiles_from_inputs(profile, active_profiles)
    ) or profile
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
) -> ChatResponse:
    product_handlers: dict[str, IntentHandler] = {
        "missing_product": handle_missing_product,
        "ingredient_check": handle_ingredient_check,
        "can_i_eat": handle_can_i_eat,
        "nutrition_explain": handle_nutrition_explain,
        "health_risk_check": handle_health_risk_check,
        "profile_based_recheck": handle_profile_based_recheck,
        "product_summary": handle_product_summary,
    }

    general_handlers: dict[str, IntentHandler] = {
        "general_food_health": handle_general_food_health,
        "general_allergy_guide": handle_general_allergy_guide,
        "general_diet_guide": handle_general_diet_guide,
        "general_nutrition_guide": handle_general_nutrition_guide,
        "unknown": handle_unknown,
    }

    profiles = active_profiles_from_inputs(profile, active_profiles)

    if len(profiles) > 1 and intent in {"can_i_eat", "health_risk_check"} and product is not None:
        individual_responses = [
            product_handlers[intent](active_profile, product, message)
            for active_profile in profiles
        ]
        combined_answer = build_multi_profile_answer(profiles, individual_responses)
        combined_reasons = [
            f"{active_profile.profile_name}: {reason}"
            for active_profile, response in zip(profiles, individual_responses)
            for reason in response.reasons
        ]
        per_profile_results = [
            {
                "profile_id": active_profile.profile_id,
                "profile_name": active_profile.profile_name,
                "target_type": active_profile.target_type,
                "intent": response.intent,
                "risk_level": response.risk_level,
                "answer": response.answer,
                "reasons": response.reasons,
            }
            for active_profile, response in zip(profiles, individual_responses)
        ]
        merged_profile = merge_profiles(profiles)
        return ChatResponse(
            task_type=individual_responses[0].task_type,
            answer_source="rules",
            pipeline_stage="facts",
            intent=intent,
            answer=combined_answer,
            risk_level=highest_risk_level(individual_responses),
            reasons=combined_reasons,
            profile_name=merged_profile.profile_name if merged_profile else None,
            active_profile_names=active_profile_names(profiles),
            per_profile_results=per_profile_results,
        )

    if intent in product_handlers:
        if product is None and intent != "missing_product":
            return handle_unknown(profile, product, message)
        response = product_handlers[intent](profile, product, message)
        return response.model_copy(update={
            "pipeline_stage": "facts",
            "active_profile_names": active_profile_names(profiles),
        })

    if intent in general_handlers:
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
    effective_profiles = active_profiles_from_inputs(profile, active_profiles)
    effective_profile = merge_profiles(effective_profiles) or profile

    if rule_response.task_type == "product_chat":
        final_response = enhance_rule_response(
            rule_response,
            message,
            effective_profile,
            product,
            profiles=effective_profiles,
            chat_history=chat_history,
        )
    else:
        final_response = try_general_llm_fallback(
            rule_response,
            message,
            effective_profile,
            product,
            chat_history=chat_history,
        )

    final_response = attach_product_recommendations(
        final_response,
        product=product,
        profile=effective_profile,
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
) -> ChatResponse:
    if response.task_type != "product_chat":
        return response
    if response.intent not in {"can_i_eat", "health_risk_check"}:
        return response
    if response.risk_level not in {"high", "medium"}:
        return response

    recommendations = recommend_alternative_products(product=product, profile=profile)
    if not recommendations:
        return response

    return response.model_copy(update={"recommended_products": recommendations})


def generate_chat_response(
    profile: Optional[Profile],
    product: Optional[Product],
    message: str,
    active_profiles: Optional[List[Profile]] = None,
    chat_history: Optional[list[ChatHistoryItem]] = None,
) -> ChatResponse:
    effective_profiles = active_profiles_from_inputs(profile, active_profiles)
    effective_profile = merge_profiles(effective_profiles) or profile
    intent = extract_user_intent(
        message=message,
        product=product,
        profile=effective_profile,
        active_profiles=effective_profiles,
        chat_history=chat_history,
    )
    rule_response = build_rule_based_response(
        intent=intent,
        profile=effective_profile,
        product=product,
        message=message,
        active_profiles=effective_profiles,
    )
    final_response = generate_final_response(
        rule_response=rule_response,
        message=message,
        profile=effective_profile,
        product=product,
        active_profiles=effective_profiles,
        chat_history=chat_history,
    )
    conversation_state = {
        "last_intent": final_response.intent,
        "last_task_type": final_response.task_type,
    }
    if product is not None and product.product_found is not False:
        product_payload = product.model_dump(exclude_none=True)
        conversation_state["last_confirmed_product"] = product_payload
        return final_response.model_copy(update={
            "current_product": product_payload,
            "conversation_state": conversation_state,
        })

    return final_response.model_copy(update={"conversation_state": conversation_state})
