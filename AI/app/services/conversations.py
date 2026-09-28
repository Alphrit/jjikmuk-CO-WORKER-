"""Application-level access to the saved-conversation repository.

Saved conversations intentionally live outside the operational activity/audit log.
The API configures this repository alongside the product and activity repositories
at startup, while tests can replace it explicitly without touching global DB state.
"""

from __future__ import annotations

from app.infrastructure.conversations import (
    ConversationRepository,
    InMemoryConversationRepository,
)


_default_conversation_repository: ConversationRepository = (
    InMemoryConversationRepository()
)


def get_conversation_repository() -> ConversationRepository:
    return _default_conversation_repository


def set_conversation_repository(repository: ConversationRepository) -> None:
    global _default_conversation_repository
    _default_conversation_repository = repository


__all__ = [
    "get_conversation_repository",
    "set_conversation_repository",
]
