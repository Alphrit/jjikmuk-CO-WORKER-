"""memory conversation repository implementation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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
    NewConversationMessage,
    _as_utc,
    _bounded_limit,
    _canonical_uuid,
    _copy_conversation,
    _copy_message,
    _decode_cursor,
    _encode_cursor,
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


class InMemoryConversationRepository(ConversationRepository):
    def __init__(
        self,
        *,
        retention_days: Optional[int] = DEFAULT_CONVERSATION_RETENTION_DAYS,
        clock: Callable[[], datetime] = _utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
    ):
        self.retention_days = _validate_retention_days(retention_days)
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._lock = RLock()
        self._conversations: Dict[str, Conversation] = {}
        self._messages: Dict[str, list[ConversationMessage]] = {}

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _new_uuid(self, field_name: str) -> str:
        return _canonical_uuid(self._uuid_factory(), field_name=field_name)

    def readiness_check(self) -> Dict[str, Any]:
        return {
            "available": True,
            "provider": "memory",
            "retention_days": self.retention_days,
        }

    def _owned(
        self, user_id: int, conversation_id: str, now: datetime
    ) -> Conversation:
        value = self._conversations.get(conversation_id)
        if (
            value is None
            or value.user_id != user_id
        ):
            raise ConversationNotFoundError("Conversation not found.")
        return value

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
        with self._lock:
            existing = self._conversations.get(conversation_id)
            if existing is not None:
                if existing.user_id != user_id:
                    raise ConversationConflictError("Conversation UUID already exists.")
                if supplied:
                    return _copy_conversation(existing)
                raise ConversationConflictError("Conversation UUID already exists.")
            active_count = sum(
                item.user_id == user_id
                for item in self._conversations.values()
            )
            if active_count >= max_conversations:
                raise ConversationCapacityError(
                    "Active conversation limit reached; delete a room before creating another."
                )
            value = Conversation(
                conversation_id,
                user_id,
                normalized_title,
                title_manually_set,
                {},
                0,
                now,
                now,
                None,
            )
            self._conversations[conversation_id] = value
            self._messages[conversation_id] = []
            return _copy_conversation(value)

    def list(
        self,
        user_id: int,
        *,
        limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
        before: Optional[str] = None,
    ) -> ConversationPage:
        user_id = _validate_user_id(user_id)
        limit = _bounded_limit(limit, maximum=MAX_CONVERSATION_LIST_LIMIT)
        cursor: Optional[Tuple[datetime, str]] = None
        if before is not None:
            payload = _decode_cursor(
                before, expected_keys={"updated_at", "conversation_id"}
            )
            cursor = (
                _parse_timestamp(payload["updated_at"]),
                _canonical_uuid(payload["conversation_id"], field_name="conversation_id"),
            )
        now = self._now()
        with self._lock:
            values = [
                item
                for item in self._conversations.values()
                if item.user_id == user_id
                and (
                    cursor is None
                    or (item.updated_at, item.conversation_id) < cursor
                )
            ]
            values.sort(
                key=lambda item: (item.updated_at, item.conversation_id), reverse=True
            )
            selected = values[: limit + 1]
            has_more = len(selected) > limit
            selected = selected[:limit]
            next_cursor = None
            if has_more:
                last = selected[-1]
                next_cursor = _encode_cursor(
                    {
                        "updated_at": _sqlite_timestamp(last.updated_at),
                        "conversation_id": last.conversation_id,
                    }
                )
            return ConversationPage(
                tuple(_copy_conversation(item) for item in selected), next_cursor
            )

    def get(self, user_id: int, conversation_id: str) -> Optional[Conversation]:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        with self._lock:
            try:
                return _copy_conversation(
                    self._owned(user_id, conversation_id, self._now())
                )
            except ConversationNotFoundError:
                return None

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
        with self._lock:
            self._owned(user_id, conversation_id, self._now())
            rows = [
                item
                for item in self._messages[conversation_id]
                if before_sequence is None or item.sequence < before_sequence
            ]
            rows.sort(key=lambda item: item.sequence, reverse=True)
            selected = rows[: limit + 1]
            has_more = len(selected) > limit
            selected = selected[:limit]
            next_cursor = (
                _encode_cursor({"sequence": selected[-1].sequence})
                if has_more
                else None
            )
            selected.reverse()
            return ConversationMessagePage(
                tuple(_copy_message(item) for item in selected), next_cursor
            )

    def _existing_exchange(
        self, conversation_id: str, turn_id: str
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        rows = [
            item
            for item in self._messages[conversation_id]
            if item.turn_id == turn_id
        ]
        if not rows:
            return None
        by_role = {item.role: item for item in rows}
        if len(rows) != 2 or set(by_role) != {"user", "assistant"}:
            raise ConversationIntegrityError("Persisted exchange is incomplete.")
        return _copy_message(by_role["user"]), _copy_message(by_role["assistant"])

    def _append_locked(
        self,
        conversation: Conversation,
        messages: Tuple[NewConversationMessage, ...],
        *,
        now: datetime,
        new_state: Optional[Dict[str, Any]],
        auto_title: Optional[str],
    ) -> Tuple[ConversationMessage, ...]:
        existing = self._messages[conversation.conversation_id]
        if len(existing) + len(messages) > MAX_MESSAGES_PER_CONVERSATION:
            raise ConversationCapacityError(
                f"Conversation message limit of {MAX_MESSAGES_PER_CONVERSATION} reached."
            )
        occupied = {
            (item.turn_id, item.role)
            for item in existing
            if item.turn_id is not None
        }
        if any(
            item.turn_id is not None and (item.turn_id, item.role) in occupied
            for item in messages
        ):
            raise ConversationConflictError("turn_id and role already exist.")
        first_sequence = existing[-1].sequence + 1 if existing else 1
        created = tuple(
            ConversationMessage(
                self._new_uuid("message_id"),
                conversation.conversation_id,
                first_sequence + index,
                item.role,
                item.content,
                deepcopy(dict(item.metadata)),
                item.turn_id,
                now,
            )
            for index, item in enumerate(messages)
        )
        if len({item.message_id for item in created}) != len(created):
            raise ConversationConflictError("Generated message UUID already exists.")
        title = conversation.title
        if (
            auto_title is not None
            and not conversation.title_manually_set
            and title == DEFAULT_CONVERSATION_TITLE
        ):
            title = auto_title
        updated = replace(
            conversation,
            title=title,
            state=(
                deepcopy(new_state)
                if new_state is not None
                else deepcopy(conversation.state)
            ),
            revision=_next_revision(conversation.revision),
            updated_at=now,
            expires_at=None,
        )
        existing.extend(created)
        self._conversations[conversation.conversation_id] = updated
        return tuple(_copy_message(item) for item in created)

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
        with self._lock:
            now = self._now()
            return self._append_locked(
                self._owned(user_id, conversation_id, now),
                messages,
                now=now,
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
        with self._lock:
            now = self._now()
            conversation = self._owned(user_id, conversation_id, now)
            existing = self._existing_exchange(conversation_id, turn_id)
            if existing is not None:
                return existing
            if revision is not None and conversation.revision != revision:
                raise ConversationConflictError(
                    "Conversation was updated by another request."
                )
            if expected_time is not None and conversation.updated_at != expected_time:
                raise ConversationConflictError(
                    "Conversation was updated by another request."
                )
            created = self._append_locked(
                conversation,
                messages,
                now=now,
                new_state=state,
                auto_title=title,
            )
            return created[0], created[1]

    def get_exchange_by_turn_id(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        turn_id = _canonical_uuid(turn_id, field_name="turn_id")
        with self._lock:
            self._owned(user_id, conversation_id, self._now())
            return self._existing_exchange(conversation_id, turn_id)

    def rename(self, user_id: int, conversation_id: str, title: str) -> Conversation:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        title = _validate_title(title)
        with self._lock:
            current = self._owned(user_id, conversation_id, self._now())
            updated = replace(
                current,
                title=title,
                title_manually_set=True,
                revision=_next_revision(current.revision),
                updated_at=self._now(),
            )
            self._conversations[conversation_id] = updated
            return _copy_conversation(updated)

    def hard_delete(self, user_id: int, conversation_id: str) -> bool:
        user_id = _validate_user_id(user_id)
        conversation_id = _canonical_uuid(conversation_id, field_name="conversation_id")
        with self._lock:
            current = self._conversations.get(conversation_id)
            if current is None or current.user_id != user_id:
                return False
            del self._conversations[conversation_id]
            self._messages.pop(conversation_id, None)
            return True

    def hard_delete_all_by_user(
        self, user_id: int, *, limit: Optional[int] = None
    ) -> int:
        user_id = _validate_user_id(user_id)
        if limit is not None:
            limit = _bounded_limit(
                limit, maximum=MAX_CONVERSATIONS_PER_USER, field_name="limit"
            )
        with self._lock:
            ids = [
                item.conversation_id
                for item in self._conversations.values()
                if item.user_id == user_id
            ]
            if limit is not None:
                ids = ids[:limit]
            for conversation_id in ids:
                del self._conversations[conversation_id]
                self._messages.pop(conversation_id, None)
            return len(ids)
