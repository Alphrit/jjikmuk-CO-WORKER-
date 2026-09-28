import asyncio
import time
from unittest.mock import patch

import pytest

from app.api import main as api_main
from app.infrastructure.conversations import MySQLConversationRepository
from app.infrastructure.repository import (
    MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT,
    MYSQL_POOL_RECYCLE_SECONDS_DEFAULT,
    MYSQL_POOL_SIZE_DEFAULT,
    MySQLActivityRepository,
    MySQLProductRepository,
    dispose_mysql_connection_pools,
)


class FakeConnection:
    def __init__(self, *, stale_after_ping=None):
        self.stale_after_ping = stale_after_ping
        self.ping_calls = 0
        self.rollback_calls = 0
        self.commit_calls = 0
        self.physically_closed = False

    def ping(self, reconnect=False):
        assert reconnect is False
        self.ping_calls += 1
        if (
            self.stale_after_ping is not None
            and self.ping_calls > self.stale_after_ping
        ):
            raise OSError("stale test connection")

    def rollback(self):
        self.rollback_calls += 1

    def commit(self):
        self.commit_calls += 1

    def close(self):
        self.physically_closed = True


class FakePyMySQL:
    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or FakeConnection
        self.connections = []
        self.connect_kwargs = []

    def connect(self, **kwargs):
        connection = self.connection_factory()
        self.connections.append(connection)
        self.connect_kwargs.append(kwargs)
        return connection


@pytest.fixture(autouse=True)
def clean_shared_mysql_pools():
    dispose_mysql_connection_pools()
    try:
        yield
    finally:
        dispose_mysql_connection_pools()


def _repositories_using(fake_pymysql):
    product_repository = MySQLProductRepository(password="test")
    activity_repository = MySQLActivityRepository(password="test")
    product_repository._pymysql = fake_pymysql
    activity_repository._pymysql = fake_pymysql
    return product_repository, activity_repository


def test_product_and_activity_share_a_bounded_pool_and_rollback_on_return(
    monkeypatch,
):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "2")
    fake_pymysql = FakePyMySQL()
    product_repository, activity_repository = _repositories_using(fake_pymysql)

    with product_repository._connect() as first_lease:
        first_connection = first_lease.dbapi_connection

    assert first_connection.rollback_calls == 1
    assert first_connection.commit_calls == 0

    with activity_repository._connect() as second_lease:
        assert second_lease.dbapi_connection is first_connection

    assert len(fake_pymysql.connections) == 1
    assert first_connection.ping_calls == 2
    assert first_connection.rollback_calls == 2
    assert first_connection.physically_closed is False

    dispose_mysql_connection_pools()

    assert first_connection.physically_closed is True


def test_all_mysql_repositories_share_verified_tls_connection_kwargs(monkeypatch):
    ca_path = "/run/secrets/mysql/ca.pem"
    cert_path = "/run/secrets/mysql/client-cert.pem"
    key_path = "/run/secrets/mysql/client-key.pem"
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_SSL_CA", ca_path)
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_SSL_CERT", cert_path)
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_SSL_KEY", key_path)
    fake_pymysql = FakePyMySQL()
    repositories = [
        MySQLProductRepository(password="test"),
        MySQLActivityRepository(password="test"),
        MySQLConversationRepository(password="test"),
    ]
    for repository in repositories:
        repository._pymysql = fake_pymysql
        assert repository.connection_kwargs["ssl_ca"] == ca_path
        assert repository.connection_kwargs["ssl_cert"] == cert_path
        assert repository.connection_kwargs["ssl_key"] == key_path
        assert repository.connection_kwargs["ssl_verify_cert"] is True
        assert repository.connection_kwargs["ssl_verify_identity"] is True
        with repository._connect():
            pass

    assert len(fake_pymysql.connections) == 1
    assert len(fake_pymysql.connect_kwargs) == 1


def test_pool_exhaustion_raises_bounded_builtin_timeout(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "1")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS", "0.1")
    fake_pymysql = FakePyMySQL()
    product_repository, activity_repository = _repositories_using(fake_pymysql)

    checked_out = product_repository._connect()
    started_at = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="pool exhausted"):
            activity_repository._connect()
    finally:
        checked_out.close()

    assert time.monotonic() - started_at < 1.0
    assert len(fake_pymysql.connections) == 1


def test_dispose_closes_idle_and_late_returned_connections(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "2")
    fake_pymysql = FakePyMySQL()
    product_repository, _activity_repository = _repositories_using(fake_pymysql)

    checked_out = product_repository._connect()
    checked_out_connection = checked_out.dbapi_connection
    idle = product_repository._connect()
    idle_connection = idle.dbapi_connection
    idle.close()

    dispose_mysql_connection_pools()

    assert idle_connection.physically_closed is True
    assert checked_out_connection.physically_closed is False

    checked_out.close()

    assert checked_out_connection.physically_closed is True
    with pytest.raises(RuntimeError, match="disposed"):
        product_repository._connect()


def test_checkout_ping_discards_a_stale_connection(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "1")
    created_count = 0

    def connection_factory():
        nonlocal created_count
        created_count += 1
        return FakeConnection(stale_after_ping=1 if created_count == 1 else None)

    fake_pymysql = FakePyMySQL(connection_factory)
    product_repository, _activity_repository = _repositories_using(fake_pymysql)

    with product_repository._connect() as first_lease:
        first_connection = first_lease.dbapi_connection

    with product_repository._connect() as replacement_lease:
        replacement_connection = replacement_lease.dbapi_connection

    assert replacement_connection is not first_connection
    assert len(fake_pymysql.connections) == 2
    assert first_connection.physically_closed is True


def test_repeated_ping_failures_raise_a_sanitized_database_error(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "1")
    fake_pymysql = FakePyMySQL(
        lambda: FakeConnection(stale_after_ping=0)
    )
    product_repository, _activity_repository = _repositories_using(fake_pymysql)

    with pytest.raises(OSError, match="healthy connection") as error:
        product_repository._connect()

    assert "stale test connection" not in str(error.value)
    assert len(fake_pymysql.connections) >= 2
    assert all(connection.physically_closed for connection in fake_pymysql.connections)


def test_recycle_discards_an_expired_connection(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "1")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_RECYCLE_SECONDS", "30")
    fake_pymysql = FakePyMySQL()
    product_repository, _activity_repository = _repositories_using(fake_pymysql)

    first_lease = product_repository._connect()
    first_connection = first_lease.dbapi_connection
    connection_record = first_lease._connection_record
    first_lease.close()
    connection_record.starttime -= 31

    with product_repository._connect() as replacement_lease:
        replacement_connection = replacement_lease.dbapi_connection

    assert replacement_connection is not first_connection
    assert len(fake_pymysql.connections) == 2
    assert first_connection.physically_closed is True


@pytest.mark.parametrize(
    ("name", "invalid_values", "setting", "expected"),
    [
        (
            "FOOD_CHATBOT_MYSQL_POOL_SIZE",
            ["", "0", "-1", "1.5", "65", "NaN", "not-a-number"],
            "pool_size",
            MYSQL_POOL_SIZE_DEFAULT,
        ),
        (
            "FOOD_CHATBOT_MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS",
            ["", "0", "0.09", "60.1", "NaN", "inf", "not-a-number"],
            "acquire_timeout_seconds",
            MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT,
        ),
        (
            "FOOD_CHATBOT_MYSQL_POOL_RECYCLE_SECONDS",
            ["", "0", "29.9", "86400.1", "NaN", "inf", "not-a-number"],
            "recycle_seconds",
            MYSQL_POOL_RECYCLE_SECONDS_DEFAULT,
        ),
    ],
)
def test_invalid_pool_settings_fail_safe_to_bounded_defaults(
    monkeypatch,
    name,
    invalid_values,
    setting,
    expected,
):
    for invalid_value in invalid_values:
        monkeypatch.setenv(name, invalid_value)
        repository = MySQLProductRepository(password="test")
        assert repository.connection_pool_settings[setting] == expected


def test_valid_pool_settings_are_shared_by_both_repositories(monkeypatch):
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_SIZE", "7")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("FOOD_CHATBOT_MYSQL_POOL_RECYCLE_SECONDS", "900")

    product_repository = MySQLProductRepository(password="test")
    activity_repository = MySQLActivityRepository(password="test")

    expected = {
        "pool_size": 7,
        "acquire_timeout_seconds": 2.5,
        "recycle_seconds": 900.0,
    }
    assert product_repository.connection_pool_settings == expected
    assert activity_repository.connection_pool_settings == expected


def test_lifespan_disposes_pools_when_startup_validation_fails():
    dispose_calls = []

    async def run_lifespan():
        async with api_main.lifespan(api_main.app):
            raise AssertionError("startup failure must prevent yielding")

    with patch.object(
        api_main,
        "configure_repositories",
        side_effect=RuntimeError("schema not ready"),
    ), patch.object(
        api_main,
        "dispose_mysql_connection_pools",
        side_effect=lambda: dispose_calls.append(True),
    ):
        with pytest.raises(RuntimeError, match="schema not ready"):
            asyncio.run(run_lifespan())

    assert dispose_calls == [True]
