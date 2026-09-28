import os
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.models import ActivityRecord
from app.infrastructure.repository import (
    InMemoryActivityRepository,
    InMemoryProductRepository,
    MySQLActivityRepository,
    MySQLProductRepository,
    SQLiteActivityRepository,
    SQLiteProductRepository,
    dispose_mysql_connection_pools,
)
from app.services.activity import get_activity_repository, set_activity_repository
from app.services.product import get_product_repository, set_product_repository


@pytest.fixture(autouse=True)
def restore_repositories():
    previous_product_repository = get_product_repository()
    previous_activity_repository = get_activity_repository()
    try:
        yield
    finally:
        set_product_repository(previous_product_repository)
        set_activity_repository(previous_activity_repository)


@pytest.mark.parametrize("invalid_value", ["", "flase", "garbage", "2"])
def test_sensitive_boolean_flags_fail_closed_for_empty_or_invalid_values(invalid_value):
    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR": invalid_value,
            "FOOD_CHATBOT_SHARED_REPLAY_PROTECTION": invalid_value,
        },
        clear=False,
    ):
        assert api_main._db_error_fallback_enabled() is False
        assert api_main._shared_replay_protection_asserted() is False


def test_sensitive_boolean_flags_accept_only_explicit_true_values():
    for true_value in ["1", "true", "TRUE", "yes", "on"]:
        with patch.dict(
            os.environ,
            {
                "FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR": true_value,
                "FOOD_CHATBOT_SHARED_REPLAY_PROTECTION": true_value,
            },
            clear=False,
        ):
            assert api_main._db_error_fallback_enabled() is True
            assert api_main._shared_replay_protection_asserted() is True


def test_missing_database_provider_does_not_silently_enable_sample_products():
    set_product_repository(InMemoryProductRepository())
    set_activity_repository(InMemoryActivityRepository())
    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_DB_PROVIDER": "",
            "FOOD_CHATBOT_DB_PATH": "",
        },
        clear=False,
    ):
        assert api_main._configured_db_provider() == "unconfigured"
        with pytest.raises(RuntimeError, match="Set it explicitly"):
            api_main.configure_repositories()
        response = TestClient(api_main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["provider_matches_configuration"] is False


def test_blank_feature_flags_do_not_open_operator_endpoints():
    set_product_repository(InMemoryProductRepository())
    set_activity_repository(InMemoryActivityRepository())
    client = TestClient(api_main.app)

    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_DB_PROVIDER": "memory",
            "FOOD_CHATBOT_ENABLE_DIAGNOSTICS": "",
            "FOOD_CHATBOT_ENABLE_HISTORY_API": "",
            "FOOD_CHATBOT_ADMIN_TOKEN": "test-admin-token",
        },
        clear=False,
    ):
        headers = {"X-Food-Chatbot-Admin-Token": "test-admin-token"}
        assert client.get("/diagnostics/runtime", headers=headers).status_code == 404
        assert client.get("/history/user/1", headers=headers).status_code == 404


def test_public_chat_does_not_attribute_a_body_claimed_user_id_by_default():
    set_product_repository(InMemoryProductRepository())
    activity_repository = InMemoryActivityRepository()
    set_activity_repository(activity_repository)
    client = TestClient(api_main.app)

    with patch.dict(
        os.environ,
        {"FOOD_CHATBOT_ENABLE_TRUSTED_HISTORY_WRITES": "false"},
        clear=False,
    ):
        response = client.post(
            "/chat",
            json={
                "message": "저염식이면 뭘 확인해야 해?",
                "profile": {
                    "profile_name": "신원 위조 프로필",
                    "user_id": 31337,
                    "allergies": [],
                    "conditions": [],
                    "special_diets": ["저염식"],
                },
            },
        )

    assert response.status_code == 200
    assert activity_repository.list_by_user_id(31337) == []
    assert len(activity_repository.records) == 1
    assert activity_repository.records[0].user_id is None


def test_readiness_rejects_sqlite_without_products_table(tmp_path):
    db_path = tmp_path / "empty.sqlite3"
    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_DB_PROVIDER": "sqlite",
            "FOOD_CHATBOT_DB_PATH": str(db_path),
        },
        clear=False,
    ):
        api_main.configure_repositories()
        response = TestClient(api_main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["product_repository_available"] is False


def test_readiness_accepts_sqlite_with_usable_product_schema(tmp_path):
    db_path = tmp_path / "products.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                barcode TEXT,
                product_name TEXT,
                raw_materials TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO products (barcode, product_name, raw_materials)
            VALUES ('8800000000001', '테스트 제품', '우유')
            """
        )
        conn.commit()

    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_DB_PROVIDER": "sqlite",
            "FOOD_CHATBOT_DB_PATH": str(db_path),
        },
        clear=False,
    ):
        api_main.configure_repositories()
        response = TestClient(api_main.app).get("/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["audit_log_ready"] is True


def test_readiness_rejects_sqlite_with_empty_product_catalog(tmp_path):
    db_path = tmp_path / "empty-products.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                barcode TEXT,
                product_name TEXT,
                raw_materials TEXT
            )
            """
        )
        conn.commit()

    repository = SQLiteProductRepository(str(db_path))
    repository_status = repository.readiness_check()
    assert repository_status["available"] is False
    assert repository_status["product_row_available"] is False
    assert repository_status["reason"] == "product_catalog_empty"

    with patch.dict(
        os.environ,
        {
            "FOOD_CHATBOT_DB_PROVIDER": "sqlite",
            "FOOD_CHATBOT_DB_PATH": str(db_path),
        },
        clear=False,
    ):
        api_main.configure_repositories()
        response = TestClient(api_main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["product_repository_available"] is False


def test_sqlite_activity_readiness_requires_every_column_used_by_writes(tmp_path):
    db_path = tmp_path / "partial-activity.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE activity_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT NOT NULL,
                message TEXT,
                intent TEXT,
                risk_level TEXT,
                fallback_reason TEXT,
                validation_failure_types TEXT,
                critical_safety_violation INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    repository = SQLiteActivityRepository(str(db_path))
    status = repository.readiness_check()

    assert status["available"] is False
    assert status["audit_log_ready"] is False
    assert status["missing_required_column_count"] > 0


def test_repository_readiness_does_not_call_detailed_health_check():
    class CheapReadinessRepository:
        def readiness_check(self):
            return {"available": True}

        def health_check(self):
            raise AssertionError("Detailed health check must not run from /ready.")

    assert api_main._repository_readiness(CheapReadinessRepository()) == {
        "available": True
    }


def test_mysql_activity_repository_disables_legacy_history_writes_by_default():
    activity = ActivityRecord(
        user_id=424242,
        barcode="8801234567890",
        action_type="SCAN",
        message="이거 먹어도 돼?",
    )
    default_repository = MySQLActivityRepository(password="test")
    default_calls = []
    default_repository._save_chatbot_log = lambda record: default_calls.append(("audit", record))
    default_repository._save_legacy_history = lambda record: default_calls.append(("legacy", record))

    default_repository.save(activity)

    assert [kind for kind, _ in default_calls] == ["audit"]

    trusted_repository = MySQLActivityRepository(
        password="test",
        enable_legacy_history_writes=True,
    )
    trusted_calls = []
    trusted_repository._save_chatbot_log = lambda record: trusted_calls.append(("audit", record))
    trusted_repository._save_legacy_history = lambda record: trusted_calls.append(("legacy", record))

    trusted_repository.save(activity)

    assert [kind for kind, _ in trusted_calls] == ["audit", "legacy"]


def test_mysql_repositories_pass_explicit_socket_timeouts_to_pymysql(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_CONNECT_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_READ_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_WRITE_TIMEOUT_SECONDS", "12.75")

    captured_calls = []

    class FakeConnection:
        def ping(self, reconnect=False):
            assert reconnect is False

        def rollback(self):
            return None

        def close(self):
            return None

    class FakePyMySQL:
        def connect(self, **kwargs):
            captured_calls.append(kwargs)
            return FakeConnection()

    repositories = [
        MySQLProductRepository(password="test"),
        MySQLActivityRepository(password="test"),
    ]
    for repository in repositories:
        repository._pymysql = FakePyMySQL()
        repository._connect().close()
    dispose_mysql_connection_pools()

    assert len(captured_calls) == 2
    for kwargs in captured_calls:
        assert kwargs["connect_timeout"] == 2.5
        assert kwargs["read_timeout"] == 11.0
        assert kwargs["write_timeout"] == 12.75


@pytest.mark.parametrize(
    "invalid_value",
    [None, "", "0", "-1", "NaN", "inf", "3600.1", "1e100", "not-a-number"],
)
def test_mysql_repositories_use_safe_defaults_for_missing_or_invalid_timeouts(
    monkeypatch,
    invalid_value,
):
    timeout_names = [
        "FOOD_CHATBOT_MYSQL_CONNECT_TIMEOUT_SECONDS",
        "FOOD_CHATBOT_MYSQL_READ_TIMEOUT_SECONDS",
        "FOOD_CHATBOT_MYSQL_WRITE_TIMEOUT_SECONDS",
    ]
    for name in timeout_names:
        if invalid_value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, invalid_value)

    for repository in [
        MySQLProductRepository(password="test"),
        MySQLActivityRepository(password="test"),
    ]:
        assert repository.connection_kwargs["connect_timeout"] == 10.0
        assert repository.connection_kwargs["read_timeout"] == 30.0
        assert repository.connection_kwargs["write_timeout"] == 30.0


def test_mysql_product_search_index_check_is_read_only_even_when_legacy_flag_is_true():
    executed_queries = []

    class FakeCursor:
        def __init__(self):
            self.last_query = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, _params=None):
            self.last_query = query
            executed_queries.append(query)

        def fetchall(self):
            if "SHOW COLUMNS" in self.last_query:
                return [
                    {"Field": "id"},
                    {"Field": "product_name"},
                    {"Field": "search_keywords"},
                    {"Field": "raw_materials"},
                ]
            return []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    repository = MySQLProductRepository(
        password="test",
        ensure_search_indexes=True,
    )
    repository._connect = lambda: FakeConnection()

    status = repository.ensure_search_index()

    assert status["runtime_ddl_enabled"] is False
    assert status["migration_required"] is True
    assert status["schema_change_source"] == "AI/migrations"
    assert executed_queries
    assert all(
        not query.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
        for query in executed_queries
    )


def test_mysql_activity_schema_validation_fails_closed_without_runtime_ddl():
    repository = MySQLActivityRepository(password="test")
    repository.readiness_check = lambda: {
        "available": False,
        "audit_log_ready": False,
        "reason": "mysql_activity_schema_not_ready",
        "missing_audit_column_count": 3,
    }

    with pytest.raises(RuntimeError, match="20260909_chatbot_log_product_trace.sql"):
        repository.ensure_chatbot_log_table()

    assert repository._chatbot_log_schema_ready is False


def test_mysql_startup_rejects_missing_product_or_activity_schema(monkeypatch):
    class FakeProductRepository:
        def __init__(self, status):
            self.status = status

        def readiness_check(self):
            return self.status

    class FakeActivityRepository:
        def __init__(self, error=None):
            self.error = error
            self.validation_calls = 0

        def ensure_chatbot_log_table(self):
            self.validation_calls += 1
            if self.error:
                raise self.error

    monkeypatch.setenv("FOOD_CHATBOT_DB_PROVIDER", "mysql")
    monkeypatch.setenv("FOOD_CHATBOT_FALLBACK_TO_MEMORY_ON_DB_ERROR", "true")

    missing_product = FakeProductRepository({
        "available": False,
        "reason": "product_safety_schema_incomplete",
    })
    unused_activity = FakeActivityRepository()
    with patch.object(
        api_main,
        "MySQLProductRepository",
        return_value=missing_product,
    ), patch.object(
        api_main,
        "MySQLActivityRepository",
        return_value=unused_activity,
    ):
        with pytest.raises(RuntimeError, match="product schema/catalog"):
            api_main.configure_repositories()
    assert unused_activity.validation_calls == 0

    ready_product = FakeProductRepository({"available": True})
    missing_activity = FakeActivityRepository(RuntimeError(
        "MySQL chatbot audit schema is not ready; apply AI/migrations before startup."
    ))
    with patch.object(
        api_main,
        "MySQLProductRepository",
        return_value=ready_product,
    ), patch.object(
        api_main,
        "MySQLActivityRepository",
        return_value=missing_activity,
    ):
        with pytest.raises(RuntimeError, match="apply AI/migrations"):
            api_main.configure_repositories()
    assert missing_activity.validation_calls == 1

    unavailable_product = FakeProductRepository({
        "available": False,
        "reason": "mysql_product_check_failed",
        "error_type": "OSError",
    })
    with patch.object(
        api_main,
        "MySQLProductRepository",
        return_value=unavailable_product,
    ), patch.object(
        api_main,
        "MySQLActivityRepository",
        return_value=FakeActivityRepository(),
    ):
        api_main.configure_repositories()
    assert isinstance(get_product_repository(), InMemoryProductRepository)
    assert isinstance(get_activity_repository(), InMemoryActivityRepository)


def test_mysql_activity_readiness_requires_audit_log_schema():
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    repository = MySQLActivityRepository(password="test")
    repository._connect = lambda: FakeConnection()
    repository._table_exists = lambda _conn, table: table != repository.chat_log_table_name
    repository._table_columns = lambda _conn, _table: set()

    missing_status = repository.readiness_check()

    assert missing_status["available"] is False
    assert missing_status["audit_log_ready"] is False

    repository._table_exists = lambda _conn, _table: True
    repository._table_columns = (
        lambda _conn, table: repository.CHATBOT_LOG_REQUIRED_COLUMNS
        if table == repository.quoted_chat_log_table_name
        else {"user_id", "barcode", "actionType"}
    )

    ready_status = repository.readiness_check()

    assert ready_status["available"] is True
    assert ready_status["audit_log_ready"] is True


def test_product_repositories_support_authoritative_product_id_lookup(tmp_path):
    memory_repository = InMemoryProductRepository(
        seed_data={
            "custom": {
                "id": 17,
                "product_name": "ID 조회 제품",
                "barcode": "8800000000017",
            }
        }
    )
    assert memory_repository.get_by_product_id(17).product_name == "ID 조회 제품"
    assert memory_repository.get_by_product_id(True) is None

    db_path = tmp_path / "product-id.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE products (id INTEGER PRIMARY KEY, product_name TEXT, barcode TEXT)"
        )
        conn.execute(
            "INSERT INTO products (id, product_name, barcode) VALUES (?, ?, ?)",
            (17, "SQLite ID 제품", "8800000000017"),
        )
        conn.commit()

    sqlite_repository = SQLiteProductRepository(str(db_path))
    assert sqlite_repository.get_by_product_id(17).product_name == "SQLite ID 제품"
    assert sqlite_repository.get_by_product_id(-1) is None


def test_mysql_product_id_lookup_is_parameterized_and_fails_safe():
    executions = []

    class FakeCursor:
        def __init__(self):
            self.last_query = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params=None):
            self.last_query = query
            executions.append((query, params))

        def fetchall(self):
            return [{"Field": "id"}, {"Field": "product_name"}, {"Field": "barcode"}]

        def fetchone(self):
            return {
                "id": 17,
                "product_name": "MySQL ID 제품",
                "barcode": "8800000000017",
            }

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    repository = MySQLProductRepository(password="test")
    repository._connect = lambda: FakeConnection()

    product = repository.get_by_product_id(17)

    assert product.product_name == "MySQL ID 제품"
    select_query, select_params = executions[-1]
    assert "= %s" in select_query
    assert select_params == (17,)

    repository._connect = lambda: (_ for _ in ()).throw(OSError("database unavailable"))
    assert repository.get_by_product_id(17) is None


def test_mysql_product_readiness_requires_schema_and_nonempty_catalog():
    class FakeCursor:
        def __init__(self, fields, product_row):
            self.fields = fields
            self.product_row = product_row

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return self.product_row

        def fetchall(self):
            return [{"Field": field} for field in self.fields]

    class FakeConnection:
        def __init__(self, fields, product_row):
            self.fields = fields
            self.product_row = product_row

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return FakeCursor(self.fields, self.product_row)

    repository = MySQLProductRepository(password="test")
    repository._connect = lambda: FakeConnection(
        ["created_at", "memo"],
        {"ok": 1},
    )

    missing_status = repository.readiness_check()

    assert missing_status["available"] is False
    assert missing_status["reason"] == "product_safety_schema_incomplete"

    repository._connect = lambda: FakeConnection(
        ["id", "product_name", "raw_materials"],
        {"ok": 1},
    )

    ready_status = repository.readiness_check()

    assert ready_status["available"] is True
    assert ready_status["identity_column_available"] is True
    assert ready_status["product_name_column_available"] is True
    assert ready_status["safety_evidence_column_available"] is True
    assert ready_status["product_row_available"] is True

    repository._connect = lambda: FakeConnection(
        ["id", "product_name", "raw_materials"],
        product_row=None,
    )

    empty_status = repository.readiness_check()

    assert empty_status["available"] is False
    assert empty_status["product_row_available"] is False
    assert empty_status["reason"] == "product_catalog_empty"
