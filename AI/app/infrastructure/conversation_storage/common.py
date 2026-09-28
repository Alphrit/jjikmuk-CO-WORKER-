"""User-owned conversation and transcript persistence.

This module intentionally contains only the data required by the chatbot user:
rooms, titles, messages, bounded conversation state, and turn idempotency.
Operational audit logs are stored separately and never duplicate transcripts.
"""

from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from uuid import UUID



DEFAULT_CONVERSATION_RETENTION_DAYS: Optional[int] = None
DEFAULT_MAX_CONVERSATIONS_PER_USER = 200
MAX_CONVERSATIONS_PER_USER = 10_000
MAX_MESSAGES_PER_CONVERSATION = 400
DEFAULT_CONVERSATION_LIST_LIMIT = 20
MAX_CONVERSATION_LIST_LIMIT = 100
DEFAULT_MESSAGE_LIST_LIMIT = 50
MAX_MESSAGE_LIST_LIMIT = 200
MAX_APPEND_BATCH_SIZE = 20
MAX_CONVERSATION_TITLE_LENGTH = 32
MAX_CONVERSATION_MESSAGE_LENGTH = 16_000
MAX_CONVERSATION_STATE_BYTES = 8 * 1024
MAX_MESSAGE_METADATA_BYTES = 64 * 1024
DEFAULT_CONVERSATION_TITLE = "새 채팅"

_UTC = timezone.utc


class ConversationRepositoryError(RuntimeError):
    """Base class for expected conversation persistence failures."""


class ConversationNotFoundError(ConversationRepositoryError):
    """The room is missing or not owned by the supplied user."""


class ConversationCapacityError(ConversationRepositoryError):
    """A configured room or message limit has been reached."""


class ConversationConflictError(ConversationRepositoryError):
    """An idempotency key or optimistic revision conflicts."""


class ConversationIntegrityError(ConversationRepositoryError):
    """Persisted data violates the conversation storage contract."""


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    user_id: int
    title: str
    title_manually_set: bool
    state: Dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]

    @property
    def id(self) -> str:
        return self.conversation_id


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    metadata: Dict[str, Any]
    turn_id: Optional[str]
    created_at: datetime

    @property
    def id(self) -> str:
        return self.message_id


@dataclass(frozen=True, slots=True)
class NewConversationMessage:
    role: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    turn_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: Tuple[Conversation, ...]
    next_cursor: Optional[str]


@dataclass(frozen=True, slots=True)
class ConversationMessagePage:
    """Newest message page returned in chronological display order."""

    items: Tuple[ConversationMessage, ...]
    next_cursor: Optional[str]


def _utc_now() -> datetime:
    return datetime.now(tz=_UTC)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    return value.replace(tzinfo=_UTC) if value.tzinfo is None else value.astimezone(_UTC)


def _sqlite_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _mysql_timestamp(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        raise ConversationIntegrityError("Conversation timestamp is invalid.")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ConversationIntegrityError("Conversation timestamp is invalid.") from exc


def _optional_timestamp(value: Any) -> Optional[datetime]:
    return None if value is None else _parse_timestamp(value)


def _validate_user_id(user_id: int) -> int:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or not 1 <= user_id <= 9_223_372_036_854_775_807
    ):
        raise ValueError("user_id must be a positive signed 64-bit integer")
    return user_id


def _validate_revision(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 9_223_372_036_854_775_807
    ):
        raise ValueError("expected_revision must be a non-negative signed 64-bit integer")
    return value


def _next_revision(value: int) -> int:
    value = _validate_revision(value)
    if value == 9_223_372_036_854_775_807:
        raise ConversationCapacityError("Conversation revision limit reached.")
    return value + 1


def _canonical_uuid(value: Any, *, field_name: str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc


def _validate_title(value: Optional[str]) -> str:
    if value is None:
        return DEFAULT_CONVERSATION_TITLE
    if not isinstance(value, str):
        raise ValueError("title must be a string")
    title = " ".join(value.strip().split())
    if not title:
        raise ValueError("title must not be blank")
    if len(title) > MAX_CONVERSATION_TITLE_LENGTH:
        raise ValueError(f"title exceeds {MAX_CONVERSATION_TITLE_LENGTH} characters")
    return title


def _validate_retention_days(value: Optional[int]) -> Optional[int]:
    if value is not None:
        raise ValueError(
            "Conversation expiry is not supported; conversations are retained "
            "until the owner deletes them."
        )
    return None


def _bounded_limit(value: int, *, maximum: int, field_name: str = "limit") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value


def _normalize_json_mapping(
    value: Optional[Mapping[str, Any]],
    *,
    max_bytes: int,
    field_name: str,
) -> Tuple[Dict[str, Any], str]:
    if value is None:
        normalized: Dict[str, Any] = {}
    elif isinstance(value, Mapping):
        normalized = dict(value)
    else:
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(f"{field_name} must contain JSON-compatible values") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} UTF-8 bytes")
    return json.loads(encoded), encoded


def _decode_json_mapping(value: Any, *, max_bytes: int) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            candidate = dict(value)
            raw = json.dumps(candidate, ensure_ascii=False, allow_nan=False)
        elif isinstance(value, (bytes, bytearray)):
            raw = bytes(value).decode("utf-8")
            candidate = json.loads(raw)
        elif isinstance(value, str):
            raw = value
            candidate = json.loads(raw)
        else:
            return {}
        if len(raw.encode("utf-8")) > max_bytes:
            raise ConversationIntegrityError("Saved JSON object exceeds its storage limit.")
    except ConversationIntegrityError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError, RecursionError):
        return {}
    return deepcopy(candidate) if isinstance(candidate, dict) else {}


def _validate_message(message: NewConversationMessage) -> NewConversationMessage:
    if not isinstance(message, NewConversationMessage):
        raise ValueError("messages must contain NewConversationMessage values")
    if message.role not in {"user", "assistant"}:
        raise ValueError("message role must be user or assistant")
    if not isinstance(message.content, str):
        raise ValueError("message content must be a string")
    if len(message.content) > MAX_CONVERSATION_MESSAGE_LENGTH:
        raise ValueError(
            f"message content exceeds {MAX_CONVERSATION_MESSAGE_LENGTH} characters"
        )
    if message.role == "assistant" and not message.content.strip():
        raise ValueError("assistant message content must not be blank")
    metadata, _ = _normalize_json_mapping(
        message.metadata,
        max_bytes=MAX_MESSAGE_METADATA_BYTES,
        field_name="message metadata",
    )
    turn_id = (
        _canonical_uuid(message.turn_id, field_name="turn_id")
        if message.turn_id is not None
        else None
    )
    return replace(message, metadata=metadata, turn_id=turn_id)


def _validate_messages(
    messages: Sequence[NewConversationMessage],
) -> Tuple[NewConversationMessage, ...]:
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise ValueError("messages must be a sequence")
    if not 1 <= len(messages) <= MAX_APPEND_BATCH_SIZE:
        raise ValueError(
            f"messages must contain between 1 and {MAX_APPEND_BATCH_SIZE} items"
        )
    result = tuple(_validate_message(item) for item in messages)
    turn_roles = [
        (item.turn_id, item.role) for item in result if item.turn_id is not None
    ]
    if len(turn_roles) != len(set(turn_roles)):
        raise ValueError("a turn_id cannot repeat the same role in one batch")
    return result


def _encode_cursor(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, expected_keys: set[str]) -> Dict[str, Any]:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        result = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(result, dict) or set(result) != expected_keys:
        raise ValueError("cursor is invalid")
    return result


def _copy_conversation(value: Conversation) -> Conversation:
    return replace(value, state=deepcopy(value.state))


def _copy_message(value: ConversationMessage) -> ConversationMessage:
    return replace(value, metadata=deepcopy(value.metadata))


def _persisted_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    raise ConversationIntegrityError("Saved conversation title flag is invalid.")


def _conversation_from_row(row: Mapping[str, Any]) -> Conversation:
    try:
        return Conversation(
            conversation_id=_canonical_uuid(row["conversation_id"], field_name="conversation_id"),
            user_id=_validate_user_id(int(row["user_id"])),
            title=_validate_title(row["title"]),
            title_manually_set=_persisted_bool(row["title_manually_set"]),
            state=_decode_json_mapping(
                row["state_json"], max_bytes=MAX_CONVERSATION_STATE_BYTES
            ),
            revision=_validate_revision(int(row["revision"])),
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
            # Legacy column is retained for schema compatibility, not expiry.
            expires_at=None,
        )
    except ConversationIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ConversationIntegrityError("Saved conversation row is invalid.") from exc


def _message_from_row(row: Mapping[str, Any]) -> ConversationMessage:
    try:
        sequence = int(row["message_sequence"])
        if not 1 <= sequence <= MAX_MESSAGES_PER_CONVERSATION:
            raise ValueError("message sequence is outside the storage contract")
        message = _validate_message(
            NewConversationMessage(
                role=row["role"],
                content=row["content"],
                metadata=_decode_json_mapping(
                    row["metadata_json"], max_bytes=MAX_MESSAGE_METADATA_BYTES
                ),
                turn_id=row["turn_id"],
            )
        )
        return ConversationMessage(
            message_id=_canonical_uuid(row["message_id"], field_name="message_id"),
            conversation_id=_canonical_uuid(
                row["conversation_id"], field_name="conversation_id"
            ),
            sequence=sequence,
            role=message.role,
            content=message.content,
            metadata=dict(message.metadata),
            turn_id=message.turn_id,
            created_at=_parse_timestamp(row["created_at"]),
        )
    except ConversationIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ConversationIntegrityError("Saved message row is invalid.") from exc


class ConversationRepository:
    CONVERSATION_COLUMNS = {
        "conversation_id", "user_id", "title", "title_manually_set", "state_json",
        "revision", "created_at", "updated_at", "expires_at",
    }
    MESSAGE_COLUMNS = {
        "message_id", "conversation_id", "message_sequence", "turn_id", "role",
        "content", "metadata_json", "created_at",
    }

    """Minimal storage contract required by the public chat API."""

    def readiness_check(self) -> Dict[str, Any]:
        raise NotImplementedError

    def create(
        self,
        user_id: int,
        title: Optional[str] = None,
        *,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS_PER_USER,
        title_manually_set: bool = False,
        conversation_id: Optional[str] = None,
    ) -> Conversation:
        raise NotImplementedError

    def list(
        self,
        user_id: int,
        *,
        limit: int = DEFAULT_CONVERSATION_LIST_LIMIT,
        before: Optional[str] = None,
    ) -> ConversationPage:
        raise NotImplementedError

    def list_conversations(self, *args: Any, **kwargs: Any) -> ConversationPage:
        return self.list(*args, **kwargs)

    def get(self, user_id: int, conversation_id: str) -> Optional[Conversation]:
        raise NotImplementedError

    def list_messages(
        self,
        user_id: int,
        conversation_id: str,
        *,
        limit: int = DEFAULT_MESSAGE_LIST_LIMIT,
        before: Optional[str] = None,
    ) -> ConversationMessagePage:
        raise NotImplementedError

    def append(
        self,
        user_id: int,
        conversation_id: str,
        role: str,
        content: str,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
        turn_id: Optional[str] = None,
    ) -> ConversationMessage:
        return self.append_many(
            user_id,
            conversation_id,
            [
                NewConversationMessage(
                    role,
                    content,
                    {} if metadata is None else metadata,
                    turn_id,
                )
            ],
        )[0]

    def append_many(
        self,
        user_id: int,
        conversation_id: str,
        messages: Sequence[NewConversationMessage],
        *,
        new_state: Optional[Mapping[str, Any]] = None,
        auto_title: Optional[str] = None,
    ) -> Tuple[ConversationMessage, ...]:
        raise NotImplementedError

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
        raise NotImplementedError

    def get_exchange_by_turn_id(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> Optional[Tuple[ConversationMessage, ConversationMessage]]:
        raise NotImplementedError

    def rename(self, user_id: int, conversation_id: str, title: str) -> Conversation:
        raise NotImplementedError

    def hard_delete(self, user_id: int, conversation_id: str) -> bool:
        raise NotImplementedError

    def hard_delete_all_by_user(
        self, user_id: int, *, limit: Optional[int] = None
    ) -> int:
        raise NotImplementedError

    def purge_expired(
        self, *, now: Optional[datetime] = None, limit: int = 1000
    ) -> int:
        """Legacy no-op: transcripts are removed only by owner deletion."""
        return 0
