"""Independent-audit failures and adjacent safety/identity boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.models import ChatHistoryItem, ChatRequest, Profile
from app.services.emergency_triage import build_emergency_triage_response
from app.services.handlers import handle_nutrition_explain
from app.services import product as products
from app.infrastructure.repository import InMemoryProductRepository
from app.main import chat
from test_chatbot import _verified_product
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources

AI_DIR = Path(__file__).resolve().parents[1]
CASES = json.loads((AI_DIR / 'evaluation/conversation_extended_cases.json').read_text())['cases']
LIVE = json.loads((AI_DIR / 'evaluation/live_extended_journeys.json').read_text())
RAG_CASES = [(name + ':' + str(index), turns[:index + 1]) for name, turns in LIVE
             for index, turn in enumerate(turns) if turn[1] is not None]


@pytest.fixture(scope='module')
def extended_results(tmp_path_factory):
    destination = tmp_path_factory.mktemp('independent-audit') / 'results.json'
    run = subprocess.run([sys.executable, 'evaluation/run_conversation_usability_eval.py',
                          '--cases', 'evaluation/conversation_extended_cases.json',
                          '--output', str(destination)], cwd=AI_DIR, env=os.environ.copy(),
                         capture_output=True, text=True, timeout=180)
    assert destination.exists(), run.stderr[-2000:]
    return {case['id']: case for case in json.loads(destination.read_text())['cases']}


@pytest.mark.parametrize('case_id', [c['id'] for c in CASES])
def test_fresh_user_journeys(case_id, extended_results):
    result = extended_results[case_id]
    assert result['passed'], result['failures']
    for index, turn in enumerate(result['turns']):
        # The replay artifact must preserve the request at that moment, not
        # acquire future questions when the evaluator appends to its history.
        assert len(turn['request']['chat_history']) == index * 2


@pytest.mark.parametrize('case_id,turns', RAG_CASES, ids=[c[0] for c in RAG_CASES])
def test_live_audit_questions_keep_the_expected_evidence(case_id, turns, official_entries, monkeypatch):
    from app.infrastructure.rag import official_citation_from_entry
    monkeypatch.setattr(products, '_default_repository', InMemoryProductRepository(seed_data={}))
    history = [item for turn in turns[:-1] for item in (
        ChatHistoryItem(role='user', content=turn[0]),
        ChatHistoryItem(role='assistant', content='검토된 설명'),
    )]
    response = chat(ChatRequest(message=turns[-1][0], chat_history=history))
    expected = {official_citation_from_entry(entry) for entry in official_entries
                if entry['source'].split(':')[-1] in turns[-1][1]}
    assert response.answer_source == 'rag'
    assert set(response.sources) == expected
    assert response.current_product is None
    assert '임시로 반영' not in response.answer


@pytest.mark.parametrize('question', [
    '그럼 섞일 확률을 숫자로 말할 수 있어?',
    '얼마나 섞였는지 %로 알려줘',
    '그럼 혼입 확률은 몇 %야?',
])
def test_cross_contact_quantity_followup_explains_evidence_limit(question, monkeypatch):
    from app.services import hybrid
    monkeypatch.setattr(hybrid, 'get_rag_context_entries', lambda **kw: pytest.fail('No quantitative evidence'))
    history = [ChatHistoryItem(role='user', content='땅콩 제품이랑 생산 라인을 같이 쓴다는 문구가 어떤 뜻이야?')]
    response = chat(ChatRequest(message=question, chat_history=history))
    assert response.intent == 'general_allergy_guide'
    assert response.current_product is None and not response.sources
    assert '근거가 없어요' in response.answer
    assert '추정해서 안내하지 않아요' in response.answer
    assert '제품명' not in response.answer


@pytest.mark.parametrize('message,emergency', [
    ('우유 먹고 숨쉬기 어려워요', True),
    ('우유 먹고 호흡이 어려워요', True),
    ('우유 먹고 숨이 안 쉬어지는 건 아니야', False),
    ('우유 먹고 호흡곤란은 없어요', False),
    ('우유 먹고 숨이 안 쉬어지는 건 아니고 목이 부어요', True),
    ('예전에 우유 먹고 숨이 안 쉬어졌어요', False),
    ('만약 우유 먹고 호흡이 어려우면 어떻게 해?', False),
])
def test_current_symptom_morphology_and_local_negation(message, emergency):
    response = build_emergency_triage_response(message)
    assert (response is not None) is emergency
    if response:
        assert response.safety_guidance.emergency_contact == '119'
        assert response.answer_source == 'rules'


def test_emergency_followup_precedes_catalog_lookup(monkeypatch):
    from app.api import main as api
    monkeypatch.setattr(api, 'resolve_product_context', lambda **kw: pytest.fail('No lookup during emergency'))
    response = api._run_chat_pipeline(ChatRequest(
        message='먹었는데 숨이 안 쉬어져', profiles=[Profile(profile_name='엄마', allergies=['우유'])],
        conversation_state={'last_confirmed_product': {'barcode': 'missing-but-contextual'}},
    ), log_activity=False)[0]
    assert response.intent == 'emergency_safety'


@pytest.mark.parametrize('basis,unit,size,expected', [
    ('per_100ml', 'ml', 250, '100mg'),
    ('per_100g', 'g', 300, '120mg'),
    ('per_100ml', 'g', 250, None),  # no density conversion
    ('unknown', 'ml', 250, None),
])
def test_package_calculation_requires_matching_denominator(basis, unit, size, expected):
    product = _verified_product(ingredients='정제수', allergy=[]).model_copy(update={
        'nutrition': {'sodium': 40},
    })
    from app.core.models import NutritionBasis
    product.nutrition_basis = NutritionBasis(basis_type=basis, package_size_value=size, package_size_unit=unit)
    response = handle_nutrition_explain(None, product, '한 통 다 마시면 나트륨은 얼마야?')
    if expected:
        assert expected in response.answer
    else:
        assert '계산하지 못했어요' in response.answer
        assert '약 100mg' not in response.answer
