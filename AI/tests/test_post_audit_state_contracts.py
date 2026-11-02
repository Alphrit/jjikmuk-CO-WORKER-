"""Keep the independent audit fixed; test state transitions and new compositions.

The catalog/LLM may fail, but that never changes which product or person a
question refers to. These are API behavior checks, not regex snapshots.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.models import ChatHistoryItem, ChatRequest, Profile
from app.main import chat
from app.services.emergency_triage import build_emergency_triage_response
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources
from test_semantic_request_regressions import catalog, state

AI = Path(__file__).resolve().parents[1]
AUDIT = json.loads((AI / 'evaluation/conversation_exploratory_cases.json').read_text())
LIVE = json.loads((AI / 'evaluation/live_exploratory_journeys.json').read_text())
RAG_CASES = [(f'{name}:{i}', turns[:i + 1]) for name, turns in LIVE
             for i, turn in enumerate(turns) if turn[1]]


@pytest.fixture(scope='module')
def independent_results(tmp_path_factory):
    output = tmp_path_factory.mktemp('post-audit') / 'results.json'
    completed = subprocess.run([
        sys.executable, 'evaluation/run_conversation_usability_eval.py',
        '--cases', 'evaluation/conversation_exploratory_cases.json', '--output', str(output),
    ], cwd=AI, env=os.environ.copy(), capture_output=True, text=True, timeout=180)
    assert output.exists(), completed.stderr[-2000:]
    return {case['id']: case for case in json.loads(output.read_text())['cases']}


@pytest.mark.parametrize('case_id', [case['id'] for case in AUDIT['cases']])
def test_independent_audit_contract(case_id, independent_results):
    result = independent_results[case_id]
    assert result['passed'], result['failures']


@pytest.mark.parametrize('case_id,turns', RAG_CASES, ids=[case[0] for case in RAG_CASES])
def test_unseen_rag_journeys_preserve_evidence_and_do_not_invent_ownership(case_id, turns, official_entries, monkeypatch):
    from app.infrastructure.rag import official_citation_from_entry
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services import product
    monkeypatch.setattr(product, '_default_repository', InMemoryProductRepository(seed_data={}))
    history = [item for turn in turns[:-1] for item in (
        ChatHistoryItem(role='user', content=turn[0]),
        ChatHistoryItem(role='assistant', content='검토된 설명'),
    )]
    response = chat(ChatRequest(message=turns[-1][0], chat_history=history))
    expected = {official_citation_from_entry(e) for e in official_entries
                if e['source'].split(':')[-1] in turns[-1][1]}
    assert response.answer_source == 'rag', response.fallback_reason
    assert set(response.sources) == expected
    assert response.current_product is None
    assert not response.conversation_state.pending_allergy
    assert '임시로 반영' not in response.answer


@pytest.mark.parametrize('name', ['미등록라이스음료XYZ', '새로운오트바ABC', '목록밖스낵Q123'])
@pytest.mark.parametrize('question', ['먹어도 돼?', '나트륨은?', '원재료 알려줘'])
def test_unknown_named_target_never_reuses_old_selection_even_after_general_topic(catalog, name, question):
    first = chat(ChatRequest(message='이 제품 정보 알려줘', barcode='880000009001'))
    history = [ChatHistoryItem(role='user', content='검사우유 먹어도 돼?'),
               ChatHistoryItem(role='assistant', content=first.answer)]
    missing = chat(ChatRequest(message=f'{name} {question}', conversation_state=state(first), chat_history=history))
    assert missing.current_product is None
    assert not state(missing).get('last_confirmed_product')
    general = chat(ChatRequest(message='무가당은 무슨 뜻이야?', conversation_state=state(missing), chat_history=history))
    after = chat(ChatRequest(message='그 상품 나트륨은?', conversation_state=state(general), chat_history=history))
    assert after.current_product is None
    assert '40mg' not in after.answer
    picked = chat(ChatRequest(message='이 제품 나트륨은?', barcode='880000009002', conversation_state=state(after)))
    assert picked.current_product.barcode == '880000009002'
    assert not state(picked).get('unresolved_product_query')


@pytest.mark.parametrize('food', ['요거트음료', '콩음료', '과자'])
@pytest.mark.parametrize('symptom', ['목이 부었습니다', '혀가 부었어요', '목이 조이는 느낌이에요', '숨이 차요', '목이 부은 것 같아요', '숨을 쉬기가 어려워요'])
def test_new_combinations_of_food_and_current_symptoms_do_not_wait_for_ai(food, symptom):
    response = build_emergency_triage_response(f'{food}를 먹은 직후 {symptom}')
    assert response is not None
    assert response.answer_source == 'rules'
    assert response.safety_guidance.emergency_contact == '119'


@pytest.mark.parametrize('symptom', ['목이 붓지 않았어요', '혀가 부은 적은 없어요', '호흡곤란도 없어요', '목이 부은 적은 있어요. 지금은 괜찮아요'])
def test_current_exposure_alone_does_not_override_negated_symptom(symptom):
    assert build_emergency_triage_response(f'우유를 마셨는데 {symptom}') is None


@pytest.mark.parametrize('concept', ['비건 표시와 어떤 관계야?', '비건 식단과 모순인가요?', '락토프리와 차이도 설명해줘'])
def test_own_condition_and_concept_in_same_clause_keep_the_personal_condition(concept):
    from app.services.message_constraint_guard import _message_allergens
    parsed = _message_allergens('저는 우유 알레르기가 있는데 ' + concept)
    assert parsed.confirmed == {'우유'}
    assert not parsed.unspecified


@pytest.mark.parametrize('message', ['고혈압 약을 먹는데 이 제품을 먹어도 되나요?', '내일 서울 날씨 알려줘'])
def test_outside_scope_question_is_not_interpreted_as_new_product_or_blocked_by_old_miss(catalog, message):
    missing = chat(ChatRequest(message='미등록스낵XYZ 먹어도 돼?'))
    assert missing.current_product is None
    response = chat(ChatRequest(message=message, conversation_state=state(missing)))
    assert response.intent == 'out_of_domain'
    assert response.current_product is None


@pytest.mark.parametrize('suffix', ['나트륨만 알려줘', '단백질은?', '먹어도 돼?'])
def test_demonstrative_replacement_selects_new_name_not_previous_product(catalog, suffix):
    first = chat(ChatRequest(message='이 제품 정보 알려줘', barcode='880000009001'))
    response = chat(ChatRequest(message=f'그 제품 말고 검사귀리음료 {suffix}', conversation_state=state(first)))
    assert response.current_product is not None
    assert response.current_product.barcode == '880000009002'


@pytest.mark.parametrize('field', ['profile_id', 'profileId', 'id', 'user_id', 'userId'])
@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan'), 1.5])
def test_profile_identifiers_are_bounded_integers_before_storage(field, value):
    with pytest.raises(ValidationError):
        Profile.model_validate({field: value, 'nickname': '검사'})


@pytest.mark.parametrize('payload', [
    b'{"message":"\\ud800"}',
    b'{"message":"test","profiles":[{"nickname":"\\udfff"}]}',
    b'{"message":"test","profiles":[{"userId":1e309}]}',
    b'{"message":"test","raw_profile":{"id":NaN}}',
    b'{"message":"test","conversation_state":{"\\ud800":1}}',
    b'{"message":"test","raw_product":{"sodium":-Infinity}}',
])
def test_invalid_json_primitives_are_client_errors_with_private_cache_headers(payload, monkeypatch):
    from app.api.main import app
    monkeypatch.setenv('FOOD_CHATBOT_API_TOKEN', '')
    monkeypatch.setenv('FOOD_CHATBOT_ENABLE_RATE_LIMIT', 'false')
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/chat', content=payload, headers={'Content-Type': 'application/json'})
    assert response.status_code == 400
    assert 'no-store' in response.headers['cache-control']
    assert 'Traceback' not in response.text
