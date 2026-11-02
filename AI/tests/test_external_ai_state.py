from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.infrastructure import external_ai_state as state


def configured(monkeypatch, tmp_path, used=9, limit=500):
    path = tmp_path / 'external.sqlite3'
    state.initialize(path, limit=limit, already_used=used)
    monkeypatch.setenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB', str(path))
    return path


def test_preserves_prior_paid_attempts_and_never_resets(monkeypatch, tmp_path):
    path = configured(monkeypatch, tmp_path, used=498)
    state.reserve_call('generation')
    state.reserve_call('embedding')
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('generation')
    assert state.status()['used'] == 500
    with pytest.raises(FileExistsError):
        state.initialize(path, limit=500, already_used=0)


def test_atomic_cap_across_connections(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path, used=490)
    def attempt(_):
        try:
            state.reserve_call('embedding')
            return True
        except state.ExternalAIStateError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(30))) == 10
    assert state.status()['used'] == 500


def test_approved_increase_preserves_calls_and_consent(monkeypatch, tmp_path):
    path = configured(monkeypatch, tmp_path, used=498)
    state.reserve_call('generation')
    state.reserve_call('embedding')
    assert state.consume_nonce(b'existing', expires_at=200, now=100)
    with sqlite3.connect(path) as db:
        calls = db.execute('SELECT * FROM calls ORDER BY id').fetchall()
        nonces = db.execute('SELECT * FROM nonces ORDER BY digest').fetchall()
        db.execute('UPDATE budget SET cap=1000 WHERE id=1 AND cap=500')
    assert state.status() == {'configured': True, 'limit': 1000, 'used': 500, 'remaining': 500}
    assert not state.consume_nonce(b'existing', expires_at=200, now=101)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM calls ORDER BY id').fetchall() == calls
        assert db.execute('SELECT * FROM nonces ORDER BY digest').fetchall() == nonces
        assert db.execute('SELECT prior FROM budget').fetchone() == (498,)
    state.reserve_call('generation')
    assert state.status()['used'] == 501


def test_new_1000_boundary_remains_atomic(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path, used=995, limit=1000)
    def attempt(_):
        try:
            state.reserve_call('generation')
            return True
        except state.ExternalAIStateError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(30))) == 5
    assert state.status()['remaining'] == 0


def test_cap_above_approval_is_rejected(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        state.initialize(tmp_path / 'invalid', limit=1001, already_used=0)
    path = configured(monkeypatch, tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE budget SET cap=1001 WHERE id=1')
    assert state.status() == {'configured': False}
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('embedding')


def test_missing_or_corrupt_state_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB', str(tmp_path/'absent'))
    assert not state.status()['configured']
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('generation')
    assert not (tmp_path/'absent').exists()


def test_nonce_replay_persists_and_distinct_nonce_works(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path)
    assert state.consume_nonce(b'first', expires_at=200, now=100)
    assert not state.consume_nonce(b'first', expires_at=200, now=101)
    assert state.consume_nonce(b'second', expires_at=200, now=101)
    assert state.status()['used'] == 9


def test_expired_nonces_removed_but_unexpired_not_evicted(monkeypatch, tmp_path):
    configured(monkeypatch, tmp_path)
    assert state.consume_nonce(b'old', expires_at=110, now=100)
    assert state.consume_nonce(b'new', expires_at=200, now=111)
    assert state.consume_nonce(b'old', expires_at=210, now=111)
    assert not state.consume_nonce(b'new', expires_at=210, now=112)


def test_no_unconfigured_production_disclosure(monkeypatch):
    monkeypatch.delenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB', raising=False)
    monkeypatch.setenv('FOOD_CHATBOT_ENV', 'production')
    with pytest.raises(state.ExternalAIStateError):
        state.reserve_call('generation')


def test_original_development_tests_do_not_need_a_paid_ledger(monkeypatch):
    monkeypatch.delenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB', raising=False)
    monkeypatch.setenv('FOOD_CHATBOT_ENV', 'development')
    assert state.reserve_call('generation') is None


def test_embedding_transport_counts_failed_attempt_and_blocks_after_cap(monkeypatch, tmp_path):
    import httpx
    from app.infrastructure.budgeted_embedding_transport import BudgetedEmbeddingTransport
    configured(monkeypatch, tmp_path, used=499)
    sent = []
    def offline_transport(self, request):
        sent.append(True)
        raise httpx.ConnectError('synthetic network failure')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', offline_transport)
    request = httpx.Request('POST', 'https://api.openai.com/v1/embeddings',
                            json={'model': 'text-embedding-3-small', 'input': 'synthetic'})
    with BudgetedEmbeddingTransport() as transport:
        with pytest.raises(httpx.ConnectError):
            transport.handle_request(request)
        with pytest.raises(state.ExternalAIStateError):
            transport.handle_request(request)
    assert sent == [True]
    assert state.status()['used'] == 500


def test_proof_replay_is_rejected_after_process_registry_reset(monkeypatch, tmp_path):
    import time
    from app.infrastructure import external_ai_consent as consent
    configured(monkeypatch, tmp_path)
    secret = 'synthetic-separate-consent-secret-20261015'
    monkeypatch.setenv('FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET', secret)
    now = int(time.time())
    body = b'{"message":"synthetic"}'
    signature = consent.sign_external_ai_consent(secret=secret, timestamp=now,
        method='POST', path='/chat', user_id='5', consent='granted', nonce='synthetic-nonce-1234', body=body)
    headers = {consent.CONSENT_HEADER: 'granted', consent.TIMESTAMP_HEADER: str(now),
               consent.NONCE_HEADER: 'synthetic-nonce-1234', consent.SIGNATURE_HEADER: signature,
               consent.USER_ID_HEADER: '5'}
    verify = lambda: consent.verify_external_ai_consent(headers=headers, method='POST',
        path='/chat', body=body, operator_enabled=True, now=now)
    assert verify().allowed
    consent.reset_external_ai_consent_nonce_registry()
    assert not verify().allowed
