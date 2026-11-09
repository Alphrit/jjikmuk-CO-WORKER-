"""Opt-in unverified-catalog policy and operational state readiness.

This does not register sources, grant consent, or change backend v2 verdicts.
"""
import os
import re

from app.infrastructure import external_ai_state

# Team mode never builds recipes or product recommendations from the
# unverified catalog (Stage C). Decided here once, before any handler runs, so
# handlers, follow-up chips and the final filter agree.
STAGE_C_INTENTS = frozenset({'recipe_recommend', 'alternative_food_recommend'})
STAGE_C_QUESTION = re.compile(r'레시피|요리법|만드는\s*법|추천|대체|대안')
TEAM_FEATURE_UNAVAILABLE_REASON = 'team_feature_unavailable'
RECIPE_UNAVAILABLE_ANSWER = (
    '지금은 상품 정보의 출처를 확인할 수 없어 레시피 추천을 제공하지 않아요. '
    '제품의 원재료·알레르기·영양성분 확인은 도와드릴 수 있어요.'
)
RECOMMENDATION_UNAVAILABLE_ANSWER = (
    '지금은 상품 정보의 출처를 확인할 수 없어 상품 추천을 제공하지 않아요. '
    '궁금한 제품명이나 바코드를 알려 주시면 등록된 원재료·알레르기 표시를 확인해 드릴게요.'
)


def _enabled(name):
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def unverified_catalog_enabled():
    return _enabled('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG')


def leads_to_stage_c(question):
    """A follow-up chip that, tapped, asks for a recipe or a recommendation.

    Wording alone misses '비슷한 제품 중에서 더 나은 것도 알려줘', so the chip is
    also classified the way it will be when tapped.
    """
    from app.intent.rules import rule_based_classify_intent
    return bool(STAGE_C_QUESTION.search(question)) or rule_based_classify_intent(question, None, None, []) in STAGE_C_INTENTS


def team_blocks_question(question):
    return unverified_catalog_enabled() and leads_to_stage_c(question)


def team_feature_unavailable_response(intent, product):
    """The final answer for a blocked feature, before its handler runs.

    Only without a product: with a selected product the recommendation path
    still computes the registered-conflict warning the team filter keeps.
    """
    if product is not None or intent not in STAGE_C_INTENTS or not unverified_catalog_enabled():
        return None
    from app.core.models import ChatResponse
    answer = RECIPE_UNAVAILABLE_ANSWER if intent == 'recipe_recommend' else RECOMMENDATION_UNAVAILABLE_ANSWER
    return ChatResponse(
        intent=intent, task_type='general_chat', answer_source='rules', pipeline_stage='rules', answer=answer,
        risk_level='unknown', overall_decision='check_required', assessment_status='incomplete',
        missing_information=['상품 정보의 출처 미확인'], reasons=['팀 모드에서 제공하지 않는 기능'],
        fallback_reason=TEAM_FEATURE_UNAVAILABLE_REASON,
        conversation_state={'version': 1, 'last_intent': intent, 'last_task_type': 'general_chat'},
    )


def deployment_errors(*, external_enabled, conversations_enabled):
    errors = []
    if unverified_catalog_enabled():
        if conversations_enabled or (external_enabled and not _enabled('FOOD_CHATBOT_TEAM_EXTERNAL_AI')):
            errors.append('team_unverified_catalog_requires_stateless_rules')
        if os.getenv('FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES', '').strip():
            errors.append('team_unverified_catalog_requires_empty_source_registry')
    if external_enabled and not external_ai_state.status()['configured']:
        errors.append('production_external_ai_persistent_state_missing')
    return errors
