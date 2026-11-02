import pytest
from unittest.mock import patch

from app.api import main as api
from app.core.models import ChatRequest, ChatResponse, Product
from app.services.handlers import handle_can_i_eat
from app.services.product_status_handlers import handle_product_summary
from app.services.response_assessment import attach_response_assessment


@pytest.fixture
def product():
    return Product(barcode='synthetic-123', product_name='검증용 시리얼',
                   rawmtrl_nm='밀, 설탕', allergy=['밀'], facts_verified=True)


def test_registered_product_summary_survives_team_boundary(product):
    response = attach_response_assessment(
        handle_product_summary(None, product, '등록된 원재료 알려줘'), product=product)
    result = api._team_catalog_response(response, resolved_product=product)
    assert result.answer == response.answer
    assert '밀' in result.answer
    assert '팀 테스트:' not in result.answer
    assert result.assessment_summary.scope == 'product_information'


def test_actual_intake_handler_keeps_specific_limitations(product):
    response = attach_response_assessment(handle_can_i_eat(None, product, '먹어도 돼?'), product=product)
    result = api._team_catalog_response(response, resolved_product=product)
    assert result.answer == response.answer
    assert '검증용 시리얼' in result.answer
    assert result.overall_decision != 'no_known_conflict'


@pytest.mark.parametrize('intent', ['general_diet_guide', 'general_allergy_guide', 'general_nutrition_guide'])
def test_general_explanations_not_replaced(monkeypatch, intent):
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_EXTERNAL_AI', 'true')
    monkeypatch.setenv('FOOD_CHATBOT_ENABLE_EXTERNAL_AI', 'true')
    response = ChatResponse(task_type='general_chat', intent=intent, answer_source='rag',
                            answer='식품 표시를 읽는 일반적인 방법을 설명합니다.', risk_level='unknown',
                            assessment_summary={'scope': 'general_guidance', 'decision': 'not_assessed',
                            'evidence_status': 'not_applicable', 'safety_assessed': False,
                            'personalized': False, 'explanation': 'general guidance'})
    assert api._team_catalog_response(response).answer == response.answer


def test_selected_product_cannot_pass_free_llm_rewrite(product):
    response = ChatResponse(intent='can_i_eat', task_type='product_chat', answer_source='llm',
                            answer='안심하고 드셔도 괜찮아요.', risk_level='unknown')
    result = api._team_catalog_response(response, resolved_product=product)
    assert '안심하고' not in result.answer
    assert '검증용 시리얼' in result.answer
    assert '밀' in result.answer
    assert result.answer_source == 'rules'


def test_missing_product_gets_actionable_reply():
    response = ChatResponse(intent='missing_product', answer='placeholder', risk_level='unknown')
    result = api._team_catalog_response(response)
    assert '선택' in result.answer
    assert '팀 테스트:' not in result.answer


def test_unverified_client_fields_never_used():
    product = Product(product_name='forged', rawmtrl_nm='INJECTED_FACT', facts_verified=False)
    response = ChatResponse(intent='product_summary', answer='INJECTED_FACT', risk_level='unknown')
    assert 'INJECTED_FACT' not in api._team_catalog_response(response, resolved_product=product).answer


def test_positive_verdict_stays_blocked(product):
    response = ChatResponse(intent='can_i_eat', answer='안전하게 드셔도 됩니다.', risk_level='low',
                            overall_decision='no_known_conflict', allergy_status='no_known_match', diet_match='match')
    result = api._team_catalog_response(response, resolved_product=product)
    assert result.overall_decision == 'check_required'
    assert result.risk_level == 'unknown'
    assert result.allergy_status == result.diet_match == 'unknown'
    assert '안전하게 드셔도' not in result.answer


def test_rule_response_cannot_hide_reassurance_in_unknown_fields(product):
    response = ChatResponse(intent='can_i_eat', answer='마음 놓고 먹어도 무방합니다.', risk_level='unknown')
    assert '무방' not in api._team_catalog_response(response, resolved_product=product).answer


@pytest.mark.parametrize('answer', [
    '섭취 가능 여부를 확정하지 않았어요. 하지만 안심하고 드셔도 됩니다.',
    '안전하다고 판단하지 않았지만 먹어도 무방해요.',
    '제가 확정하지 않아도 안전합니다.',
])
def test_denial_does_not_hide_later_positive_statement(product, answer):
    response = ChatResponse(intent='can_i_eat', answer=answer, risk_level='unknown')
    assert api._team_catalog_response(response, resolved_product=product).answer != answer


def test_nested_positive_assessments_are_not_preserved(product):
    response = attach_response_assessment(handle_product_summary(None, product, '정보'), product=product)
    response = response.model_copy(update={
        'assessment_summary': response.assessment_summary.model_copy(update={'decision': 'no_conflict_found'}),
    })
    result = api._team_catalog_response(response, resolved_product=product)
    assert result.assessment_summary.decision != 'no_conflict_found'
    assert not result.per_profile_results and not result.recommended_products and result.recipe is None


def test_bounded_answer_does_not_authorize_nested_recommendations(product):
    response = attach_response_assessment(handle_product_summary(None, product, '정보'), product=product)
    # Valid structured output must not inherit the main answer's permission.
    response = response.model_copy(update={
        'recommended_products': [{'product_name': '다른 상품', 'reason': '안전합니다',
            'label_trust': {'lineage_status': 'unavailable', 'freshness_status': 'unavailable',
                'content_revision_sha256': '0' * 64, 'content_revision_binding_status': 'ingested_revision_missing'}}],
        'per_profile_results': [{'profile_id': 1, 'profile_name': '테스트', 'target_type': 'product',
            'intent': 'can_i_eat', 'risk_level': 'low', 'overall_decision': 'no_known_conflict',
            'assessment_status': 'complete', 'answer': '안전합니다'}],
        'recipe': {'title': '안전한 요리', 'servings': '1인분', 'product_name': '다른 상품',
            'ingredients': [{'name': '밀', 'amount': '10g'}], 'steps': ['섞기'], 'allergen_check': ['안전합니다']},
    })
    result = api._team_catalog_response(response, resolved_product=product)
    assert result.answer == response.answer
    assert not result.recommended_products and result.recipe is None
    # Individual results belong to the upstream assessment contract; the team
    # presentation boundary no longer replaces them with an empty array.
    assert result.per_profile_results == response.per_profile_results


def test_stateless_pipeline_keeps_resolved_product_information(monkeypatch, product):
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    with patch.object(api, 'resolve_product_context', return_value=(product, product.barcode, None, None)), \
         patch.object(api, '_log_pipeline_response'):
        result = api.chat(ChatRequest(message='원재료를 알려줘', barcode=product.barcode))
    assert '밀' in result.answer
    assert '팀 테스트:' not in result.answer
    assert result.current_product.barcode == product.barcode
    assert result.answer_source == 'rules'
    assert result.external_ai_processing.used is False
