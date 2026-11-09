"""chatbot_logs columns must hold what the backend sends, and failures must say which column."""

import logging

from app.infrastructure.activity_storage.mysql import MySQLActivityRepository, _database_error_detail


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)


def _repository():
    repository = object.__new__(MySQLActivityRepository)
    repository.quoted_chat_log_table_name = "`chatbot_logs`"
    return repository


def test_narrow_policy_version_column_is_reported():
    rows = [{"Field": "policy_version", "Type": "varchar(32)"}, {"Field": "message", "Type": "text"}]
    assert _repository()._short_audit_columns(_Conn(rows)) == ["policy_version"]


def test_widened_policy_version_column_passes():
    assert _repository()._short_audit_columns(_Conn([{"Field": "policy_version", "Type": "varchar(256)"}])) == []


def test_schema_width_check_never_breaks_startup():
    class Broken:
        def cursor(self):
            raise OSError("no connection")
    assert _repository()._short_audit_columns(Broken()) == []


def test_startup_warns_with_the_migration_to_apply(monkeypatch, caplog):
    repository = _repository()
    monkeypatch.setattr(repository, "readiness_check",
                        lambda: {"available": True, "short_audit_columns": ["policy_version"]})
    with caplog.at_level(logging.WARNING):
        repository._ensure_chatbot_log_table()
    assert "20261109_chatbot_log_policy_version_length.sql" in caplog.text


def test_save_failure_names_code_and_column_but_not_the_value():
    class DataError(Exception):
        pass
    too_long = DataError(1406, "Data too long for column 'policy_version' at row 1")
    assert _database_error_detail(too_long) == " code=1406 column=policy_version"
    bad_value = DataError(1366, "Incorrect string value: '\\xED\\x99\\x8D\\xEA\\xB8\\xB8' for column 'message' at row 1")
    detail = _database_error_detail(bad_value)
    assert detail == " code=1366 column=message"
    assert "\\x" not in detail
