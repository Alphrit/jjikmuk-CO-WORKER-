"""Persistent call allocation and consent replay store; never stores prompts.

Compatible with the existing operational SQLite ledger. Opening an unavailable
ledger must never create a fresh one or reset previous usage.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time

# User-approved cumulative allocation (2026-10-30); existing usage is preserved.
APPROVED_MAX_CALLS = 1000


class ExternalAIStateError(RuntimeError):
    pass


def initialize(path: Path, *, limit: int, already_used: int) -> None:
    """Explicit offline/operator provisioning only; never called at startup."""
    if not 0 <= already_used <= limit <= APPROVED_MAX_CALLS:
        raise ValueError('Invalid approved chatbot allocation')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE budget (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER, prior INTEGER)')
        db.execute('INSERT INTO budget VALUES (1, ?, ?)', (limit, already_used))
        db.execute('CREATE TABLE calls (id INTEGER PRIMARY KEY, kind TEXT, started REAL)')
        db.execute('CREATE TABLE nonces (digest BLOB PRIMARY KEY, expires REAL)')


@contextmanager
def connection():
    configured = os.getenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB', '')
    path = Path(configured)
    if not configured or not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ExternalAIStateError('External AI state unavailable')
    try:
        with sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True, timeout=5) as db:
            yield db
    except sqlite3.Error as error:
        raise ExternalAIStateError('External AI state unavailable') from error


def status() -> dict:
    try:
        with connection() as db:
            cap, used = _budget(db)
            db.execute('SELECT digest FROM nonces LIMIT 1')
        return {'configured': True, 'limit': cap, 'used': used, 'remaining': cap-used}
    except (ExternalAIStateError, TypeError, ValueError):
        return {'configured': False}


def _budget(db):
    row = db.execute('SELECT cap, prior FROM budget WHERE id=1').fetchone()
    if row is None or any(type(value) is not int for value in row):
        raise ExternalAIStateError('Invalid external AI budget')
    cap, prior = row
    used = prior + db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
    if not 0 <= prior <= used <= cap <= APPROVED_MAX_CALLS:
        raise ExternalAIStateError('Invalid external AI budget')
    return cap, used


def required() -> bool:
    """Production never silently drops accounting if the path is missing."""
    return bool(os.getenv('FOOD_CHATBOT_EXTERNAL_AI_STATE_DB')) or (
        os.getenv('FOOD_CHATBOT_ENV', 'development').strip().lower() == 'production'
    )


def reserve_call(kind: str):
    if kind not in {'generation', 'embedding'}:
        raise ExternalAIStateError('Invalid provider operation')
    # Existing offline fixtures use injected transports and no production state.
    if not required():
        return None
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        cap, used = _budget(db)
        if used >= cap:
            raise ExternalAIStateError('Approved chatbot call allocation exhausted')
        return db.execute('INSERT INTO calls(kind,started) VALUES (?,?)', (kind, time.time())).lastrowid


def consume_nonce(digest: bytes, *, expires_at: float, now: float) -> bool:
    try:
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM nonces WHERE expires < ?', (now,))
            if db.execute('SELECT COUNT(*) FROM nonces').fetchone()[0] >= 10000:
                return False  # Never evict a still-valid proof to make room.
            return db.execute('INSERT OR IGNORE INTO nonces VALUES (?,?)', (digest, expires_at)).rowcount == 1
    except ExternalAIStateError:
        return False
