"""Stable imports for user-owned conversation persistence.

Implementations live in conversation_storage; no transcript expires automatically.
"""

from .conversation_storage.common import (
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
    DEFAULT_CONVERSATION_RETENTION_DAYS,
    DEFAULT_CONVERSATION_TITLE,
    MAX_CONVERSATION_MESSAGE_LENGTH,
    MAX_CONVERSATION_STATE_BYTES,
    MAX_CONVERSATION_TITLE_LENGTH,
    MAX_MESSAGE_LIST_LIMIT,
    MAX_MESSAGE_METADATA_BYTES,
    MAX_MESSAGES_PER_CONVERSATION,
    NewConversationMessage,
)
from .conversation_storage.memory import InMemoryConversationRepository
from .conversation_storage.sqlite import SQLiteConversationRepository
from .conversation_storage.mysql import MySQLConversationRepository

__all__ = [
    "Conversation",
    "ConversationCapacityError",
    "ConversationConflictError",
    "ConversationIntegrityError",
    "ConversationMessage",
    "ConversationMessagePage",
    "ConversationNotFoundError",
    "ConversationPage",
    "ConversationRepository",
    "ConversationRepositoryError",
    "DEFAULT_CONVERSATION_RETENTION_DAYS",
    "DEFAULT_CONVERSATION_TITLE",
    "InMemoryConversationRepository",
    "MAX_CONVERSATION_MESSAGE_LENGTH",
    "MAX_CONVERSATION_STATE_BYTES",
    "MAX_CONVERSATION_TITLE_LENGTH",
    "MAX_MESSAGE_LIST_LIMIT",
    "MAX_MESSAGE_METADATA_BYTES",
    "MAX_MESSAGES_PER_CONVERSATION",
    "MySQLConversationRepository",
    "NewConversationMessage",
    "SQLiteConversationRepository",
]
