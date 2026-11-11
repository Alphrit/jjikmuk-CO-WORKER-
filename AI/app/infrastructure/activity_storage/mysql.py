"""activity storage / mysql repository implementation."""

from app.core.models import ActivityRecord
from threading import Lock
from typing import Any, Dict, List, Optional
import hashlib
import json
import re

from app.infrastructure.activity_storage.common import (
    ActivityRepository,
    _decode_response_product_label_trace,
    _fallback_log_payload,
    _fallback_summary_from_records,
    _normalize_nullable_activity_list_fields,
    _response_product_label_trace_json,
)

from app.infrastructure.mysql_support import (
    _mysql_connection_pool_settings,
    _mysql_connection_timeout_kwargs,
    _mysql_connection_tls_kwargs,
    _quote_mysql_identifier,
    _shared_mysql_connection_pool,
)

import logging

logger = logging.getLogger(__name__)


class MySQLActivityRepository(ActivityRepository):
    # The base chatbot_logs table. Columns added by later AI migrations
    # (20260730/20260828 audit fields, 20260909 product trace) are written only
    # when the deployed DB has them, so an older DB keeps logging and /ready.
    CHATBOT_LOG_REQUIRED_COLUMNS = {
        "id",
        "user_id",
        "barcode",
        "report_number",
        "product_id",
        "action_type",
        "message",
        "intent",
        "answer_source",
        "pipeline_stage",
        "risk_level",
        "created_at",
    }
    # Columns whose width must hold what the backend sends. Without the widening
    # migration a longer value is shortened to fit (_fit_column), so the row
    # is still saved and no DB change is needed.
    CHATBOT_LOG_MIN_CHAR_LENGTH = {
        "policy_version": (256, "AI/migrations/20261109_chatbot_log_policy_version_length.sql"),
    }

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        database: str = "jjikmuk",
        user: str = "root",
        password: str = "",
        table_name: str = "histories",
        chat_log_table_name: str = "chatbot_logs",
        charset: str = "utf8mb4",
        enable_legacy_history_writes: bool = False,
    ):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError(
                "MySQL DB를 사용하려면 PyMySQL이 필요합니다. "
                "`pip install -r requirements.txt`를 다시 실행해주세요."
            ) from exc

        self._pymysql = pymysql
        self.connection_kwargs = {
            "host": host,
            "port": int(port),
            "database": database,
            "user": user,
            "password": password,
            "charset": charset,
            "cursorclass": DictCursor,
            **_mysql_connection_timeout_kwargs(),
            **_mysql_connection_tls_kwargs(),
        }
        self.connection_pool_settings = _mysql_connection_pool_settings()
        self._connection_pool = None
        self.table_name = table_name
        self.quoted_table_name = _quote_mysql_identifier(table_name)
        self.chat_log_table_name = chat_log_table_name
        self.quoted_chat_log_table_name = _quote_mysql_identifier(chat_log_table_name)
        self.enable_legacy_history_writes = enable_legacy_history_writes is True
        self._chatbot_log_schema_ready = False
        self._chatbot_log_schema_lock = Lock()
        self._chatbot_log_columns: set[str] = set()
        self._column_widths: Dict[str, int] = {}

    def _connect(self):
        if self._connection_pool is None:
            self._connection_pool = _shared_mysql_connection_pool(
                self._pymysql,
                self.connection_kwargs,
                self.connection_pool_settings,
            )
        return self._connection_pool.connect()

    def _is_database_error(self, exc: Exception) -> bool:
        return isinstance(exc, (self._pymysql.MySQLError, OSError, TimeoutError))

    def _table_exists(self, conn, table_name: str) -> bool:
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE %s", (table_name,))
            return cursor.fetchone() is not None

    def _table_columns(self, conn, quoted_table_name: str) -> set[str]:
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW COLUMNS FROM {quoted_table_name}")
            return {row["Field"] for row in cursor.fetchall()}

    def _short_audit_columns(self, conn) -> list[str]:
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM {self.quoted_chat_log_table_name}")
                types = {row["Field"]: str(row.get("Type") or "") for row in cursor.fetchall()}
        except Exception:
            return []
        short = []
        for column, (minimum, _migration) in self.CHATBOT_LOG_MIN_CHAR_LENGTH.items():
            width = re.fullmatch(r"(?:var)?char\((\d+)\)", types.get(column, "").lower())
            if width:
                self.__dict__.setdefault("_column_widths", {})[column] = int(width[1])
            if width and int(width[1]) < minimum:
                short.append(column)
        return short

    def _existing_log_columns(self, conn, wanted) -> list[str]:
        try:
            existing = self._table_columns(conn, self.quoted_chat_log_table_name)
        except Exception:  # unknown columns: read them all, as before
            return list(wanted)
        return [column for column in wanted if column in existing] or list(wanted)

    def _fit_column(self, column: str, value):
        """Shorten a value for a narrower existing column instead of losing the row.

        '…-purpose-v5.2' (37 chars) on VARCHAR(32) would fail every insert; a
        shortened value with a hash tail stays distinguishable.
        """
        width = getattr(self, "_column_widths", {}).get(column)
        if not isinstance(value, str) or width is None or len(value) <= width:
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return value[: max(width - 9, 0)] + "~" + digest

    def readiness_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                chatbot_log_exists = self._table_exists(conn, self.chat_log_table_name)
                chatbot_log_columns = (
                    self._table_columns(conn, self.quoted_chat_log_table_name)
                    if chatbot_log_exists
                    else set()
                )
                missing_audit_columns = self.CHATBOT_LOG_REQUIRED_COLUMNS - chatbot_log_columns
                short_audit_columns = self._short_audit_columns(conn) if chatbot_log_exists else []
                audit_log_ready = chatbot_log_exists and not missing_audit_columns

                history_table_exists = self._table_exists(conn, self.table_name)
                users_table_exists = self._table_exists(conn, "users")
                legacy_history_ready = True
                if self.enable_legacy_history_writes:
                    history_columns = (
                        self._table_columns(conn, self.quoted_table_name)
                        if history_table_exists
                        else set()
                    )
                    has_user = bool({"user_id", "userId"} & history_columns)
                    has_barcode = "barcode" in history_columns
                    has_action = bool({"action_type", "actionType"} & history_columns)
                    legacy_history_ready = (
                        history_table_exists
                        and users_table_exists
                        and has_user
                        and has_barcode
                        and has_action
                    )

            available = audit_log_ready and legacy_history_ready
            return {
                "available": available,
                "audit_log_ready": audit_log_ready,
                "table": self.table_name,
                "chat_log_table": self.chat_log_table_name,
                "chat_log_table_exists": chatbot_log_exists,
                "missing_audit_column_count": len(missing_audit_columns),
                "short_audit_columns": short_audit_columns,
                "legacy_history_writes_enabled": self.enable_legacy_history_writes,
                "legacy_history_ready": legacy_history_ready,
                "history_table_exists": history_table_exists,
                "users_table_exists": users_table_exists,
                "reason": None if available else "mysql_activity_schema_not_ready",
            }
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            return {
                "available": False,
                "audit_log_ready": False,
                "table": self.table_name,
                "reason": "mysql_activity_check_failed",
                "error_type": type(exc).__name__,
            }

    def health_check(self) -> Dict[str, Any]:
        readiness = self.readiness_check()
        if not readiness.get("available"):
            return readiness

        try:
            with self._connect() as conn:
                history_row = None
                chatbot_log_row = None
                safety_monitoring_row = None
                users_row = None
                with conn.cursor() as cursor:
                    if readiness.get("history_table_exists"):
                        cursor.execute(f"SELECT COUNT(*) AS count FROM {self.quoted_table_name}")
                        history_row = cursor.fetchone()
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {self.quoted_chat_log_table_name}")
                    chatbot_log_row = cursor.fetchone()
                    cursor.execute(
                        f"""
                        SELECT
                            SUM(CASE WHEN fallback_reason IS NOT NULL AND fallback_reason <> '' THEN 1 ELSE 0 END) AS fallback_count,
                            SUM(CASE WHEN critical_safety_violation = TRUE THEN 1 ELSE 0 END) AS critical_safety_violation_count
                        FROM {self.quoted_chat_log_table_name}
                        """
                    )
                    safety_monitoring_row = cursor.fetchone()
                    if readiness.get("users_table_exists"):
                        cursor.execute("SELECT COUNT(*) AS count FROM `users`")
                        users_row = cursor.fetchone()
            return {
                **readiness,
                "row_count": history_row["count"] if history_row else None,
                "chat_log_row_count": chatbot_log_row["count"] if chatbot_log_row else None,
                "safety_monitoring": {
                    "fallback_count": safety_monitoring_row["fallback_count"] if safety_monitoring_row else None,
                    "critical_safety_violation_count": safety_monitoring_row["critical_safety_violation_count"] if safety_monitoring_row else None,
                },
                "users_row_count": users_row["count"] if users_row else None,
                "role_split": {
                    "chatbot_logs": "AI chatbot conversation logs with message, intent, answer_source, pipeline_stage, risk_level, confidence, and confidence_reasons.",
                    "histories": "Backend scan/eat activity history linked to users.user_id and barcode.",
                },
            }
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            return {
                **readiness,
                "available": False,
                "reason": "mysql_activity_diagnostics_failed",
                "error_type": type(exc).__name__,
            }

    def _find_existing_column(self, conn, candidates: list[str]) -> Optional[str]:
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW COLUMNS FROM {self.quoted_table_name}")
            rows = cursor.fetchall()

        existing_columns = {row["Field"] for row in rows}
        for candidate in candidates:
            if candidate in existing_columns:
                return candidate
        return None

    def _ensure_chatbot_log_table(self) -> None:
        status = self.readiness_check()
        if status.get("available") is True:
            for column in status.get("short_audit_columns") or []:
                minimum, migration = self.CHATBOT_LOG_MIN_CHAR_LENGTH[column]
                logger.warning("MySQL chatbot_logs.%s is narrower than %d characters; longer values are "
                            "shortened to fit (optional widening: %s).", column, minimum, migration)
            try:
                with self._connect() as conn:
                    self._chatbot_log_columns = self._table_columns(conn, self.quoted_chat_log_table_name)
            except Exception:  # an unknown column list writes every column, as before
                self._chatbot_log_columns = set()
            return
        reason = status.get("reason") or "mysql_activity_schema_not_ready"
        if reason == "mysql_activity_check_failed":
            raise OSError(
                "MySQL chatbot audit-schema validation could not reach the database."
            )
        missing_count = status.get("missing_audit_column_count")
        detail = (
            f" reason={reason}, missing_audit_column_count={missing_count}"
            if missing_count is not None
            else f" reason={reason}"
        )
        raise RuntimeError(
            "MySQL chatbot audit schema is not ready; apply "
            "the base chatbot_logs table (backend schema) before startup; later audit columns "
            "(AI/migrations/20260909_chatbot_log_product_trace.sql) are optional."
            f"{detail}"
        )

    def _chatbot_log_table_exists(self, conn) -> bool:
        return self._table_exists(conn, self.chat_log_table_name)

    def ensure_chatbot_log_table(self) -> None:
        if self._chatbot_log_schema_ready:
            return
        with self._chatbot_log_schema_lock:
            if self._chatbot_log_schema_ready:
                return
            self._ensure_chatbot_log_table()
            self._chatbot_log_schema_ready = True

    def _save_chatbot_log(self, activity: ActivityRecord) -> None:
        self.ensure_chatbot_log_table()
        chatbot_action_type = "chat" if activity.message or activity.intent else activity.action_type
        values = {
            "user_id": activity.user_id,
            "barcode": activity.barcode,
            "report_number": activity.report_number,
            "product_id": activity.product_id,
            "action_type": chatbot_action_type,
            "message": activity.message,
            "intent": activity.intent,
            "answer_source": activity.answer_source,
            "pipeline_stage": activity.pipeline_stage,
            "risk_level": activity.risk_level,
            "confidence": activity.confidence,
            "confidence_reasons": json.dumps(activity.confidence_reasons, ensure_ascii=False),
            "fallback_reason": activity.fallback_reason,
            "validation_failure_types": json.dumps(activity.validation_failure_types, ensure_ascii=False),
            "critical_safety_violation": activity.critical_safety_violation,
            "blocked_llm_output_stored": activity.blocked_llm_output_stored,
            "response_id": activity.response_id,
            "external_ai_used": activity.external_ai_used,
            "external_ai_services": json.dumps(activity.external_ai_services, ensure_ascii=False),
            "policy_version": self._fit_column("policy_version", activity.policy_version),
            "applied_rule_ids": json.dumps(activity.applied_rule_ids, ensure_ascii=False),
            "source_ids": json.dumps(activity.source_ids, ensure_ascii=False),
            "response_product_label_trace": _response_product_label_trace_json(activity),
        }
        existing = getattr(self, "_chatbot_log_columns", set())
        # Only the columns this DB has; an unknown column list means 'write them all'.
        columns = [column for column in values if not existing or column in existing]
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self.quoted_chat_log_table_name} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})",
                    tuple(values[column] for column in columns),
                )
            conn.commit()

    def _save_legacy_history(self, activity: ActivityRecord) -> None:
        if activity.user_id is None or not activity.barcode:
            return

        with self._connect() as conn:
            user_column = self._find_existing_column(conn, ["user_id", "userId"])
            barcode_column = self._find_existing_column(conn, ["barcode"])
            action_column = self._find_existing_column(conn, ["action_type", "actionType"])
            created_column = self._find_existing_column(conn, ["created_at", "createdAt"])
            if user_column is None or barcode_column is None or action_column is None:
                return

            columns = [
                _quote_mysql_identifier(user_column),
                _quote_mysql_identifier(barcode_column),
                _quote_mysql_identifier(action_column),
            ]
            values = ["%s", "%s", "%s"]
            params = [activity.user_id, activity.barcode, activity.action_type]
            if created_column:
                columns.append(_quote_mysql_identifier(created_column))
                values.append("NOW()")

            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self.quoted_table_name} ({', '.join(columns)}) VALUES ({', '.join(values)})",
                    params,
                )
            conn.commit()

    def save(self, activity: ActivityRecord) -> None:
        try:
            self._save_chatbot_log(activity)
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL chatbot log save failed: %s%s", type(exc).__name__, _database_error_detail(exc))

        if self.enable_legacy_history_writes:
            try:
                self._save_legacy_history(activity)
            except self._pymysql.err.IntegrityError:
                return
            except Exception as exc:
                if not self._is_database_error(exc):
                    raise
                logger.warning("MySQL legacy history save failed: %s", type(exc).__name__)
                return

    def _list_chatbot_logs_by_user_id(self, user_id: int, limit: int) -> List[ActivityRecord]:
        with self._connect() as conn:
            if not self._chatbot_log_table_exists(conn):
                return []
            # Older DBs lack the later audit columns; read what exists.
            selected = self._existing_log_columns(conn, (
                "user_id", "barcode", "report_number", "product_id", "action_type",
                "message", "intent", "answer_source", "pipeline_stage", "risk_level",
                "confidence", "confidence_reasons", "fallback_reason",
                "validation_failure_types", "critical_safety_violation",
                "blocked_llm_output_stored", "response_id", "external_ai_used",
                "external_ai_services", "policy_version", "applied_rule_ids", "source_ids",
                "response_product_label_trace", "created_at"))
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT {", ".join(selected)}
                    FROM {self.quoted_chat_log_table_name}
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        records = []
        for row in rows:
            payload = dict(row)
            if payload.get("created_at") is not None:
                payload["created_at"] = str(payload["created_at"])
            raw_confidence_reasons = payload.get("confidence_reasons")
            if isinstance(raw_confidence_reasons, str) and raw_confidence_reasons:
                try:
                    payload["confidence_reasons"] = json.loads(raw_confidence_reasons)
                except json.JSONDecodeError:
                    payload["confidence_reasons"] = [raw_confidence_reasons]
            raw_validation_failure_types = payload.get("validation_failure_types")
            if isinstance(raw_validation_failure_types, str) and raw_validation_failure_types:
                try:
                    payload["validation_failure_types"] = json.loads(raw_validation_failure_types)
                except json.JSONDecodeError:
                    payload["validation_failure_types"] = [raw_validation_failure_types]
            raw_external_ai_services = payload.get("external_ai_services")
            if isinstance(raw_external_ai_services, str) and raw_external_ai_services:
                try:
                    payload["external_ai_services"] = json.loads(raw_external_ai_services)
                except json.JSONDecodeError:
                    payload["external_ai_services"] = []
            for field in ("applied_rule_ids", "source_ids"):
                raw = payload.get(field)
                if isinstance(raw, str) and raw:
                    try:
                        payload[field] = json.loads(raw)
                    except json.JSONDecodeError:
                        payload[field] = []
            payload["response_product_label_trace"] = (
                _decode_response_product_label_trace(
                    payload.get("response_product_label_trace")
                )
            )
            _normalize_nullable_activity_list_fields(payload)
            records.append(ActivityRecord(**payload))
        return records

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        try:
            detailed_rows = self._list_chatbot_logs_by_user_id(user_id, limit)
            if detailed_rows:
                return detailed_rows
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL chatbot log list failed: %s", exc)

        try:
            with self._connect() as conn:
                user_column = self._find_existing_column(conn, ["user_id", "userId"])
                barcode_column = self._find_existing_column(conn, ["barcode"])
                action_column = self._find_existing_column(conn, ["action_type", "actionType"])
                created_column = self._find_existing_column(conn, ["created_at", "createdAt"])
                id_column = self._find_existing_column(conn, ["id"])
                if user_column is None or barcode_column is None or action_column is None:
                    return []

                created_select = (
                    f"{_quote_mysql_identifier(created_column)} AS created_at"
                    if created_column
                    else "NULL AS created_at"
                )
                order_columns = []
                if created_column:
                    order_columns.append(f"{_quote_mysql_identifier(created_column)} DESC")
                if id_column:
                    order_columns.append(f"{_quote_mysql_identifier(id_column)} DESC")
                order_sql = f"ORDER BY {', '.join(order_columns)}" if order_columns else ""

                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT
                            {_quote_mysql_identifier(user_column)} AS user_id,
                            {_quote_mysql_identifier(barcode_column)} AS barcode,
                            {_quote_mysql_identifier(action_column)} AS action_type,
                            {created_select}
                        FROM {self.quoted_table_name}
                        WHERE {_quote_mysql_identifier(user_column)} = %s
                        {order_sql}
                        LIMIT %s
                        """,
                        (user_id, limit),
                    )
                    rows = cursor.fetchall()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL activity list failed: %s", exc)
            return []

        return [ActivityRecord(**row) for row in rows]

    def top_unknown_intent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            with self._connect() as conn:
                if not self._chatbot_log_table_exists(conn):
                    return []
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT
                            LOWER(TRIM(message)) AS message,
                            MIN(message) AS example,
                            COUNT(*) AS count,
                            MAX(created_at) AS last_seen_at
                        FROM {self.quoted_chat_log_table_name}
                        WHERE intent = %s
                          AND message IS NOT NULL
                          AND TRIM(message) <> ''
                        GROUP BY LOWER(TRIM(message))
                        ORDER BY count DESC, last_seen_at DESC
                        LIMIT %s
                        """,
                        ("unknown", limit),
                    )
                    rows = cursor.fetchall()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL unknown intent summary failed: %s", exc)
            return []

        return [
            {
                "rank": index + 1,
                "message": row.get("message"),
                "count": row.get("count"),
                "example": row.get("example"),
                "last_seen_at": str(row.get("last_seen_at")) if row.get("last_seen_at") is not None else None,
            }
            for index, row in enumerate(rows)
        ]

    def _fallback_rows(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if not self._chatbot_log_table_exists(conn):
                return []
            selected = self._existing_log_columns(conn, (
                "user_id", "barcode", "report_number", "action_type",
                "message", "intent", "answer_source", "pipeline_stage", "risk_level",
                "confidence", "fallback_reason", "validation_failure_types",
                "critical_safety_violation", "blocked_llm_output_stored",
                "response_id", "external_ai_used", "external_ai_services",
                "response_product_label_trace", "created_at"))
            if not {"fallback_reason", "validation_failure_types", "critical_safety_violation"} <= set(selected):
                return []  # the monitoring columns were never added to this DB
            with conn.cursor() as cursor:
                limit_sql = "LIMIT %s" if limit is not None else ""
                params: tuple[Any, ...] = (limit,) if limit is not None else ()
                cursor.execute(
                    f"""
                    SELECT {", ".join(selected)}
                    FROM {self.quoted_chat_log_table_name}
                    WHERE
                        (fallback_reason IS NOT NULL AND fallback_reason <> '')
                        OR (validation_failure_types IS NOT NULL AND validation_failure_types <> '' AND validation_failure_types <> '[]')
                        OR critical_safety_violation = TRUE
                    ORDER BY created_at DESC, id DESC
                    {limit_sql}
                    """,
                    params,
                )
                return list(cursor.fetchall())

    def _fallback_activity_records(self, limit: Optional[int] = None) -> List[ActivityRecord]:
        rows = self._fallback_rows(limit=limit)
        records = []
        for row in rows:
            payload = dict(row)
            if payload.get("created_at") is not None:
                payload["created_at"] = str(payload["created_at"])
            raw_validation_failure_types = payload.get("validation_failure_types")
            if isinstance(raw_validation_failure_types, str) and raw_validation_failure_types:
                try:
                    payload["validation_failure_types"] = json.loads(raw_validation_failure_types)
                except json.JSONDecodeError:
                    payload["validation_failure_types"] = [raw_validation_failure_types]
            raw_external_ai_services = payload.get("external_ai_services")
            if isinstance(raw_external_ai_services, str) and raw_external_ai_services:
                try:
                    payload["external_ai_services"] = json.loads(raw_external_ai_services)
                except json.JSONDecodeError:
                    payload["external_ai_services"] = []
            payload["response_product_label_trace"] = (
                _decode_response_product_label_trace(
                    payload.get("response_product_label_trace")
                )
            )
            _normalize_nullable_activity_list_fields(payload)
            records.append(ActivityRecord(**payload))
        return records

    def fallback_summary(self, limit: int = 20) -> Dict[str, Any]:
        try:
            records = self._fallback_activity_records()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL fallback summary failed: %s", exc)
            return _fallback_summary_from_records([], limit=limit) | {"error": str(exc)}
        return _fallback_summary_from_records(records, limit=limit)

    def recent_fallback_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            records = self._fallback_activity_records(limit=limit)
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            logger.warning("MySQL recent fallback logs failed: %s", exc)
            return []
        return [_fallback_log_payload(record) for record in records]


def _database_error_detail(exc) -> str:
    """Error code and column only: MySQL messages can quote the rejected value."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    column = re.search(r"column '([A-Za-z0-9_]+)'", str(exc.args[1])) if len(exc.args) > 1 else None
    return (f" code={code}" if code else "") + (f" column={column[1]}" if column else "")
