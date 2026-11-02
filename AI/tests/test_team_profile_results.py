"""Team presentation must not discard upstream individual assessments."""
from unittest.mock import patch

import pytest

from app.api import main as api
from app.core.models import ChatRequest, ChatResponse, Product
from app.services.handlers import handle_can_i_eat
from app.services.response_assessment import attach_response_assessment


def profiles():
    return [
        dict(profile_id=1, profile_name='검사A', target_type='self',
             intent='can_i_eat', risk_level='high', overall_decision='avoid',
             allergy_status='conflict', assessment_status='complete',
             answer='등록된 밀 알레르기 성분을 확인하세요.', reasons=['밀 성분 충돌']),
        dict(profile_id=2, profile_name='검사B', target_type='family',
             intent='can_i_eat', risk_level='unknown', overall_decision='check_required',
             assessment_status='incomplete', answer='표시 정보를 확인하세요.',
             reasons=['정보 부족']),
    ]


@pytest.fixture
def product():
    return Product(barcode='synthetic-123', product_name='검증용 시리얼',
                   rawmtrl_nm='밀, 설탕', allergy=['밀'], facts_verified=True)


@pytest.mark.parametrize('fallback', [False, True])
def test_individual_results_and_identity_survive_both_response_paths(product, fallback):
    response = attach_response_assessment(handle_can_i_eat(None, product, '먹어도 돼?'), product=product)
    response = response.model_copy(update={
        'per_profile_results': profiles(), 'profile_name': '검사A, 검사B',
        'active_profile_names': ['검사A', '검사B'],
        **({'answer_source': 'llm', 'answer': '안전합니다.'} if fallback else {}),
    })
    before = response.model_dump(mode='json')
    result = api._team_catalog_response(response, resolved_product=product)
    wire = ChatResponse.model_validate(result.model_dump(mode='json')).model_dump(mode='json')
    assert wire['per_profile_results'] == before['per_profile_results']
    assert wire['active_profile_names'] == ['검사A', '검사B']
    assert wire['profile_name'] == '검사A, 검사B'
    assert response.model_dump(mode='json') == before
    if fallback:
        assert result.answer_source == 'rules'
        assert '안전합니다.' not in result.answer


@pytest.mark.parametrize('count', [0, 1, 2])
def test_real_stateless_pipeline_preserves_upstream_results(monkeypatch, product, count):
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    request = ChatRequest(message='이 상품 먹어도 돼?', barcode=product.barcode,
                          profiles=[{'profile_id': i+1, 'nickname': '검사'+str(i),
                                     'allergies': ['밀'] if i == 0 else ['우유']}
                                    for i in range(count)])
    with patch.object(api, 'resolve_product_context', return_value=(product, product.barcode, None, None)), \
         patch.object(api, '_log_pipeline_response'):
        actual_boundary = api._team_catalog_response
        upstream = []
        def capture(response, **kwargs):
            upstream.append(response.model_dump(mode='json'))
            return actual_boundary(response, **kwargs)
        with patch.object(api, '_team_catalog_response', side_effect=capture):
            result = api.chat(request)
    assert upstream
    assert result.model_dump(mode='json')['per_profile_results'] == upstream[0]['per_profile_results']
    assert result.active_profile_names == upstream[0]['active_profile_names']
    assert result.external_ai_processing.used is False
    if count == 2:
        assert len(result.per_profile_results) == 2
    elif count == 0:
        assert not result.per_profile_results
