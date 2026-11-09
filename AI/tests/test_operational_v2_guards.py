"""Synthetic v2 and transport checks: no AWS, real keys or provider calls."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from app.api import main as api
from app.infrastructure import external_ai_state as state
from app.infrastructure import external_ai_consent as consent
from backend_v2_helpers import request_payload
from test_backend_analysis_v2 import client, send
from test_external_ai_state import configured
from test_production_deployment_boundary import _production_env


def test_team_startup_requires_all_other_production_guards(monkeypatch):
    _production_env(monkeypatch, FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG='true',
                    FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES='')
    assert api._deployment_boundary_status()['valid']
    monkeypatch.setenv('FOOD_CHATBOT_MYSQL_SSL_CA', '')
    monkeypatch.setenv('FOOD_CHATBOT_API_TOKEN', '')
    codes = api._deployment_boundary_status()['error_codes']
    assert 'production_mysql_tls_ca_missing' in codes
    assert 'production_api_token_too_short' in codes


def test_default_production_still_requires_real_sources(monkeypatch):
    _production_env(monkeypatch, FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES='')
    assert 'production_product_label_source_registry_invalid' in api._deployment_boundary_status()['error_codes']


@pytest.mark.parametrize('flag,code', [
    ('FOOD_CHATBOT_ENABLE_CONVERSATIONS', 'team_unverified_catalog_requires_stateless_rules'),
    ('FOOD_CHATBOT_ENABLE_EXTERNAL_AI', 'team_unverified_catalog_requires_stateless_rules'),
    ('FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES', 'team_unverified_catalog_requires_empty_source_registry'),
])
def test_team_mode_is_explicit_and_never_invents_sources(monkeypatch, flag, code):
    settings = dict(FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG='true',
                    FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES='')
    settings[flag] = 'manufacturer_label:synthetic' if flag.endswith('SOURCES') else 'true'
    _production_env(monkeypatch, **settings)
    assert code in api._deployment_boundary_status()['error_codes']


def test_external_team_mode_needs_existing_ledger_and_valid_consent(monkeypatch, tmp_path):
    _production_env(monkeypatch, FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG='true',
                    FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES='',
                    FOOD_CHATBOT_TEAM_EXTERNAL_AI='true', FOOD_CHATBOT_ENABLE_EXTERNAL_AI='true',
                    FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET='synthetic-consent-secret-32-characters')
    assert 'production_external_ai_persistent_state_missing' in api._deployment_boundary_status()['error_codes']
    configured(monkeypatch, tmp_path)
    assert api._deployment_boundary_status()['valid']
    monkeypatch.setenv('FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET', '')
    assert 'production_external_ai_consent_not_configured' in api._deployment_boundary_status()['error_codes']


@pytest.mark.parametrize('statuses', [(), ('PASS',), ('UNKNOWN',), ('DANGER','PASS'), tuple(['PASS']*21)])
def test_team_mode_preserves_v2_verdicts_people_and_headers(client, monkeypatch, statuses):
    payload = request_payload(statuses)
    expected = send(client, payload)
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    monkeypatch.setenv('FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES', '')
    actual = send(client, payload)
    for key in ('per_profile_results','active_profile_names','backend_analysis',
                'overall_decision','diet_match','assessment_summary','current_product'):
        assert actual[key] == expected[key]
    assert actual['answer_source'] == 'backend'
    assert actual['answer'].startswith(expected['answer'])
    from app.services.followup_questions import PRODUCT_REFERENCE_NOTE
    assert actual['answer'].endswith(PRODUCT_REFERENCE_NOTE)
    assert actual['external_ai_processing']['used'] is False


def test_team_v2_context_only_keeps_context_header(client, monkeypatch):
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    payload = request_payload(('PASS',), product=False)
    result = send(client, payload)
    assert result['backend_analysis']['application'] == 'context_only'
    assert result['backend_analysis']['contextId'] == payload['analysisContext']['contextId']


def test_team_flag_does_not_bypass_auth(client, monkeypatch):
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    assert client.post('/chat', json=request_payload()).status_code == 401


@pytest.mark.parametrize('message', [
    '나는 달걀 알레르기도 있어. 이 상품 먹어도 돼?',
    '새우 알레르기도 있어. 이 상품 먹어도 돼?',
])
def test_changed_condition_clarification_is_not_replaced(client, monkeypatch, message):
    payload = request_payload(('PASS',))
    payload['message'] = message
    expected = send(client, payload)
    assert expected['backend_analysis']['application'] == 'context_only'
    monkeypatch.setenv('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG', 'true')
    actual = send(client, payload)
    for field in ('answer', 'backend_analysis', 'per_profile_results', 'overall_decision'):
        assert actual[field] == expected[field]


def test_selection_required_response_is_not_replaced():
    from app.core.models import ChatResponse
    from app.core.backend_analysis import PublicBackendAnalysis
    from app.services.team_catalog import restrict_team_catalog_response
    payload = request_payload()
    context = payload['analysisContext']
    response = ChatResponse(answer='찾은 상품을 선택해 현재 프로필의 판정 정보를 받아주세요.',
        intent='product_summary', risk_level='unknown', overall_decision='check_required',
        backend_analysis=PublicBackendAnalysis(contextId=context['contextId'], policyVersion=context['policyVersion'],
            profileSource=context['profileSource'], selectionMode='provided', productResolution='FOUND',
            status='DANGER', dietStatus='PASS', application='product_selection_required'))
    assert restrict_team_catalog_response(response) == response


def test_malformed_budget_and_production_path_fail_closed(monkeypatch, tmp_path):
    path = configured(monkeypatch, tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM budget')
    assert state.status() == {'configured': False}
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('generation')
    monkeypatch.delenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB')
    monkeypatch.setenv('FOOD_CHATBOT_ENV', ' Production ')
    assert consent.external_ai_consent_boundary_status()['replay_scope'] == 'persistent_unavailable'
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('generation')


def test_full_nonce_store_never_evicts_live_proofs(monkeypatch, tmp_path):
    path = configured(monkeypatch, tmp_path)
    with sqlite3.connect(path) as db:
        db.executemany('INSERT INTO nonces VALUES (?,?)', [(str(i).encode(), 500) for i in range(10000)])
    assert not state.consume_nonce(b'new', expires_at=500, now=100)
    assert not state.consume_nonce(b'0', expires_at=500, now=100)
    assert state.consume_nonce(b'new', expires_at=900, now=501)


def test_generation_counts_network_failure_and_stops_before_next_send(monkeypatch, tmp_path):
    from app.infrastructure import llm
    configured(monkeypatch, tmp_path, used=499)
    sent = []
    def fail(*args, **kwargs):
        sent.append(True)
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(llm.request, 'urlopen', fail)
    for _ in range(2):
        assert llm._post_json('https://api.openai.com/v1/responses', {}, {}) is None
    assert sent == [True]
    assert state.status()['used'] == 500


@pytest.mark.parametrize('kind', ['generation', 'embedding', 'async_embedding'])
@pytest.mark.parametrize('exhausted', [False, True])
def test_disclosure_marker_requires_successful_budget_reservation(monkeypatch, tmp_path, kind, exhausted):
    from app.infrastructure import llm
    from app.infrastructure.external_ai_consent import (
        ExternalAIConsentDecision, external_ai_request_context,
        current_external_ai_processing_summary,
    )
    from app.infrastructure.budgeted_embedding_transport import (
        BudgetedEmbeddingTransport, BudgetedAsyncEmbeddingTransport,
    )
    configured(monkeypatch, tmp_path, used=500 if exhausted else 499)
    sent = []
    def timeout(*args, **kwargs):
        sent.append(True)
        raise TimeoutError('synthetic timeout')
    async def async_timeout(*args, **kwargs):
        timeout()
    monkeypatch.setattr(llm.request, 'urlopen', timeout)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', timeout)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_timeout)
    decision = ExternalAIConsentDecision(verified=True, consent_granted=True, reason='verified')
    request = httpx.Request('POST', 'https://api.openai.com/v1/embeddings',
                            json={'model':'text-embedding-3-small','input':'synthetic'})
    expected_error = state.ExternalAIStateError if exhausted else TimeoutError
    async def run_async():
        with external_ai_request_context(decision):
            async with BudgetedAsyncEmbeddingTransport(
                endpoint=str(request.url), model='text-embedding-3-small',
            ) as transport:
                with pytest.raises(expected_error):
                    await transport.handle_async_request(request)
            return current_external_ai_processing_summary()
    if kind == 'async_embedding':
        # Read the ContextVar inside the same task that performed the request.
        summary = asyncio.run(run_async())
    else:
        with external_ai_request_context(decision):
            if kind == 'generation':
                assert llm._post_json('https://api.openai.com/v1/responses', {}, {}) is None
            else:
                with BudgetedEmbeddingTransport() as transport, pytest.raises(expected_error):
                    transport.handle_request(request)
            summary = current_external_ai_processing_summary()
    assert summary['used'] is (not exhausted)
    assert len(sent) == (0 if exhausted else 1)


def test_async_embedding_cannot_bypass_shared_cap(monkeypatch, tmp_path):
    from app.infrastructure.budgeted_embedding_transport import BudgetedAsyncEmbeddingTransport
    configured(monkeypatch, tmp_path, used=499)
    sent = []
    async def fake(self, request):
        sent.append(True)
        return httpx.Response(200, json={'data': []})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', fake)
    async def run():
        async with BudgetedAsyncEmbeddingTransport(endpoint='https://api.openai.com/v1/embeddings', model='text-embedding-3-small') as transport:
            request = httpx.Request('POST','https://api.openai.com/v1/embeddings',json={'model':'text-embedding-3-small','input':'synthetic'})
            assert (await transport.handle_async_request(request)).status_code == 200
            with pytest.raises(state.ExternalAIStateError):
                await transport.handle_async_request(request)
    asyncio.run(run())
    assert sent == [True]
    assert state.status()['used'] == 500


@pytest.mark.parametrize('url,payload', [
    ('https://example.invalid/embeddings', {'model':'text-embedding-3-small'}),
    ('https://api.openai.com/v1/embeddings', {'model':'other'}),
    ('https://api.openai.com/v1/embeddings', []),
])
def test_wrong_embedding_target_rejected_before_reservation(monkeypatch, tmp_path, url, payload):
    from app.infrastructure.budgeted_embedding_transport import BudgetedEmbeddingTransport
    configured(monkeypatch, tmp_path)
    with BudgetedEmbeddingTransport() as transport, pytest.raises(ValueError):
        transport.handle_request(httpx.Request('POST', url, json=payload))
    assert state.status()['used'] == 9


def test_rag_factory_installs_both_budgeted_clients(monkeypatch, tmp_path):
    from app.infrastructure import rag
    from app.infrastructure.budgeted_embedding_transport import BudgetedEmbeddingTransport, BudgetedAsyncEmbeddingTransport
    configured(monkeypatch, tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-unused')
    monkeypatch.setattr(rag, '_rag_dependencies_available', lambda: True)
    monkeypatch.setattr(rag, 'OpenAIEmbeddings', lambda **kw: kw)
    result = rag._get_embeddings()
    try:
        assert isinstance(result['http_client']._transport, BudgetedEmbeddingTransport)
        assert isinstance(result['http_async_client']._transport, BudgetedAsyncEmbeddingTransport)
        assert not result['http_client'].follow_redirects
        assert not result['http_async_client'].follow_redirects
        assert result['max_retries'] == 0
    finally:
        result['http_client'].close()
        asyncio.run(result['http_async_client'].aclose())
    assert state.status()['used'] == 9


@pytest.mark.parametrize('async_call', [False, True])
@pytest.mark.parametrize('text', ['가' * 500, '😀' * 4000], ids=['normal-korean', 'maximum-unicode'])
def test_real_sdk_batches_bulk_embeddings_within_transport_limit(monkeypatch, tmp_path, async_call, text):
    from app.infrastructure import rag
    configured(monkeypatch, tmp_path, used=0, limit=1000)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-unused')
    physical_requests = []
    def answer(request):
        payload = json.loads(request.content)
        physical_requests.append(len(request.content))
        return httpx.Response(200, request=request, json={'object':'list',
            'data':[{'index':i,'object':'embedding','embedding':[0.1,0.2]} for i in range(len(payload['input']))],
            'model':payload['model'], 'usage':{'prompt_tokens':1,'total_tokens':1}})
    def sync_send(self, request):
        return answer(request)
    async def async_send(self, request):
        return answer(request)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', sync_send)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_send)
    embeddings = rag._get_embeddings()
    try:
        results = asyncio.run(embeddings.aembed_documents([text]*100)) if async_call else embeddings.embed_documents([text]*100)
        assert len(results) == 100
        assert len(physical_requests) == 50
        assert max(physical_requests) <= 65536
        assert state.status()['used'] == len(physical_requests)
    finally:
        embeddings.http_client.close()
        asyncio.run(embeddings.http_async_client.aclose())
