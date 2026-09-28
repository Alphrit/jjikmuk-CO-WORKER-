"""mysql support repository implementation."""

from threading import Lock
from typing import Any, Dict
import math
import os

import logging

logger = logging.getLogger(__name__)


MYSQL_CONNECT_TIMEOUT_SECONDS_DEFAULT = 10.0


MYSQL_READ_TIMEOUT_SECONDS_DEFAULT = 30.0


MYSQL_WRITE_TIMEOUT_SECONDS_DEFAULT = 30.0


MYSQL_TIMEOUT_SECONDS_MAX = 3600.0


MYSQL_POOL_SIZE_DEFAULT = 10


MYSQL_POOL_SIZE_MAX = 64


MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT = 5.0


MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MIN = 0.1


MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MAX = 60.0


MYSQL_POOL_RECYCLE_SECONDS_DEFAULT = 1800.0


MYSQL_POOL_RECYCLE_SECONDS_MIN = 30.0


MYSQL_POOL_RECYCLE_SECONDS_MAX = 86400.0


def _positive_mysql_timeout_seconds(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value.strip())
    except (TypeError, ValueError, OverflowError):
        value = float("nan")
    if (
        not math.isfinite(value)
        or value <= 0
        or value > MYSQL_TIMEOUT_SECONDS_MAX
    ):
        logger.warning(
            "Invalid positive MySQL timeout environment variable %s; using %.1f seconds.",
            name,
            default,
        )
        return default
    return value


def _mysql_connection_timeout_kwargs() -> Dict[str, float]:
    return {
        "connect_timeout": _positive_mysql_timeout_seconds(
            "FOOD_CHATBOT_MYSQL_CONNECT_TIMEOUT_SECONDS",
            MYSQL_CONNECT_TIMEOUT_SECONDS_DEFAULT,
        ),
        "read_timeout": _positive_mysql_timeout_seconds(
            "FOOD_CHATBOT_MYSQL_READ_TIMEOUT_SECONDS",
            MYSQL_READ_TIMEOUT_SECONDS_DEFAULT,
        ),
        "write_timeout": _positive_mysql_timeout_seconds(
            "FOOD_CHATBOT_MYSQL_WRITE_TIMEOUT_SECONDS",
            MYSQL_WRITE_TIMEOUT_SECONDS_DEFAULT,
        ),
    }


def mysql_tls_configuration_status() -> Dict[str, bool]:
    """Return a path-free summary of the configured MySQL TLS boundary."""

    ca_configured = bool((os.getenv("FOOD_CHATBOT_MYSQL_SSL_CA") or "").strip())
    client_cert_configured = bool(
        (os.getenv("FOOD_CHATBOT_MYSQL_SSL_CERT") or "").strip()
    )
    client_key_configured = bool(
        (os.getenv("FOOD_CHATBOT_MYSQL_SSL_KEY") or "").strip()
    )
    return {
        "ca_configured": ca_configured,
        "client_cert_configured": client_cert_configured,
        "client_key_configured": client_key_configured,
        "client_certificate_pair_valid": (
            client_cert_configured == client_key_configured
        ),
        "server_certificate_verification_enabled": ca_configured,
        "server_identity_verification_enabled": ca_configured,
    }


def _mysql_connection_tls_kwargs() -> Dict[str, Any]:
    """Build verified PyMySQL TLS arguments without supporting insecure TLS."""

    ca_path = (os.getenv("FOOD_CHATBOT_MYSQL_SSL_CA") or "").strip()
    if not ca_path:
        return {}

    kwargs: Dict[str, Any] = {
        "ssl_ca": ca_path,
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }
    cert_path = (os.getenv("FOOD_CHATBOT_MYSQL_SSL_CERT") or "").strip()
    key_path = (os.getenv("FOOD_CHATBOT_MYSQL_SSL_KEY") or "").strip()
    if cert_path and key_path:
        kwargs.update(
            {
                "ssl_cert": cert_path,
                "ssl_key": key_path,
            }
        )
    return kwargs


def _bounded_mysql_integer(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip(), 10)
    except (TypeError, ValueError, OverflowError):
        value = minimum - 1
    if value < minimum or value > maximum:
        logger.warning(
            "Invalid bounded MySQL integer environment variable %s; using %d.",
            name,
            default,
        )
        return default
    return value


def _bounded_mysql_seconds(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value.strip())
    except (TypeError, ValueError, OverflowError):
        value = float("nan")
    if not math.isfinite(value) or value < minimum or value > maximum:
        logger.warning(
            "Invalid bounded MySQL seconds environment variable %s; using %.1f.",
            name,
            default,
        )
        return default
    return value


def _mysql_connection_pool_settings() -> Dict[str, Any]:
    return {
        "pool_size": _bounded_mysql_integer(
            "FOOD_CHATBOT_MYSQL_POOL_SIZE",
            MYSQL_POOL_SIZE_DEFAULT,
            minimum=1,
            maximum=MYSQL_POOL_SIZE_MAX,
        ),
        "acquire_timeout_seconds": _bounded_mysql_seconds(
            "FOOD_CHATBOT_MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS",
            MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_DEFAULT,
            minimum=MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MIN,
            maximum=MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS_MAX,
        ),
        "recycle_seconds": _bounded_mysql_seconds(
            "FOOD_CHATBOT_MYSQL_POOL_RECYCLE_SECONDS",
            MYSQL_POOL_RECYCLE_SECONDS_DEFAULT,
            minimum=MYSQL_POOL_RECYCLE_SECONDS_MIN,
            maximum=MYSQL_POOL_RECYCLE_SECONDS_MAX,
        ),
    }


class _SharedMySQLConnectionPool:
    """Bounded PyMySQL pool preserving the repositories' close/context contract."""

    def __init__(
        self,
        pymysql_module,
        connection_kwargs: Dict[str, Any],
        settings: Dict[str, Any],
    ):
        try:
            from sqlalchemy import event
            from sqlalchemy.exc import DisconnectionError
            from sqlalchemy.exc import InvalidRequestError
            from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeoutError
            from sqlalchemy.pool import QueuePool
        except ImportError as exc:
            raise RuntimeError(
                "MySQL connection pooling requires SQLAlchemy from requirements.txt."
            ) from exc

        self.connection_kwargs = dict(connection_kwargs)
        # PyMySQL executes init_command for every newly created physical
        # connection. Connection establishment fails if the server refuses the
        # UTC session contract, instead of silently interpreting DATETIME label
        # observations in the host/session timezone.
        self.connection_kwargs.setdefault(
            "init_command",
            "SET time_zone = '+00:00'",
        )
        self.pool_size = int(settings["pool_size"])
        self.acquire_timeout_seconds = float(settings["acquire_timeout_seconds"])
        self.recycle_seconds = float(settings["recycle_seconds"])
        self._pool_timeout_error = SQLAlchemyPoolTimeoutError
        self._invalid_connection_error = InvalidRequestError
        self._disconnection_error = DisconnectionError
        self._disposed = False
        self._pool = QueuePool(
            lambda: pymysql_module.connect(**self.connection_kwargs),
            pool_size=self.pool_size,
            max_overflow=0,
            timeout=self.acquire_timeout_seconds,
            recycle=self.recycle_seconds,
            reset_on_return="rollback",
            use_lifo=True,
        )
        event.listen(self._pool, "checkout", self._pre_ping)
        event.listen(self._pool, "checkin", self._close_if_disposed)

    def _pre_ping(self, dbapi_connection, _connection_record, _connection_proxy) -> None:
        try:
            dbapi_connection.ping(reconnect=False)
        except Exception as exc:
            raise self._disconnection_error(
                "MySQL pooled connection failed its checkout ping."
            ) from exc

    def _close_if_disposed(self, dbapi_connection, connection_record) -> None:
        if self._disposed and dbapi_connection is not None:
            connection_record.invalidate()

    def connect(self):
        if self._disposed:
            raise RuntimeError("MySQL connection pool is disposed.")
        try:
            return self._pool.connect()
        except self._pool_timeout_error:
            raise TimeoutError(
                "MySQL connection pool exhausted before the acquire timeout."
            ) from None
        except self._invalid_connection_error:
            raise OSError(
                "MySQL connection pool could not establish a healthy connection."
            ) from None

    def dispose(self) -> None:
        self._disposed = True
        self._pool.dispose()


_MYSQL_CONNECTION_POOLS: Dict[tuple[Any, ...], _SharedMySQLConnectionPool] = {}


_MYSQL_CONNECTION_POOLS_LOCK = Lock()


def _mysql_pool_key_value(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return (type(value).__qualname__, id(value))
    return value


def _shared_mysql_connection_pool(
    pymysql_module,
    connection_kwargs: Dict[str, Any],
    settings: Dict[str, Any],
) -> _SharedMySQLConnectionPool:
    key = (
        id(pymysql_module),
        tuple(
            sorted(
                (name, _mysql_pool_key_value(value))
                for name, value in connection_kwargs.items()
            )
        ),
        int(settings["pool_size"]),
        float(settings["acquire_timeout_seconds"]),
        float(settings["recycle_seconds"]),
    )
    with _MYSQL_CONNECTION_POOLS_LOCK:
        pool = _MYSQL_CONNECTION_POOLS.get(key)
        if pool is None:
            pool = _SharedMySQLConnectionPool(
                pymysql_module,
                connection_kwargs,
                settings,
            )
            _MYSQL_CONNECTION_POOLS[key] = pool
        return pool


def dispose_mysql_connection_pools() -> None:
    """Close all currently idle shared MySQL connections during app shutdown."""

    with _MYSQL_CONNECTION_POOLS_LOCK:
        pools = list(_MYSQL_CONNECTION_POOLS.values())
        _MYSQL_CONNECTION_POOLS.clear()
    for pool in pools:
        pool.dispose()


def _quote_mysql_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Invalid MySQL identifier: {identifier}")
    return f"`{identifier}`"
