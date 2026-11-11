import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import ActivityProductLabelTrace, ActivityRecord
from app.infrastructure.repository import (
    InMemoryActivityRepository,
    MySQLActivityRepository,
    SQLiteActivityRepository,
)


TRACE_KEYS = {
    "repository_identity_matched",
    "label_lineage_status",
    "label_freshness_status",
    "label_observed_at",
    "content_revision_sha256",
    "content_revision_binding_status",
    "nutrition_basis_type",
}


def _trace() -> ActivityProductLabelTrace:
    return ActivityProductLabelTrace(
        repository_identity_matched=True,
        label_lineage_status="complete",
        label_freshness_status="within_policy",
        label_observed_at=datetime(2026, 9, 8, 12, 34, 56, tzinfo=timezone.utc),
        content_revision_sha256="a" * 64,
        content_revision_binding_status="matched_ingested_record",
        nutrition_basis_type="per_serving",
    )


def _assert_bounded_trace_payload(payload: dict) -> None:
    assert set(payload) == TRACE_KEYS
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "source_record_id",
        "source_id",
        "raw_materials",
        "ingredients",
        "prompt",
        "answer",
        "profile",
        "upstream_revision",
    ):
        assert forbidden not in serialized


def test_in_memory_activity_trace_keeps_typed_value_and_legacy_none():
    repository = InMemoryActivityRepository()
    trace = _trace()

    repository.save(ActivityRecord(user_id=1, response_product_label_trace=trace))
    repository.save(ActivityRecord(user_id=2))

    assert repository.list_by_user_id(1)[0].response_product_label_trace == trace
    assert repository.list_by_user_id(2)[0].response_product_label_trace is None


def test_sqlite_activity_trace_round_trip_and_fallback_diagnostics(tmp_path):
    repository = SQLiteActivityRepository(str(tmp_path / "activity.sqlite3"))
    trace = _trace()
    repository.save(
        ActivityRecord(
            user_id=7,
            intent="can_i_eat",
            fallback_reason="test_fallback",
            response_product_label_trace=trace,
        )
    )
    repository.save(ActivityRecord(user_id=8, intent="general_food_question"))
    unknown_observation_trace = trace.model_copy(
        update={"label_observed_at": None}
    )
    repository.save(
        ActivityRecord(
            user_id=9,
            response_product_label_trace=unknown_observation_trace,
        )
    )

    with sqlite3.connect(repository.db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, response_product_label_trace "
            "FROM activity_history ORDER BY user_id"
        ).fetchall()

    stored_payload = json.loads(rows[0][1])
    _assert_bounded_trace_payload(stored_payload)
    assert rows[1][1] is None
    unknown_observation_payload = json.loads(rows[2][1])
    _assert_bounded_trace_payload(unknown_observation_payload)
    assert unknown_observation_payload["label_observed_at"] is None
    assert repository.list_by_user_id(7)[0].response_product_label_trace == trace
    assert repository.list_by_user_id(8)[0].response_product_label_trace is None
    assert (
        repository.list_by_user_id(9)[0].response_product_label_trace
        == unknown_observation_trace
    )
    diagnostic_trace = repository.recent_fallback_logs(1)[0][
        "response_product_label_trace"
    ]
    _assert_bounded_trace_payload(diagnostic_trace)


def test_sqlite_existing_activity_schema_adds_nullable_trace_column(tmp_path):
    db_path = tmp_path / "legacy-activity.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE activity_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                barcode TEXT,
                report_number TEXT,
                product_id INTEGER,
                action_type TEXT NOT NULL,
                message TEXT,
                intent TEXT,
                answer_source TEXT,
                pipeline_stage TEXT,
                risk_level TEXT,
                confidence REAL,
                confidence_reasons TEXT,
                fallback_reason TEXT,
                validation_failure_types TEXT,
                critical_safety_violation INTEGER DEFAULT 0,
                blocked_llm_output_stored INTEGER DEFAULT 0,
                response_id TEXT,
                response_kind TEXT NOT NULL DEFAULT 'chat',
                recheck_of_response_id TEXT,
                root_response_id TEXT,
                external_ai_used INTEGER NOT NULL DEFAULT 0,
                external_ai_services TEXT NOT NULL DEFAULT '[]',
                policy_version TEXT,
                applied_rule_ids TEXT NOT NULL DEFAULT '[]',
                source_ids TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO activity_history (user_id, action_type) VALUES (?, ?)",
            (9, "chat"),
        )
        conn.commit()

    repository = SQLiteActivityRepository(str(db_path))

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(activity_history)")
        }
        legacy_value = conn.execute(
            "SELECT response_product_label_trace FROM activity_history WHERE user_id = 9"
        ).fetchone()[0]
    assert "response_product_label_trace" in columns
    assert legacy_value is None
    assert repository.readiness_check()["audit_log_ready"] is True
    assert repository.list_by_user_id(9)[0].response_product_label_trace is None


def test_sqlite_invalid_or_expanded_trace_fails_closed_on_read(tmp_path):
    repository = SQLiteActivityRepository(str(tmp_path / "invalid.sqlite3"))
    with sqlite3.connect(repository.db_path) as conn:
        conn.execute(
            "INSERT INTO activity_history "
            "(user_id, action_type, response_product_label_trace) VALUES (?, ?, ?)",
            (1, "chat", "not-json"),
        )
        expanded = _trace().model_dump(mode="json")
        expanded["source_record_id"] = "private-record-id"
        conn.execute(
            "INSERT INTO activity_history "
            "(user_id, action_type, response_product_label_trace) VALUES (?, ?, ?)",
            (2, "chat", json.dumps(expanded)),
        )
        naive_observation = _trace().model_dump(mode="json")
        naive_observation["label_observed_at"] = "2026-09-08T12:34:56"
        conn.execute(
            "INSERT INTO activity_history "
            "(user_id, action_type, response_product_label_trace) VALUES (?, ?, ?)",
            (3, "chat", json.dumps(naive_observation)),
        )
        offset_observation = _trace().model_dump(mode="json")
        offset_observation["label_observed_at"] = "2026-09-08T12:34:56+09:00"
        conn.execute(
            "INSERT INTO activity_history "
            "(user_id, action_type, response_product_label_trace) VALUES (?, ?, ?)",
            (4, "chat", json.dumps(offset_observation)),
        )
        conn.commit()

    assert repository.list_by_user_id(1)[0].response_product_label_trace is None
    assert repository.list_by_user_id(2)[0].response_product_label_trace is None
    assert repository.list_by_user_id(3)[0].response_product_label_trace is None
    restored_offset = repository.list_by_user_id(4)[0].response_product_label_trace
    assert restored_offset is not None
    assert restored_offset.label_observed_at == datetime(
        2026,
        9,
        8,
        3,
        34,
        56,
        tzinfo=timezone.utc,
    )


class _FakeCursor:
    def __init__(self, rows=None, captured=None):
        self.rows = list(rows or [])
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=None):
        if self.captured is not None:
            self.captured["query"] = query
            self.captured["params"] = params

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, rows=None, captured=None):
        self.rows = rows
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return _FakeCursor(self.rows, self.captured)

    def commit(self):
        return None


def test_mysql_activity_trace_write_uses_nullable_json_parameter():
    trace = _trace()
    captured = {}
    repository = MySQLActivityRepository(password="test")
    repository._chatbot_log_schema_ready = True
    repository._connect = lambda: _FakeConnection(captured=captured)

    repository._save_chatbot_log(
        ActivityRecord(user_id=1, response_product_label_trace=trace)
    )

    assert "response_product_label_trace" in captured["query"]
    stored_payload = json.loads(captured["params"][-1])
    _assert_bounded_trace_payload(stored_payload)

    repository._save_chatbot_log(ActivityRecord(user_id=2))
    assert captured["params"][-1] is None


def test_mysql_activity_trace_and_null_rows_round_trip():
    trace = _trace()
    encoded = json.dumps(trace.model_dump(mode="json"))
    repository = MySQLActivityRepository(password="test")
    repository._connect = lambda: _FakeConnection(
        rows=[
            {"user_id": 7, "response_product_label_trace": encoded},
            {"user_id": 7, "response_product_label_trace": None},
        ]
    )
    repository._chatbot_log_table_exists = lambda _conn: True

    records = repository._list_chatbot_logs_by_user_id(7, 20)

    assert records[0].response_product_label_trace == trace
    assert records[1].response_product_label_trace is None


def test_mysql_log_works_on_a_db_without_the_later_audit_columns():
    """An older chatbot_logs (no 20260909 trace, short policy_version) still logs and passes /ready."""
    trace_column = "response_product_label_trace"
    base_columns = MySQLActivityRepository.CHATBOT_LOG_REQUIRED_COLUMNS
    assert trace_column not in base_columns

    repository = MySQLActivityRepository(password="test")
    captured = {}
    repository._connect = lambda: _FakeConnection(captured=captured)
    repository._table_exists = lambda _conn, _table: True
    repository._table_columns = lambda _conn, table: (
        set(base_columns) | {"policy_version"} if table == repository.quoted_chat_log_table_name else set()
    )
    repository._column_widths = {"policy_version": 32}

    ready = repository.readiness_check()
    assert ready["available"] is True and ready["audit_log_ready"] is True

    repository._save_chatbot_log(ActivityRecord(
        user_id=1, barcode="880", action_type="SCAN", message="먹어도 돼?", intent="can_i_eat",
        policy_version="unified-hard-constraints-purpose-v5.2",
    ))
    assert trace_column not in captured["query"] and "external_ai_services" not in captured["query"]
    assert any(isinstance(value, str) and len(value) == 32 and "~" in value for value in captured["params"])


def test_mysql_product_trace_migration_is_additive_and_content_free():
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "20260909_chatbot_log_product_trace.sql"
    ).read_text(encoding="utf-8")
    normalized = " ".join(migration.lower().split())
    sql_only = " ".join(
        line.strip().lower()
        for line in migration.splitlines()
        if not line.lstrip().startswith("--")
    )

    assert "alter table chatbot_logs" in normalized
    assert "add column response_product_label_trace json null" in normalized
    assert "create table" not in sql_only
    for forbidden in (
        "source_record_id",
        "source_id",
        "raw_materials",
        "ingredients",
        "prompt",
        "answer",
        "profile",
        "upstream_revision",
    ):
        assert forbidden not in sql_only
