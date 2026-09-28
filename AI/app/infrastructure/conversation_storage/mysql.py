"""mysql conversation repository implementation."""

from __future__ import annotations

from app.infrastructure.mysql_support import (
    _mysql_connection_pool_settings,
    _mysql_connection_timeout_kwargs,
    _mysql_connection_tls_kwargs,
    _quote_mysql_identifier,
    _shared_mysql_connection_pool,
)
from datetime import datetime
from threading import RLock
from typing import Any
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from uuid import UUID
from uuid import uuid4

from .common import (
    Conversation,
    ConversationCapacityError,
    ConversationConflictError,
    ConversationIntegrityError,
    ConversationMessage,
    ConversationMessagePage,
    ConversationNotFoundError,
    ConversationPage,
    ConversationRepository,
    ConversationRepositoryError,
    DEFAULT_CONVERSATION_LIST_LIMIT,
    DEFAULT_CONVERSATION_RETENTION_DAYS,
    DEFAULT_CONVERSATION_TITLE,
    DEFAULT_MAX_CONVERSATIONS_PER_USER,
    DEFAULT_MESSAGE_LIST_LIMIT,
    MAX_CONVERSATIONS_PER_USER,
    MAX_CONVERSATION_LIST_LIMIT,
    MAX_CONVERSATION_STATE_BYTES,
    MAX_MESSAGES_PER_CONVERSATION,
    MAX_MESSAGE_LIST_LIMIT,
    MAX_MESSAGE_METADATA_BYTES,
    NewConversationMessage,
    _as_utc,
    _bounded_limit,
    _canonical_uuid,
    _conversation_from_row,
    _decode_cursor,
    _decode_json_mapping,
    _encode_cursor,
    _message_from_row,
    _mysql_timestamp,
    _next_revision,
    _normalize_json_mapping,
    _parse_timestamp,
    _sqlite_timestamp,
    _utc_now,
    _validate_messages,
    _validate_retention_days,
    _validate_revision,
    _validate_title,
    _validate_user_id,
)


class MySQLConversationRepository(ConversationRepository):
    """Production conversation storage using the shared bounded MySQL pool."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        database: str = "jjikmuk",
        user: str = "root",
        password: str = "",
        *,
        charset: str = "utf8mb4",
        retention_days: Optional[int] = DEFAULT_CONVERSATION_RETENTION_DAYS,
        conversation_table_name: str = "chatbot_conversations",
        message_table_name: str = "chatbot_messages",
        clock: Callable[[], datetime] = _utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError(
                "MySQL conversation storage requires PyMySQL from requirements.txt."
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
        self.retention_days = _validate_retention_days(retention_days)
        self.conversation_table_name = conversation_table_name
        self.message_table_name = message_table_name
        self.quoted_conversation_table = _quote_mysql_identifier(
            conversation_table_name
        )
        self.quoted_message_table = _quote_mysql_identifier(message_table_name)
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._schema_ready = False
        self._schema_lock = RLock()

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _new_uuid(self, field_name: str) -> str:
        return _canonical_uuid(self._uuid_factory(), field_name=field_name)

    def _connect(self):
        if self._connection_pool is None:
            self._connection_pool = _shared_mysql_connection_pool(
                self._pymysql,
                self.connection_kwargs,
                self.connection_pool_settings,
            )
        return self._connection_pool.connect()

    def _is_database_error(self, exc: Exception) -> bool:
        mysql_error = getattr(self._pymysql, "MySQLError", ())
        return isinstance(exc, (mysql_error, OSError, TimeoutError))

    def _is_integrity_error(self, exc: Exception) -> bool:
        integrity_error = getattr(self._pymysql, "IntegrityError", ())
        return bool(integrity_error) and isinstance(exc, integrity_error)

    @staticmethod
    def _index_columns(
        rows: Sequence[Mapping[str, Any]], *, unique_only: bool = False
    ) -> set[Tuple[str, ...]]:
        grouped: Dict[str, list[Tuple[int, str]]] = {}
        for row in rows:
            if unique_only and int(row.get("Non_unique", 1)) != 0:
                continue
            name = str(row.get("Key_name") or "")
            column = str(row.get("Column_name") or "")
            if name and column:
                grouped.setdefault(name, []).append(
                    (int(row.get("Seq_in_index", 0)), column)
                )
        return {
            tuple(column for _position, column in sorted(columns))
            for columns in grouped.values()
        }

    def readiness_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SHOW TABLES LIKE %s", (self.conversation_table_name,)
                    )
                    conversation_exists = cursor.fetchone() is not None
                    cursor.execute("SHOW TABLES LIKE %s", (self.message_table_name,))
                    message_exists = cursor.fetchone() is not None
                    conversation_columns: set[str] = set()
                    message_columns: set[str] = set()
                    conversation_indexes: set[Tuple[str, ...]] = set()
                    message_indexes: set[Tuple[str, ...]] = set()
                    unique_conversation_indexes: set[Tuple[str, ...]] = set()
                    unique_message_indexes: set[Tuple[str, ...]] = set()
                    foreign_keys: Sequence[Mapping[str, Any]] = ()
                    if conversation_exists:
                        cursor.execute(
                            f"SHOW COLUMNS FROM {self.quoted_conversation_table}"
                        )
                        conversation_columns = {
                            str(row["Field"]) for row in cursor.fetchall()
                        }
                        cursor.execute(
                            f"SHOW INDEX FROM {self.quoted_conversation_table}"
                        )
                        rows = cursor.fetchall()
                        conversation_indexes = self._index_columns(rows)
                        unique_conversation_indexes = self._index_columns(
                            rows, unique_only=True
                        )
                    if message_exists:
                        cursor.execute(f"SHOW COLUMNS FROM {self.quoted_message_table}")
                        message_columns = {
                            str(row["Field"]) for row in cursor.fetchall()
                        }
                        cursor.execute(f"SHOW INDEX FROM {self.quoted_message_table}")
                        rows = cursor.fetchall()
                        message_indexes = self._index_columns(rows)
                        unique_message_indexes = self._index_columns(
                            rows, unique_only=True
                        )
                        cursor.execute(
                            """
                            SELECT
                                rc.DELETE_RULE AS delete_rule,
                                kcu.COLUMN_NAME AS column_name,
                                kcu.REFERENCED_TABLE_NAME AS referenced_table_name,
                                kcu.REFERENCED_COLUMN_NAME AS referenced_column_name
                            FROM information_schema.REFERENTIAL_CONSTRAINTS AS rc
                            JOIN information_schema.KEY_COLUMN_USAGE AS kcu
                              ON kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
                             AND kcu.TABLE_NAME = rc.TABLE_NAME
                             AND kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
                            WHERE rc.CONSTRAINT_SCHEMA = DATABASE()
                              AND rc.TABLE_NAME = %s
                            """,
                            (self.message_table_name,),
                        )
                        foreign_keys = cursor.fetchall()
        except Exception as exc:
            if not self._is_database_error(exc):
                raise
            return {
                "available": False,
                "provider": "mysql",
                "reason": "mysql_conversation_check_failed",
                "error_type": type(exc).__name__,
            }

        missing_conversations = self.CONVERSATION_COLUMNS - conversation_columns
        missing_messages = self.MESSAGE_COLUMNS - message_columns
        cascade = any(
            str(row.get("delete_rule") or "").upper() == "CASCADE"
            and str(row.get("column_name") or "") == "conversation_id"
            and str(row.get("referenced_table_name") or "")
            == self.conversation_table_name
            and str(row.get("referenced_column_name") or "") == "conversation_id"
            for row in foreign_keys
        )
        required_indexes = (
            ("conversation_id",) in unique_conversation_indexes
            and ("user_id", "updated_at", "conversation_id")
            in conversation_indexes
            and ("message_id",) in unique_message_indexes
            and ("conversation_id", "message_sequence")
            in unique_message_indexes
            and ("conversation_id", "turn_id", "role")
            in unique_message_indexes
            and ("conversation_id", "created_at", "message_id") in message_indexes
        )
        available = (
            conversation_exists
            and message_exists
            and not missing_conversations
            and not missing_messages
            and cascade
            and required_indexes
        )
        return {
            "available": available,
            "provider": "mysql",
            "conversation_table": self.conversation_table_name,
            "message_table": self.message_table_name,
            "missing_conversation_column_count": len(missing_conversations),
            "missing_message_column_count": len(missing_messages),
            "message_delete_cascade": cascade,
            "required_indexes_ready": required_indexes,
            "runtime_ddl_enabled": False,
            "migration": "AI/migrations/20260828_conversation_history.sql",
            "retention_days": self.retention_days,
            "reason": None if available else "mysql_conversation_schema_not_ready",
        }

    def ensure_schema_ready(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            status = self.readiness_check()
            if status.get("available") is not True:
                if status.get("reason") == "mysql_conversation_check_failed":
                    raise OSError(
                        "MySQL conversation schema validation could not reach the database."
                    )
                raise RuntimeError(
                    "MySQL conversation schema is not ready; apply "
                    "AI/migrations/20260828_conversation_history.sql."
                )
            self._schema_ready = True

    def _owned_row(
        self,
        conn: Any,
        user_id: int,
        conversation_id: str,
        now: datetime,
        *,
        lock: bool = False,
    ) -> Mapping[str, Any]:
        suffix = " FOR UPDATE" if lock else ""
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM {self.quoted_conversation_table}
                WHERE conversation_id = %s AND user_id = %s
                LIMIT 1{suffix}
                """,
                (conversation_id, user_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ConversationNotFoundError("Conversation not found.")
        return row

    def create(
        self,
        user_id: int,
        title: Optional[str] = None,
        *,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS_PER_USER,
        title_manually_set: bool = False,
        conversation_id: Optional[str] = None,
    ) -> Conversation:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        if not isinstance(title_manually_set, bool):
            raise ValueError("title_manually_set must be a boolean")
        if title_manually_set and title is None:
            raise ValueError("title_manually_set requires an explicit title")
        if isinstance(max_conversations, bool) or not isinstance(max_conversations, int) or not 1 <= max_conversations <= MAX_CONVERSATIONS_PER_USER:
            raise ValueError(
                f"max_conversations must be between 1 and {MAX_CONVERSATIONS_PER_USER}"
            )
        normalized_title = _validate_title(title)
        supplied = conversation_id is not None
        conversation_id = (
            _canonical_uuid(conversation_id, field_name="conversation_id")
            if supplied
            else self._new_uuid("conversation_id")
        )
        now = self._now()
        expires = None
        try:
            with self._connect() as conn:
                try:
                    conn.begin()
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f"""
                            SELECT * FROM {self.quoted_conversation_table}
                            WHERE conversation_id = %s LIMIT 1 FOR UPDATE
                            """,
                            (conversation_id,),
                        )
                        existing = cursor.fetchone()
                        if existing is not None:
                            if int(existing["user_id"]) != user_id:
                                raise ConversationConflictError(
                                    "Conversation UUID already exists."
                                )
                            if supplied:
                                conn.commit()
                                return _conversation_from_row(existing)
                            raise ConversationConflictError(
                                "Conversation UUID already exists."
                            )
                        cursor.execute(
                            f"""
                            SELECT COUNT(*) AS count
                            FROM {self.quoted_conversation_table}
                            WHERE user_id = %s
                            FOR UPDATE
                            """,
                            (user_id,),
                        )
                        count = cursor.fetchone()
                        if int(count["count"]) >= max_conversations:
                            raise ConversationCapacityError(
                                "Active conversation limit reached; delete a room before creating another."
                            )
                        cursor.execute(
                            f"""
                            INSERT INTO {self.quoted_conversation_table}
                            (
                                conversation_id, user_id, title,
                                title_manually_set, state_json, revision,
                                created_at, updated_at, expires_at
                            )
                            VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s)
                            """,
                            (
                                conversation_id,
                                user_id,
                                normalized_title,
                                int(title_manually_set),
                                "{}",
                                _mysql_timestamp(now),
                                _mysql_timestamp(now),
                                (
                                    _mysql_timestamp(expires)
                                    if expires is not None
                                    else None
                                ),
                            ),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        except ConversationRepositoryError:
            raise
        except Exception as exc:
            if self._is_integrity_error(exc):
                raise ConversationConflictError(
                    "Conversation UUID already exists."
                ) from exc
            raise
        return Conversation(
            conversation_id,
            user_id,
            normalized_title,
            title_manually_set,
            {},
            0,
            now,
            now,
            expires,
        )

    def list(
        self,
        user_id: int,
        *,
        limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
        before: Optional[str] = None,
    ) -> ConversationPage:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        limit = _bounded_limit(limit, maximum=MAX_CONVERSATION_LIST_LIMIT)
        cursor_values: Optional[Tuple[datetime, str]] = None
        if before is not None:
            payload = _decode_cursor(
                before, expected_keys={"updated_at", "conversation_id"}
            )
            cursor_values = (
                _parse_timestamp(payload["updated_at"]),
                _canonical_uuid(payload["conversation_id"], field_name="conversation_id"),
            )
        clause = ""
        params: list[Any] = [user_id]
        if cursor_values is not None:
            value = _mysql_timestamp(cursor_values[0])
            clause = (
                "AND (updated_at < %s OR "
                "(updated_at = %s AND conversation_id < %s))"
            )
            params.extend((value, value, cursor_values[1]))
        params.append(limit + 1)
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {self.quoted_conversation_table}
                    WHERE user_id = %s
                      {clause}
                    ORDER BY updated_at DESC, conversation_id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        selected = rows[:limit]
        values = tuple(_conversation_from_row(row) for row in selected)
        next_cursor = None
        if len(rows) > limit:
            last = values[-1]
            next_cursor = _encode_cursor(
                {
                    "updated_at": _sqlite_timestamp(last.updated_at),
                    "conversation_id": last.conversation_id,
                }
            )
        return ConversationPage(values, next_cursor)

    def get(self, user_id: int, conversation_id: str) -> Optional[Conversation]:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        try:
            with self._connect() as conn:
                row = self._owned_row(
                    conn, user_id, conversation_id, self._now()
                )
        except ConversationNotFoundError:
            return None
        return _conversation_from_row(row)

    def list_messages(
        self,
        user_id: int,
        conversation_id: str,
        *,
        limit: int = DEFAULT_MESSAGE_LIST_LIMIT,
        before: Optional[str] = None,
    ) -> ConversationMessagePage:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        limit = _bounded_limit(limit, maximum=MAX_MESSAGE_LIST_LIMIT)
        before_sequence: Optional[int] = None
        if before is not None:
            payload = _decode_cursor(before, expected_keys={"sequence"})
            before_sequence = payload["sequence"]
            if isinstance(before_sequence, bool) or not isinstance(before_sequence, int) or before_sequence < 1:
                raise ValueError("cursor is invalid")
        clause = ""
        params: list[Any] = [conversation_id]
        if before_sequence is not None:
            clause = "AND message_sequence < %s"
            params.append(before_sequence)
        params.append(limit + 1)
        with self._connect() as conn:
            self._owned_row(conn, user_id, conversation_id, self._now())
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT * FROM {self.quoted_message_table}
                    WHERE conversation_id = %s {clause}
                    ORDER BY message_sequence DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        selected = rows[:limit]
        messages = [_message_from_row(row) for row in selected]
        next_cursor = (
            _encode_cursor({"sequence": messages[-1].sequence})
            if len(rows) > limit
            else None
        )
        messages.reverse()
        return ConversationMessagePage(tuple(messages), next_cursor)

    def _exchange_rows(
        self,
        conn: Any,
        conversation_id: str,
        turn_id: str,
        *,
        lock: bool = False,
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        suffix = " FOR UPDATE" if lock else ""
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM {self.quoted_message_table}
                WHERE conversation_id = %s AND turn_id = %s
                ORDER BY message_sequence{suffix}
                """,
                (conversation_id, turn_id),
            )
            rows = cursor.fetchall()
        if not rows:
            return None
        messages = [_message_from_row(row) for row in rows]
        by_role = {item.role: item for item in messages}
        if len(messages) != 2 or set(by_role) != {"user", "assistant"}:
            raise ConversationIntegrityError("Persisted exchange is incomplete.")
        return by_role["user"], by_role["assistant"]

    def _insert_messages(
        self,
        conn: Any,
        conversation_id: str,
        messages: Tuple[NewConversationMessage, ...],
        now: datetime,
    ) -> Tuple[ConversationMessage, ...]:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count,
                       COALESCE(MAX(message_sequence), 0) AS last_sequence
                FROM {self.quoted_message_table}
                WHERE conversation_id = %s
                FOR UPDATE
                """,
                (conversation_id,),
            )
            current = cursor.fetchone()
            count = int(current["count"])
            if count + len(messages) > MAX_MESSAGES_PER_CONVERSATION:
                raise ConversationCapacityError(
                    f"Conversation message limit of {MAX_MESSAGES_PER_CONVERSATION} reached."
                )
            first_sequence = int(current["last_sequence"]) + 1
            created: list[ConversationMessage] = []
            for index, item in enumerate(messages):
                metadata, metadata_json = _normalize_json_mapping(
                    item.metadata,
                    max_bytes=MAX_MESSAGE_METADATA_BYTES,
                    field_name="message metadata",
                )
                value = ConversationMessage(
                    self._new_uuid("message_id"),
                    conversation_id,
                    first_sequence + index,
                    item.role,
                    item.content,
                    metadata,
                    item.turn_id,
                    now,
                )
                cursor.execute(
                    f"""
                    INSERT INTO {self.quoted_message_table}
                    (
                        message_id, conversation_id, message_sequence, turn_id,
                        role, content, metadata_json, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        value.message_id,
                        conversation_id,
                        value.sequence,
                        value.turn_id,
                        value.role,
                        value.content,
                        metadata_json,
                        _mysql_timestamp(now),
                    ),
                )
                created.append(value)
        return tuple(created)

    def _update_after_append(
        self,
        conn: Any,
        row: Mapping[str, Any],
        *,
        now: datetime,
        new_state: Optional[Mapping[str, Any]],
        auto_title: Optional[str],
    ) -> None:
        state = (
            new_state
            if new_state is not None
            else _decode_json_mapping(
                row["state_json"], max_bytes=MAX_CONVERSATION_STATE_BYTES
            )
        )
        _, state_json = _normalize_json_mapping(
            state,
            max_bytes=MAX_CONVERSATION_STATE_BYTES,
            field_name="conversation state",
        )
        title = str(row["title"])
        if (
            auto_title is not None
            and not bool(row["title_manually_set"])
            and title == DEFAULT_CONVERSATION_TITLE
        ):
            title = auto_title
        expires = None
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self.quoted_conversation_table}
                SET title = %s, state_json = %s, revision = %s,
                    updated_at = %s, expires_at = %s
                WHERE conversation_id = %s
                """,
                (
                    title,
                    state_json,
                    _next_revision(int(row["revision"])),
                    _mysql_timestamp(now),
                    _mysql_timestamp(expires) if expires is not None else None,
                    row["conversation_id"],
                ),
            )

    def _append_validated(
        self,
        user_id: int,
        conversation_id: str,
        messages: Tuple[NewConversationMessage, ...],
        *,
        new_state: Optional[Mapping[str, Any]],
        auto_title: Optional[str],
        turn_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Tuple[ConversationMessage, ...]:
        self.ensure_schema_ready()
        now = self._now()
        try:
            with self._connect() as conn:
                try:
                    conn.begin()
                    row = self._owned_row(
                        conn, user_id, conversation_id, now, lock=True
                    )
                    if turn_id is not None:
                        existing = self._exchange_rows(
                            conn, conversation_id, turn_id, lock=True
                        )
                        if existing is not None:
                            conn.commit()
                            return existing
                    if expected_revision is not None and int(row["revision"]) != expected_revision:
                        raise ConversationConflictError(
                            "Conversation was updated by another request."
                        )
                    if expected_revision is None and expected_updated_at is not None and _parse_timestamp(row["updated_at"]) != expected_updated_at:
                        raise ConversationConflictError(
                            "Conversation was updated by another request."
                        )
                    created = self._insert_messages(
                        conn, conversation_id, messages, now
                    )
                    self._update_after_append(
                        conn,
                        row,
                        now=now,
                        new_state=new_state,
                        auto_title=auto_title,
                    )
                    conn.commit()
                    return created
                except Exception:
                    conn.rollback()
                    raise
        except ConversationRepositoryError:
            raise
        except Exception as exc:
            if self._is_integrity_error(exc):
                raise ConversationConflictError(
                    "Message idempotency key already exists."
                ) from exc
            raise

    def append_many(
        self,
        user_id: int,
        conversation_id: str,
        messages: Sequence[NewConversationMessage],
        *,
        new_state: Optional[Mapping[str, Any]] = None,
        auto_title: Optional[str] = None,
    ) -> Tuple[ConversationMessage, ...]:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        messages = _validate_messages(messages)
        state = (
            _normalize_json_mapping(
                new_state,
                max_bytes=MAX_CONVERSATION_STATE_BYTES,
                field_name="conversation state",
            )[0]
            if new_state is not None
            else None
        )
        title = _validate_title(auto_title) if auto_title is not None else None
        return self._append_validated(
            user_id,
            conversation_id,
            messages,
            new_state=state,
            auto_title=title,
        )

    def append_exchange(
        self,
        user_id: int,
        conversation_id: str,
        *,
        turn_id: str,
        user_content: str,
        assistant_content: str,
        assistant_metadata: Optional[Mapping[str, Any]] = None,
        new_state: Optional[Mapping[str, Any]] = None,
        auto_title: Optional[str] = None,
        expected_revision: Optional[int] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Tuple[ConversationMessage, ConversationMessage]:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        turn_id = _canonical_uuid(turn_id, field_name="turn_id")
        messages = _validate_messages(
            (
                NewConversationMessage("user", user_content, {}, turn_id),
                NewConversationMessage(
                    "assistant",
                    assistant_content,
                    {} if assistant_metadata is None else assistant_metadata,
                    turn_id,
                ),
            )
        )
        state = (
            _normalize_json_mapping(
                new_state,
                max_bytes=MAX_CONVERSATION_STATE_BYTES,
                field_name="conversation state",
            )[0]
            if new_state is not None
            else None
        )
        title = _validate_title(auto_title) if auto_title is not None else None
        revision = (
            _validate_revision(expected_revision)
            if expected_revision is not None
            else None
        )
        expected_time = (
            _as_utc(expected_updated_at)
            if revision is None and expected_updated_at is not None
            else None
        )
        created = self._append_validated(
            user_id,
            conversation_id,
            messages,
            new_state=state,
            auto_title=title,
            turn_id=turn_id,
            expected_revision=revision,
            expected_updated_at=expected_time,
        )
        return created[0], created[1]

    def get_exchange_by_turn_id(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        turn_id = _canonical_uuid(turn_id, field_name="turn_id")
        with self._connect() as conn:
            self._owned_row(conn, user_id, conversation_id, self._now())
            return self._exchange_rows(conn, conversation_id, turn_id)

    def rename(self, user_id: int, conversation_id: str, title: str) -> Conversation:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        title = _validate_title(title)
        now = self._now()
        with self._connect() as conn:
            try:
                conn.begin()
                row = self._owned_row(
                    conn, user_id, conversation_id, now, lock=True
                )
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        UPDATE {self.quoted_conversation_table}
                        SET title = %s, title_manually_set = 1,
                            revision = %s, updated_at = %s
                        WHERE conversation_id = %s
                        """,
                        (
                            title,
                            _next_revision(int(row["revision"])),
                            _mysql_timestamp(now),
                            conversation_id,
                        ),
                    )
                    cursor.execute(
                        f"""
                        SELECT * FROM {self.quoted_conversation_table}
                        WHERE conversation_id = %s
                        """,
                        (conversation_id,),
                    )
                    restored = cursor.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _conversation_from_row(restored)

    def hard_delete(self, user_id: int, conversation_id: str) -> bool:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        with self._connect() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        DELETE FROM {self.quoted_conversation_table}
                        WHERE conversation_id = %s AND user_id = %s
                        """,
                        (conversation_id, user_id),
                    )
                    deleted = cursor.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return deleted > 0

    def hard_delete_all_by_user(
        self, user_id: int, *, limit: Optional[int] = None
    ) -> int:
        self.ensure_schema_ready()
        user_id = _validate_user_id(user_id)
        if limit is not None:
            limit = _bounded_limit(
                limit, maximum=MAX_CONVERSATIONS_PER_USER, field_name="limit"
            )
        with self._connect() as conn:
            try:
                conn.begin()
                with conn.cursor() as cursor:
                    if limit is None:
                        cursor.execute(
                            f"""
                            DELETE FROM {self.quoted_conversation_table}
                            WHERE user_id = %s
                            """,
                            (user_id,),
                        )
                    else:
                        cursor.execute(
                            f"""
                            DELETE FROM {self.quoted_conversation_table}
                            WHERE conversation_id IN (
                                SELECT conversation_id FROM (
                                    SELECT conversation_id
                                    FROM {self.quoted_conversation_table}
                                    WHERE user_id = %s
                                    ORDER BY created_at, conversation_id
                                    LIMIT %s
                                ) AS selected
                            )
                            """,
                            (user_id, limit),
                        )
                    deleted = cursor.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return deleted
