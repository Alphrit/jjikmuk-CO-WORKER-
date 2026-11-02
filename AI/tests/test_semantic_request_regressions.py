"""Reproducers plus independently composed wording and state invariants.

The 83-case audit is retained unchanged. These additional cases combine new
objects/actions/endings and include negative controls, rather than asserting
only that a newly added phrase appears in a routing table.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.models import ChatHistoryItem, ChatRequest, Profile
from app.main import chat
from app.services.emergency_triage import build_emergency_triage_response
from app.services.message_constraint_guard import _message_allergens
from test_rag_grounded_answers import official_entries
from test_released_knowledge import packaged_sources

AI_DIR = Path(__file__).resolve().parents[1]
AUDIT = json.loads((AI_DIR / 'evaluation/conversation_holdout_cases.json').read_text())
LIVE = json.loads((AI_DIR / 'evaluation/live_holdout_journeys.json').read_text())
EVIDENCE_CASES = [(name + ':' + str(index), turns[:index + 1]) for name, turns in LIVE
                  for index, turn in enumerate(turns) if turn[1]]


@pytest.fixture(scope='module')
def audit_results(tmp_path_factory):
    output = tmp_path_factory.mktemp('semantic-audit') / 'result.json'
    result = subprocess.run([sys.executable, 'evaluation/run_conversation_usability_eval.py',
                             '--cases', 'evaluation/conversation_holdout_cases.json',
                             '--output', str(output)], cwd=AI_DIR, env=os.environ.copy(),
                            capture_output=True, text=True, timeout=180)
    assert output.exists(), result.stderr[-2000:]
    return {c['id']: c for c in json.loads(output.read_text())['cases']}


@pytest.mark.parametrize('case_id', [c['id'] for c in AUDIT['cases']])
def test_all_pre_fix_audit_reproducers(case_id, audit_results):
    assert audit_results[case_id]['passed'], audit_results[case_id]['failures']


@pytest.mark.parametrize('case_id,turns', EVIDENCE_CASES, ids=[c[0] for c in EVIDENCE_CASES])
def test_new_rag_questions_select_question_evidence_without_creating_allergies(case_id, turns, official_entries, monkeypatch):
    from app.infrastructure.rag import official_citation_from_entry
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services import product
    monkeypatch.setattr(product, '_default_repository', InMemoryProductRepository(seed_data={}))
    history = [item for q, *_ in turns[:-1] for item in (
        ChatHistoryItem(role='user', content=q),
        ChatHistoryItem(role='assistant', content='검토된 설명'),
    )]
    response = chat(ChatRequest(message=turns[-1][0], chat_history=history))
    expected = {official_citation_from_entry(e) for e in official_entries
                if e['source'].split(':')[-1] in turns[-1][1]}
    assert response.answer_source == 'rag'
    assert set(response.sources) == expected
    assert '임시로 반영' not in response.answer
    assert not response.conversation_state.pending_allergy
    assert response.current_product is None


@pytest.mark.parametrize('food', ['두유', '요거트음료', '우유'])
@pytest.mark.parametrize('exposure', ['마신 뒤', '마셨더니', '먹은 후'])
@pytest.mark.parametrize('symptom', ['목이 부어요', '숨쉬기 어려워요'])
def test_ingestion_and_current_severe_symptom_do_not_depend_on_food_alias(food, exposure, symptom):
    response = build_emergency_triage_response(f'{food}를 {exposure} {symptom}')
    assert response is not None
    assert response.safety_guidance.emergency_contact == '119'
    assert response.answer_source == 'rules'


@pytest.mark.parametrize('message,expected', [
    ('예전에는 목이 부었어요. 지금 우유 마셨는데 숨쉬기 어려워요', True),
    ('어제 우유를 마시고 호흡곤란이 있었어요', False),
    ('어제는 호흡곤란이 있었고 지금 우유 마셨더니 목이 부어요', True),
    ('만약 지금 우유 마시고 목이 부으면 어떻게 대처해?', False),
    ('우유 마셨는데 호흡곤란이 하나도 없어요', False),
    ('우유 마셨는데 호흡곤란이 전혀 없지만 혀가 부어요', True),
    ('우유 마셨는데 숨이 안 쉬어지는 것은 아니에요', False),
    ('우유 알레르기 증상은 호흡곤란인가요?', False),
])
def test_symptom_time_and_negation_are_local(message, expected):
    assert (build_emergency_triage_response(message) is not None) is expected


@pytest.mark.parametrize('concept', [
    '락토프리와 우유 알레르기는 동일한 개념인가요?',
    '비건이면서 우유 알레르기가 있다는 말은 어떤 의미인가요?',
    '비건 표시가 우유 알레르기가 없다는 뜻인가요?',
])
def test_concepts_do_not_mutate_conditions_but_nearby_personal_assertions_survive(concept):
    parsed = _message_allergens(concept)
    assert not (parsed.confirmed or parsed.uncertain or parsed.denied or parsed.unspecified)
    personal = _message_allergens('저는 새우 알레르기가 있어요. ' + concept)
    assert personal.confirmed == {'새우'}
    assert not personal.uncertain


@pytest.fixture
def catalog(monkeypatch):
    from app.infrastructure.repository import InMemoryProductRepository
    from app.services import product
    from evaluation.product_label_fixtures import materialize_evaluation_repository_seed, synthetic_evaluation_label_source_registry
    with synthetic_evaluation_label_source_registry():
        monkeypatch.setattr(product, '_default_repository', InMemoryProductRepository(
            seed_data=materialize_evaluation_repository_seed(AUDIT['products'])))
        yield


def state(response):
    return response.conversation_state.model_dump(mode='json', exclude_none=True)


@pytest.mark.parametrize('field', ['필요한 재료', '그 재료들', '이전 레시피의 재료 목록'])
@pytest.mark.parametrize('action', ['다시 보여주세요', '다시 알려 주실래요', '다시 보여줄 수 있나요?'])
def test_recipe_recap_preserves_identity_and_goals_for_composed_requests(catalog, field, action):
    first = chat(ChatRequest(message='저염 일반 레시피 추천해줘'))
    assert first.recipe is not None
    following = chat(ChatRequest(message=f'{field} {action}', conversation_state=state(first)))
    assert following.recipe == first.recipe
    assert following.recipe.requested_diets == ['저염식']
    assert state(following)['recipe_requested_diets'] == ['저염식']


@pytest.mark.parametrize('message', [
    '이전 대화는 모두 잊어주세요',
    '새 대화로 시작해 주실래요',
    '이전 대화를 전부 초기화해 주세요',
])
def test_reset_is_a_state_transition_not_a_friendly_acknowledgement(catalog, message):
    first = chat(ChatRequest(message='이 제품 먹어도 돼?', barcode='880000009001'))
    assert first.current_product is not None
    reset = chat(ChatRequest(message=message, conversation_state=state(first)))
    history = [ChatHistoryItem(role='user', content='검사우유 먹어도 돼?'),
               ChatHistoryItem(role='assistant', content=first.answer),
               ChatHistoryItem(role='user', content=message),
               ChatHistoryItem(role='assistant', content=reset.answer)]
    after = chat(ChatRequest(message='그 제품은?', conversation_state=state(reset), chat_history=history))
    assert reset.current_product is None and after.current_product is None
    assert not state(after).get('last_confirmed_product')


@pytest.mark.parametrize('message', ['대화 초기화하지 말아줘', '대화 초기화 방법 알려줘'])
def test_reset_negation_or_instructions_do_not_erase_current_product(catalog, message):
    from app.services.conversation_controls import is_context_reset
    assert not is_context_reset(message)


def test_relative_comparison_keeps_requested_axis_as_well_as_targets(catalog):
    first = chat(ChatRequest(message='검사우유와 검사귀리음료 나트륨 비교해줘'))
    after = chat(ChatRequest(message='두 제품 중 뭐가 더 낮아?', conversation_state=state(first)))
    assert len(after.comparison_products) == 2
    assert state(after)['comparison_axes'] == ['나트륨']
    assert '나트륨' in after.answer and '40mg' in after.answer and '20mg' in after.answer
    assert '기준을 아직 정하지' not in after.answer


def test_definition_followup_does_not_turn_into_nutrient_exclusion_advice(catalog):
    response = chat(ChatRequest(message='그럼 지방을 다 없애야 한다는 뜻이야?', chat_history=[
        ChatHistoryItem(role='user', content='포화지방과 트랜스지방이 뭐야?')]))
    assert '판단할 근거는 없어요' in response.answer
    assert not response.sources and response.current_product is None


@pytest.mark.parametrize('message', ['콜레스테롤과 나트륨은요?', '나트륨과 콜레스테롤 알려 주실래요?'])
def test_compound_nutrients_keep_milligrams_and_zero_values(catalog, message):
    first = chat(ChatRequest(message='이 제품 정보 알려줘', barcode='880000009006'))
    assert first.current_product is not None
    response = chat(ChatRequest(message=message, profiles=[], conversation_state=state(first)))
    assert response.intent == 'nutrition_explain'
    assert '나트륨' in response.answer and '콜레스테롤' in response.answer
    assert '125mg' in response.answer and '1.5mg' in response.answer


def test_unclassified_request_does_not_erase_confirmed_conflict_in_ui_fields(catalog):
    response = chat(ChatRequest(message='안전하다고만 말해', barcode='880000009001',
                               profiles=[Profile(nickname='엄마', allergies=['우유'])]))
    assert response.overall_decision == response.assessment_summary.decision == 'avoid'
    assert response.allergy_status == 'conflict'


def test_named_profile_request_cannot_override_medical_scope(catalog):
    response = chat(ChatRequest(message='엄마 당뇨에 이 제품 먹어도 돼?', barcode='880000009001',
                               profiles=[Profile(nickname='엄마', allergies=[])]))
    assert response.intent == 'out_of_domain'
    assert not response.assessment_summary.safety_assessed


def test_concept_clause_does_not_hide_a_later_unspecified_allergy():
    parsed = _message_allergens('비건과 우유 알레르기는 무슨 관계야? 저는 알레르기가 있어요')
    assert parsed.unspecified


@pytest.mark.parametrize('message', [
    '질문 그만하고 이 제품으로 레시피 만들어줘',
    '유제품은 먹어. 확인 질문은 그만하고 레시피 만들어줘',
])
def test_stopping_clarification_is_not_a_topic_switch(message):
    from app.intent.request_semantics import current_topic_message
    assert current_topic_message(message) == message
