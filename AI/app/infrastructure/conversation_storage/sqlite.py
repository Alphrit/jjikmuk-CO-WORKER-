"""sqlite conversation repository implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Callable
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from uuid import UUID
from uuid import uuid4
import re
import sqlite3

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


def _sqlite_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value):
        raise ValueError("SQLite table name is invalid")
    return f'"{value}"'



class SQLiteConversationRepository(ConversationRepository):
    """Local persistent implementation; production schema remains migration-owned."""

    def __init__(
        self,
        db_path: str,
        *,
        retention_days: Optional[int] = DEFAULT_CONVERSATION_RETENTION_DAYS,
        conversation_table_name: str = "chatbot_conversations",
        message_table_name: str = "chatbot_messages",
        clock: Callable[[], datetime] = _utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ):
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("db_path must not be blank")
        self.db_path = db_path
        self.retention_days = _validate_retention_days(retention_days)
        self.conversation_table_name = conversation_table_name
        self.message_table_name = message_table_name
        self.quoted_conversation_table = _sqlite_identifier(conversation_table_name)
        self.quoted_message_table = _sqlite_identifier(message_table_name)
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._ensure_schema()
        if self.readiness_check().get("available") is not True:
            raise RuntimeError(
                "SQLite conversation schema is not ready; migrate the local database."
            )

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _new_uuid(self, field_name: str) -> str:
        return _canonical_uuid(self._uuid_factory(), field_name=field_name)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_schema(self) -> None:
        conversation_index = _sqlite_identifier(
            f"idx_{self.conversation_table_name}_user_updated"
        )
        message_index = _sqlite_identifier(
            f"idx_{self.message_table_name}_conversation_created"
        )
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.quoted_conversation_table} (
                    conversation_id TEXT NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    title_manually_set INTEGER NOT NULL DEFAULT 0,
                    state_json TEXT NOT NULL DEFAULT '{{}}',
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NULL,
                    CHECK (title_manually_set IN (0, 1))
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute(
                    f"PRAGMA table_info({self.quoted_conversation_table})"
                ).fetchall()
            }
            if "revision" not in columns:
                conn.execute(
                    f"""
                    ALTER TABLE {self.quoted_conversation_table}
                    ADD COLUMN revision INTEGER NOT NULL DEFAULT 0
                    """
                )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.quoted_message_table} (
                    message_id TEXT NOT NULL PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_sequence INTEGER NOT NULL,
                    turn_id TEXT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{{}}',
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, message_sequence),
                    UNIQUE (conversation_id, turn_id, role),
                    FOREIGN KEY (conversation_id)
                        REFERENCES {self.quoted_conversation_table}(conversation_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {conversation_index}
                ON {self.quoted_conversation_table}
                    (user_id, updated_at, conversation_id)
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {message_index}
                ON {self.quoted_message_table}
                    (conversation_id, created_at, message_id)
                """
            )

    def readiness_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                conversation_columns = {
                    str(row[1])
                    for row in conn.execute(
                        f"PRAGMA table_info({self.quoted_conversation_table})"
                    ).fetchall()
                }
                message_columns = {
                    str(row[1])
                    for row in conn.execute(
                        f"PRAGMA table_info({self.quoted_message_table})"
                    ).fetchall()
                }
                foreign_keys = conn.execute(
                    f"PRAGMA foreign_key_list({self.quoted_message_table})"
                ).fetchall()
        except sqlite3.Error as exc:
            return {
                "available": False,
                "provider": "sqlite",
                "reason": "sqlite_conversation_check_failed",
                "error_type": type(exc).__name__,
            }
        missing_conversations = self.CONVERSATION_COLUMNS - conversation_columns
        missing_messages = self.MESSAGE_COLUMNS - message_columns
        cascade = any(
            str(row[2]) == self.conversation_table_name
            and str(row[3]) == "conversation_id"
            and str(row[4]) == "conversation_id"
            and str(row[6]).upper() == "CASCADE"
            for row in foreign_keys
        )
        available = not missing_conversations and not missing_messages and cascade
        return {
            "available": available,
            "provider": "sqlite",
            "conversation_table": self.conversation_table_name,
            "message_table": self.message_table_name,
            "missing_conversation_column_count": len(missing_conversations),
            "missing_message_column_count": len(missing_messages),
            "message_delete_cascade": cascade,
            "retention_days": self.retention_days,
            "reason": None if available else "sqlite_conversation_schema_not_ready",
        }

    def _owned_row(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        conversation_id: str,
        now_text: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            f"""
            SELECT * FROM {self.quoted_conversation_table}
            WHERE conversation_id = ? AND user_id = ?
            LIMIT 1
            """,
            (conversation_id, user_id),
        ).fetchone()
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
        now_text = _sqlite_timestamp(now)
        expires = None
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    f"""
                    SELECT * FROM {self.quoted_conversation_table}
                    WHERE conversation_id = ? LIMIT 1
                    """,
                    (conversation_id,),
                ).fetchone()
                if existing is not None:
                    if int(existing["user_id"]) != user_id:
                        raise ConversationConflictError(
                            "Conversation UUID already exists."
                        )
                    if supplied:
                        return _conversation_from_row(existing)
                    raise ConversationConflictError(
                        "Conversation UUID already exists."
                    )
                count = conn.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM {self.quoted_conversation_table}
                    WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
                if int(count["count"]) >= max_conversations:
                    raise ConversationCapacityError(
                        "Active conversation limit reached; delete a room before creating another."
                    )
                conn.execute(
                    f"""
                    INSERT INTO {self.quoted_conversation_table}
                    (
                        conversation_id, user_id, title, title_manually_set,
                        state_json, revision, created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, '{{}}', 0, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        user_id,
                        normalized_title,
                        int(title_manually_set),
                        now_text,
                        now_text,
                        _sqlite_timestamp(expires) if expires is not None else None,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConversationConflictError(
                "Conversation UUID already exists."
            ) from exc
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
        user_id = _validate_user_id(user_id)
        limit = _bounded_limit(limit, maximum=MAX_CONVERSATION_LIST_LIMIT)
        now_text = _sqlite_timestamp(self._now())
        cursor: Optional[Tuple[str, str]] = None
        if before is not None:
            payload = _decode_cursor(
                before, expected_keys={"updated_at", "conversation_id"}
            )
            cursor = (
                _sqlite_timestamp(_parse_timestamp(payload["updated_at"])),
                _canonical_uuid(payload["conversation_id"], field_name="conversation_id"),
            )
        clause = ""
        params: list[Any] = [user_id]
        if cursor is not None:
            clause = (
                "AND (updated_at < ? OR "
                "(updated_at = ? AND conversation_id < ?))"
            )
            params.extend((cursor[0], cursor[0], cursor[1]))
        params.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM {self.quoted_conversation_table}
                WHERE user_id = ?
                  {clause}
                ORDER BY updated_at DESC, conversation_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
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
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        try:
            with self._connect() as conn:
                row = self._owned_row(
                    conn,
                    user_id,
                    conversation_id,
                    _sqlite_timestamp(self._now()),
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
            clause = "AND message_sequence < ?"
            params.append(before_sequence)
        params.append(limit + 1)
        with self._connect() as conn:
            self._owned_row(
                conn,
                user_id,
                conversation_id,
                _sqlite_timestamp(self._now()),
            )
            rows = conn.execute(
                f"""
                SELECT * FROM {self.quoted_message_table}
                WHERE conversation_id = ? {clause}
                ORDER BY message_sequence DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
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
        self, conn: sqlite3.Connection, conversation_id: str, turn_id: str
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        rows = conn.execute(
            f"""
            SELECT * FROM {self.quoted_message_table}
            WHERE conversation_id = ? AND turn_id = ?
            ORDER BY message_sequence
            """,
            (conversation_id, turn_id),
        ).fetchall()
        if not rows:
            return None
        messages = [_message_from_row(row) for row in rows]
        by_role = {item.role: item for item in messages}
        if len(messages) != 2 or set(by_role) != {"user", "assistant"}:
            raise ConversationIntegrityError("Persisted exchange is incomplete.")
        return by_role["user"], by_role["assistant"]

    def _insert_messages(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        messages: Tuple[NewConversationMessage, ...],
        now: datetime,
    ) -> Tuple[ConversationMessage, ...]:
        current = conn.execute(
            f"""
            SELECT COUNT(*) AS count,
                   COALESCE(MAX(message_sequence), 0) AS last_sequence
            FROM {self.quoted_message_table}
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
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
            conn.execute(
                f"""
                INSERT INTO {self.quoted_message_table}
                (
                    message_id, conversation_id, message_sequence, turn_id,
                    role, content, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value.message_id,
                    conversation_id,
                    value.sequence,
                    value.turn_id,
                    value.role,
                    value.content,
                    metadata_json,
                    _sqlite_timestamp(now),
                ),
            )
            created.append(value)
        return tuple(created)

    def _update_after_append(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
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
        conn.execute(
            f"""
            UPDATE {self.quoted_conversation_table}
            SET title = ?, state_json = ?, revision = ?, updated_at = ?, expires_at = ?
            WHERE conversation_id = ?
            """,
            (
                title,
                state_json,
                _next_revision(int(row["revision"])),
                _sqlite_timestamp(now),
                _sqlite_timestamp(expires) if expires is not None else None,
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
        now = self._now()
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = self._owned_row(
                    conn,
                    user_id,
                    conversation_id,
                    _sqlite_timestamp(now),
                )
                if turn_id is not None:
                    existing = self._exchange_rows(conn, conversation_id, turn_id)
                    if existing is not None:
                        return existing
                if expected_revision is not None and int(row["revision"]) != expected_revision:
                    raise ConversationConflictError(
                        "Conversation was updated by another request."
                    )
                if expected_revision is None and expected_updated_at is not None and _parse_timestamp(row["updated_at"]) != expected_updated_at:
                    raise ConversationConflictError(
                        "Conversation was updated by another request."
                    )
                created = self._insert_messages(conn, conversation_id, messages, now)
                self._update_after_append(
                    conn,
                    row,
                    now=now,
                    new_state=new_state,
                    auto_title=auto_title,
                )
                return created
        except sqlite3.IntegrityError as exc:
            raise ConversationConflictError(
                "Message idempotency key already exists."
            ) from exc

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
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        turn_id = _canonical_uuid(turn_id, field_name="turn_id")
        with self._connect() as conn:
            self._owned_row(
                conn,
                user_id,
                conversation_id,
                _sqlite_timestamp(self._now()),
            )
            return self._exchange_rows(conn, conversation_id, turn_id)

    def rename(self, user_id: int, conversation_id: str, title: str) -> Conversation:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        title = _validate_title(title)
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._owned_row(
                conn, user_id, conversation_id, _sqlite_timestamp(now)
            )
            conn.execute(
                f"""
                UPDATE {self.quoted_conversation_table}
                SET title = ?, title_manually_set = 1, revision = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    title,
                    _next_revision(int(row["revision"])),
                    _sqlite_timestamp(now),
                    conversation_id,
                ),
            )
            restored = conn.execute(
                f"""
                SELECT * FROM {self.quoted_conversation_table}
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return _conversation_from_row(restored)

    def hard_delete(self, user_id: int, conversation_id: str) -> bool:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        with self._connect() as conn:
            result = conn.execute(
                f"""
                DELETE FROM {self.quoted_conversation_table}
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            )
            return result.rowcount > 0

    def hard_delete_all_by_user(
        self, user_id: int, *, limit: Optional[int] = None
    ) -> int:
        user_id = _validate_user_id(user_id)
        if limit is not None:
            limit = _bounded_limit(
                limit, maximum=MAX_CONVERSATIONS_PER_USER, field_name="limit"
            )
        with self._connect() as conn:
            if limit is None:
                result = conn.execute(
                    f"DELETE FROM {self.quoted_conversation_table} WHERE user_id = ?",
                    (user_id,),
                )
            else:
                result = conn.execute(
                    f"""
                    DELETE FROM {self.quoted_conversation_table}
                    WHERE conversation_id IN (
                        SELECT conversation_id
                        FROM {self.quoted_conversation_table}
                        WHERE user_id = ?
                        ORDER BY created_at, conversation_id
                        LIMIT ?
                    )
                    """,
                    (user_id, limit),
                )
            return result.rowcount
