"""Keep useful upstream replies without granting trust to an unverified catalog.

Product answers already use deterministic, provenance-aware handlers. Preserve
that bounded path; free LLM product rewrites and affirmative suitability claims
remain outside the team contract. General LLM/RAG explanations keep their own
grounding checks and request-consent boundary.
"""
import re

from app.core.models import ChatResponse
from app.services.conversation_controls import CONVERSATION_CONTROL_REASON
from app.services.hybrid import LOCAL_EXCERPT_REASON, _contains_reassuring_conclusion
from app.services.product_status_handlers import handle_product_summary
from app.services.response_assessment import attach_response_assessment


PRODUCT_INTENTS = {
    'product_summary', 'can_i_eat', 'profile_based_recheck',
    'ingredient_check', 'nutrition_explain',
}
GENERAL_INTENTS = {'general_diet_guide', 'general_allergy_guide', 'general_nutrition_guide', 'out_of_domain'}
# Replies that only ask which product is meant or explain that the question was
# unclear; they state no catalog fact.
NO_PRODUCT_GUIDANCE_INTENTS = {'missing_product', 'unknown'}
STAGE_C_INTENTS = {'recipe_recommend', 'alternative_food_recommend'}
# Follow-up chips that would lead into a Stage C reply.
STAGE_C_QUESTION = re.compile(r'레시피|요리법|만드는\s*법|추천|대체|대안')
COMPARISON_SOURCE_NOTE = '비교에 쓴 상품 표시 정보는 출처 미확인 상태예요. 섭취 전 실제 포장지 표시를 확인해 주세요.'
COMPARISON_UNAVAILABLE_ANSWER = (
    '출처가 확인되지 않은 상품 정보로는 알레르기 충돌이 없다고 비교해 드릴 수 없어요. '
    '각 제품을 선택하면 등록된 원재료·알레르기 표시를 확인해 드릴게요.'
)
RECIPE_UNAVAILABLE_ANSWER = (
    '지금은 상품 정보의 출처를 확인할 수 없어 레시피 추천을 제공하지 않아요. '
    '제품의 원재료·알레르기·영양성분 확인은 도와드릴 수 있어요.'
)
RECOMMENDATION_UNAVAILABLE_ANSWER = (
    '지금은 상품 정보의 출처를 확인할 수 없어 상품 추천을 제공하지 않아요. '
    '궁금한 제품명이나 바코드를 알려 주시면 등록된 원재료·알레르기 표시를 확인해 드릴게요.'
)


def _no_positive_decision(response):
    return (response.overall_decision in {'avoid', 'caution', 'check_required'}
            and response.risk_level != 'low'
            and response.allergy_status != 'no_known_match'
            and response.diet_match != 'match'
            and (response.assessment_summary is None
                 or response.assessment_summary.decision != 'no_conflict_found'))


def _bounded_verdict(response):
    # The upstream wording detector reads the noun phrase '섭취 가능 여부'
    # ("whether it can be eaten", e.g. '섭취 가능 여부를 확정하지 않았어요' or
    # '섭취 가능 여부를 안내하는 챗봇') as reassurance. It never asserts that a
    # food is edible, so neutralize only that phrase, never other text.
    claim_text = re.sub(r'섭취\s*가능\s*여부', '섭취 여부', response.answer)
    return _no_positive_decision(response) and not _contains_reassuring_conclusion(claim_text)


def _authored_text(response):
    # Fixed rule templates and approved excerpts are reviewed wording; the
    # reassurance detector exists for generated text and misreads advice such
    # as '동의어까지 확인하는 편이 안전해요'.
    return response.answer_source == 'rules' or response.fallback_reason == LOCAL_EXCERPT_REASON


def restrict_team_catalog_response(response: ChatResponse, *, resolved_product=None, external_enabled=False):
    if response.task_type == 'safety_triage' or response.intent == 'emergency_safety':
        return response
    if (response.fallback_reason == CONVERSATION_CONTROL_REASON and response.answer_source == 'rules'
            and not any((response.recommended_products, response.comparison_products, response.recipe,
                         response.product_candidates, response.current_product))):
        # Greeting, help and reset replies carry no product fact or suitability
        # claim, so replacing them with a product-search prompt only confuses.
        return response
    if (response.backend_analysis is not None
            and response.backend_analysis.application == 'product_decision'
            and response.answer_source == 'backend'):
        # v2 explains authenticated backend verdicts. Do not silently replace
        # PASS/DANGER/UNKNOWN or discard profileKey/contextId with legacy rules.
        # Backend PASS means no detected conflict, not verified label provenance.
        note = '상품 표시 출처는 미확인 상태이며, 위 백엔드 판정은 실제 섭취 안전을 보증하지 않습니다.'
        return response.model_copy(update={'answer': response.answer + '\n' + note})
    if (response.backend_analysis is not None
            and response.backend_analysis.application in {'context_only', 'product_selection_required'}
            and response.answer_source == 'rules' and _bounded_verdict(response)
            and response.intent not in STAGE_C_INTENTS
            and not any((response.recommended_products, response.comparison_products, response.recipe))):
        # A new/ambiguous allergy or a different product requires a specific
        # clarification, not a generic request to search the already selected item.
        # Recipe and recommendation replies stay Stage C even when they found
        # nothing: their own wording offers features the team filter blocks.
        return response
    summary = response.assessment_summary
    product_resolved = (resolved_product is not None and resolved_product.product_found
                        and resolved_product.facts_verified)
    no_nested_assessments = not any((response.recommended_products, response.comparison_products,
                                     response.recipe, response.per_profile_results))

    # Stage A: no catalog fact and no personal verdict (general explanations,
    # out-of-domain and "which product?" guidance). Nothing here depends on the
    # unverified catalog, so it is kept instead of a product-search prompt.
    if (resolved_product is None and response.intent in GENERAL_INTENTS | NO_PRODUCT_GUIDANCE_INTENTS
            and (response.answer_source == 'rules' or external_enabled
                 or response.fallback_reason == LOCAL_EXCERPT_REASON)
            and summary is not None and not summary.safety_assessed
            and (summary.scope == 'general_guidance' or response.intent in NO_PRODUCT_GUIDANCE_INTENTS)
            and (_bounded_verdict(response) if not _authored_text(response) else _no_positive_decision(response))
            and no_nested_assessments and not response.current_product and not response.product_candidates):
        return response

    # Stage B: comparing registered label facts is product information, like a
    # single-product summary, provided it carries no reassurance.
    if (response.intent == 'food_compare' and response.comparison_products
            and response.answer_source == 'rules' and _bounded_verdict(response)):
        return response.model_copy(update={
            'answer': response.answer + '\n' + COMPARISON_SOURCE_NOTE,
            'recommended_products': [], 'recipe': None, 'product_candidates': [],
        })

    if (product_resolved and response.intent in PRODUCT_INTENTS
            and response.answer_source == 'rules' and _bounded_verdict(response)):
        # The existing deterministic handlers already explain missing lineage,
        # registered facts, and concrete profile conflicts. Do not erase them.
        # Nested positive recommendations are a separate feature and cannot
        # inherit this permission merely because the main product is bounded.
        return response.model_copy(update={
            'recommended_products': [], 'comparison_products': [], 'recipe': None,
            'product_candidates': [],
        })

    candidates = [candidate.model_copy(update={
        'reason': '출처 미확인 검색 후보입니다. 이름·제조사·포장 정보를 비교해 선택해 주세요.',
        'reasons': [],
    }) for candidate in response.product_candidates]
    if candidates:
        answer = '검색된 상품 중 질문하려는 제품을 선택해 주세요. 같은 이름의 다른 제품일 수 있어 제조사와 포장도 확인해 주세요.'
    elif response.intent == 'recipe_recommend' or response.recipe is not None:
        # Stage C stays blocked, but say so instead of asking for a product.
        answer = RECIPE_UNAVAILABLE_ANSWER
    elif response.intent == 'alternative_food_recommend' or response.recommended_products:
        answer = RECOMMENDATION_UNAVAILABLE_ANSWER
    elif response.intent == 'food_compare':
        answer = COMPARISON_UNAVAILABLE_ANSWER
    elif product_resolved:
        # A rejected rewrite still gets the team's existing factual summary,
        # generated only from the repository-resolved product, not client data.
        factual = handle_product_summary(None, resolved_product, '등록 정보 요약')
        answer = factual.answer + '\n\n출처 미확인 정보이므로 섭취 가능 여부는 확정하지 않았어요.'
    else:
        answer = '질문할 상품을 검색해 선택한 뒤 다시 물어봐 주세요. 상품 상세 화면의 ‘이 상품 챗봇에 질문’을 이용할 수 있어요.'
        if (response.intent in PRODUCT_INTENTS or response.current_product or summary is not None
                or response.task_type in {'product_chat', 'product_assessment'}):
            answer += '\n선택 전 정보나 출처 미확인 정보로는 섭취 가능 여부를 확정할 수 없어요.'

    warning = response.overall_decision in {'avoid', 'caution'}
    if warning:
        answer = '등록 정보에 주의 신호가 있어요. 섭취 전에 해당 표시를 확인해 주세요.\n\n' + answer
    bounded = ChatResponse(
        response_id=response.response_id, generated_at=response.generated_at,
        task_type=response.task_type, intent=response.intent, answer_source='rules', answer=answer,
        overall_decision=response.overall_decision if warning else 'check_required',
        risk_level=response.risk_level if warning and response.risk_level in {'high', 'medium'} else 'unknown',
        allergy_status=response.allergy_status if response.allergy_status in {'conflict', 'cross_contact'} else 'unknown',
        assessment_status='incomplete', missing_information=['상품 정보의 출처 미확인'],
        product_candidates=candidates,
        profile_name=response.profile_name,
        active_profile_names=response.active_profile_names,
        per_profile_results=response.per_profile_results,
        conversation_state=response.conversation_state,
        internal_conversation_state=response.internal_conversation_state,
        fallback_reason='team_catalog_bounded_reply',
        backend_analysis=response.backend_analysis,
        policy_version=response.policy_version,
    )
    return attach_response_assessment(bounded, product=resolved_product if product_resolved else None)
