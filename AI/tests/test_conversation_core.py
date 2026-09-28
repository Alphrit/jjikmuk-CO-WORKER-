"""Core saved-chat contract: rooms, transcript ownership, titles, and deletion."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.infrastructure.conversations import (
    ConversationConflictError,
    InMemoryConversationRepository,
    SQLiteConversationRepository,
)
from app.infrastructure.request_identity import (
    reset_conversation_identity_nonce_registry,
    sign_conversation_identity,
)
from app.services.conversations import (
    get_conversation_repository,
    set_conversation_repository,
)


class FixedClock:
    def __call__(self):
        return datetime(2026, 9, 10, tzinfo=timezone.utc)


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return InMemoryConversationRepository(clock=FixedClock())
    return SQLiteConversationRepository(
        str(tmp_path / "chat.sqlite3"),
        clock=FixedClock(),
    )


def test_rooms_keep_full_transcripts_until_owner_deletes_them(repository):
    owner = 11
    other = 12
    first = repository.create(owner)
    second = repository.create(owner, "저염 간식", title_manually_set=True)
    turn_id = str(uuid4())

    exchange = repository.append_exchange(
        owner,
        first.conversation_id,
        turn_id=turn_id,
        user_content="새우 알레르기가 있는데 먹어도 돼?",
        assistant_content="새우 성분이 있어 피하는 편이 좋습니다.",
        assistant_metadata={"response": {"intent": "can_i_eat"}},
        new_state={"last_intent": "can_i_eat"},
        auto_title="새우 알레르기 확인",
        expected_revision=first.revision,
    )

    assert repository.get(owner, first.conversation_id).title == "새우 알레르기 확인"
    assert repository.get(owner, first.conversation_id).expires_at is None
    assert [item.content for item in repository.list_messages(
        owner, first.conversation_id
    ).items] == [
        "새우 알레르기가 있는데 먹어도 돼?",
        "새우 성분이 있어 피하는 편이 좋습니다.",
    ]
    assert repository.get_exchange_by_turn_id(
        owner, first.conversation_id, turn_id
    ) == exchange
    assert repository.get(other, first.conversation_id) is None
    assert {item.conversation_id for item in repository.list(owner).items} == {
        first.conversation_id,
        second.conversation_id,
    }

    renamed = repository.rename(owner, first.conversation_id, "내 간식 확인")
    assert renamed.title == "내 간식 확인"
    assert renamed.title_manually_set is True
    assert repository.hard_delete(other, first.conversation_id) is False
    assert repository.hard_delete(owner, first.conversation_id) is True
    assert repository.get(owner, first.conversation_id) is None
    assert repository.hard_delete_all_by_user(owner) == 1
    assert repository.list(owner).items == ()


def test_conversation_repository_rejects_expiring_retention_policy():
    with pytest.raises(ValueError, match="retained until the owner deletes"):
        InMemoryConversationRepository(retention_days=30)


def test_legacy_expiry_cannot_hide_or_delete_owned_transcripts(tmp_path):
    import sqlite3
    database = str(tmp_path / "legacy_expiry.sqlite3")
    repository = SQLiteConversationRepository(database, clock=FixedClock())
    room = repository.create(11)
    repository.append_exchange(11, room.conversation_id, turn_id=str(uuid4()), user_content="질문", assistant_content="답변")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE chatbot_conversations SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert repository.get(11, room.conversation_id).expires_at is None
    assert len(repository.list(11).items) == 1
    assert repository.create(11, conversation_id=room.conversation_id).conversation_id == room.conversation_id
    assert repository.purge_expired() == 0
    assert len(repository.list_messages(11, room.conversation_id).items) == 2
    assert repository.get(12, room.conversation_id) is None
    assert repository.hard_delete(11, room.conversation_id)


def test_turn_id_is_idempotent_but_optimistic_revision_prevents_lost_updates(
    repository,
):
    room = repository.create(1)
    turn_id = str(uuid4())
    first = repository.append_exchange(
        1,
        room.conversation_id,
        turn_id=turn_id,
        user_content="질문",
        assistant_content="답변",
        expected_revision=0,
    )
    retried = repository.append_exchange(
        1,
        room.conversation_id,
        turn_id=turn_id,
        user_content="재시도 입력",
        assistant_content="재시도 답변",
        expected_revision=0,
    )
    assert retried == first

    with pytest.raises(ConversationConflictError):
        repository.append_exchange(
            1,
            room.conversation_id,
            turn_id=str(uuid4()),
            user_content="동시 질문",
            assistant_content="동시 답변",
            expected_revision=0,
        )


def test_sqlite_transcript_survives_repository_reopen(tmp_path):
    path = str(tmp_path / "persist.sqlite3")
    first = SQLiteConversationRepository(path)
    room = first.create(7, "보관할 채팅", title_manually_set=True)
    first.append_exchange(
        7,
        room.conversation_id,
        turn_id=str(uuid4()),
        user_content="저염식으로 뭘 먹어야 해?",
        assistant_content="제품 라벨의 저염 표시를 먼저 확인해 주세요.",
    )

    reopened = SQLiteConversationRepository(path)
    assert reopened.get(7, room.conversation_id).title == "보관할 채팅"
    assert len(reopened.list_messages(7, room.conversation_id).items) == 2


API_TOKEN = "conversation-test-token"
IDENTITY_SECRET = "conversation-test-identity-secret-32-bytes"


class SigningClient(TestClient):
    def build_request(self, method, url, **kwargs):
        request = super().build_request(method, url, **kwargs)
        signed_method = request.headers.pop("x-test-sign-method", None)
        signed_path = request.headers.pop("x-test-sign-path", None)
        if signed_method and signed_path:
            request.headers["X-Food-Chatbot-Identity-Signature"] = (
                sign_conversation_identity(
                    timestamp=request.headers[
                        "X-Food-Chatbot-Identity-Timestamp"
                    ],
                    method=signed_method,
                    path=signed_path,
                    user_id=request.headers["X-Food-Chatbot-User-Id"],
                    nonce=request.headers["X-Food-Chatbot-Identity-Nonce"],
                    body=request.content,
                )
            )
        return request


def _headers(method: str, path: str, *, user_id: int = 101):
    return {
        "X-Food-Chatbot-Token": API_TOKEN,
        "X-Food-Chatbot-User-Id": str(user_id),
        "X-Food-Chatbot-Identity-Timestamp": str(int(time.time())),
        "X-Food-Chatbot-Identity-Nonce": f"test-{uuid4()}",
        "X-Food-Chatbot-Identity-Signature": "0" * 64,
        "X-Test-Sign-Method": method,
        "X-Test-Sign-Path": path,
    }


def test_public_room_api_can_create_rename_list_and_delete(monkeypatch):
    original = get_conversation_repository()
    repository = InMemoryConversationRepository()
    set_conversation_repository(repository)
    reset_conversation_identity_nonce_registry()
    monkeypatch.setenv("FOOD_CHATBOT_ENABLE_CONVERSATIONS", "true")
    monkeypatch.setenv("FOOD_CHATBOT_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("FOOD_CHATBOT_USER_IDENTITY_SECRET", IDENTITY_SECRET)
    client = SigningClient(api_main.app)
    try:
        conversation_id = str(uuid4())
        payload = {"conversation_id": conversation_id, "title": "간식 질문"}
        created = client.post(
            "/conversations",
            content=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                **_headers("POST", "/conversations"),
                "Content-Type": "application/json",
            },
        )
        assert created.status_code == 201
        assert created.json()["title"] == "간식 질문"

        path = f"/conversations/{conversation_id}"
        renamed = client.patch(
            path,
            content='{"title":"가족 간식"}'.encode("utf-8"),
            headers={
                **_headers("PATCH", path),
                "Content-Type": "application/json",
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "가족 간식"

        listed = client.get(
            "/conversations",
            headers=_headers("GET", "/conversations"),
        )
        assert listed.status_code == 200
        assert [item["conversation_id"] for item in listed.json()["conversations"]] == [
            conversation_id
        ]

        hidden = client.get(
            path,
            headers=_headers("GET", path, user_id=202),
        )
        assert hidden.status_code == 404

        deleted = client.delete(path, headers=_headers("DELETE", path))
        assert deleted.status_code == 204
    finally:
        set_conversation_repository(original)
