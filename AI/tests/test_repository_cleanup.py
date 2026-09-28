"""Repository compatibility and no-expiry regressions after storage separation."""

import importlib
import sqlite3
from uuid import uuid4

import pytest

from app.infrastructure import repository
from app.infrastructure.conversations import SQLiteConversationRepository


@pytest.mark.parametrize(("name", "module"), [
    ("ProductRepository", "product_storage.common"),
    ("InMemoryProductRepository", "product_storage.memory"),
    ("SQLiteProductRepository", "product_storage.sqlite"),
    ("MySQLProductRepository", "product_storage.mysql"),
    ("ActivityRepository", "activity_storage.common"),
    ("InMemoryActivityRepository", "activity_storage.memory"),
    ("SQLiteActivityRepository", "activity_storage.sqlite"),
    ("MySQLActivityRepository", "activity_storage.mysql"),
])
def test_legacy_imports_are_the_same_implementation(name, module):
    implementation = importlib.import_module(f"app.infrastructure.{module}")
    assert getattr(repository, name) is getattr(implementation, name)


def test_all_mysql_repositories_use_one_shared_pool_registry():
    support = importlib.import_module("app.infrastructure.mysql_support")
    assert repository._MYSQL_CONNECTION_POOLS is support._MYSQL_CONNECTION_POOLS
    assert repository.dispose_mysql_connection_pools is support.dispose_mysql_connection_pools
    for module in ("product_storage.mysql", "activity_storage.mysql", "conversation_storage.mysql"):
        implementation = importlib.import_module(f"app.infrastructure.{module}")
        assert implementation._shared_mysql_connection_pool is support._shared_mysql_connection_pool


def test_new_sqlite_rooms_do_not_create_unused_expiry_index(tmp_path):
    path = str(tmp_path / "rooms.sqlite3")
    SQLiteConversationRepository(path)
    with sqlite3.connect(path) as connection:
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(chatbot_conversations)")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chatbot_conversations)")}
    assert "idx_chatbot_conversations_expires" not in indexes
    assert "idx_chatbot_conversations_user_updated" in indexes
    assert "expires_at" in columns  # Kept for existing schema compatibility.


def test_reopening_keeps_legacy_schema_and_expired_dated_messages(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    first = SQLiteConversationRepository(path)
    room = first.create(7)
    first.append_exchange(7, room.conversation_id, turn_id=str(uuid4()),
                          user_content="저염 간식 알려줘", assistant_content="표시를 확인해 주세요.")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE INDEX idx_chatbot_conversations_expires ON chatbot_conversations(expires_at, conversation_id)")
        connection.execute("UPDATE chatbot_conversations SET expires_at = '2000-01-01T00:00:00+00:00'")
    reopened = SQLiteConversationRepository(path)
    assert reopened.get(7, room.conversation_id).expires_at is None
    assert reopened.purge_expired() == 0
    assert len(reopened.list(7).items) == 1
    assert len(reopened.list_messages(7, room.conversation_id).items) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_chatbot_conversations_expires'").fetchone()
        assert connection.execute("SELECT expires_at FROM chatbot_conversations").fetchone()[0].startswith("2000-")
