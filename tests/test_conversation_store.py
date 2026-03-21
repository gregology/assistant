"""Tests for ConversationStore."""

import json

import pytest

from app.conversation_store import ConversationStore


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "chats")


class TestCreate:
    def test_returns_hex_id(self, store):
        cid = store.create()
        assert isinstance(cid, str)
        assert len(cid) == 16
        int(cid, 16)  # validates hex

    def test_file_exists_on_disk(self, store):
        cid = store.create()
        assert (store._dir / f"{cid}.jsonl").is_file()

    def test_unique_ids(self, store):
        ids = {store.create() for _ in range(50)}
        assert len(ids) == 50


class TestExists:
    def test_true_for_created(self, store):
        cid = store.create()
        assert store.exists(cid) is True

    def test_false_for_missing(self, store):
        assert store.exists("nonexistent") is False


class TestAppend:
    def test_writes_json_line(self, store):
        cid = store.create()
        store.append(cid, "user", "chat", "hello")
        path = store._dir / f"{cid}.jsonl"
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["role"] == "user"
        assert msg["type"] == "chat"
        assert msg["content"] == "hello"
        assert "ts" in msg
        assert "metadata" not in msg

    def test_includes_metadata(self, store):
        cid = store.create()
        store.append(cid, "system", "confirmation", "Do this?", metadata={"proposal_id": "abc"})
        lines = (store._dir / f"{cid}.jsonl").read_text().strip().splitlines()
        msg = json.loads(lines[0])
        assert msg["metadata"]["proposal_id"] == "abc"

    def test_raises_keyerror_for_missing(self, store):
        with pytest.raises(KeyError):
            store.append("nonexistent", "user", "chat", "hello")


class TestRead:
    def test_returns_messages_in_order(self, store):
        cid = store.create()
        store.append(cid, "user", "chat", "first")
        store.append(cid, "assistant", "chat", "second")
        messages = store.read(cid)
        assert len(messages) == 2
        assert messages[0]["content"] == "first"
        assert messages[1]["content"] == "second"

    def test_empty_conversation(self, store):
        cid = store.create()
        assert store.read(cid) == []

    def test_raises_keyerror_for_missing(self, store):
        with pytest.raises(KeyError):
            store.read("nonexistent")


class TestClear:
    def test_truncates_file(self, store):
        cid = store.create()
        store.append(cid, "user", "chat", "hello")
        store.clear(cid)
        assert store.read(cid) == []
        assert store.exists(cid)

    def test_raises_keyerror_for_missing(self, store):
        with pytest.raises(KeyError):
            store.clear("nonexistent")


class TestListConversations:
    def test_lists_conversations(self, store):
        cid1 = store.create()
        store.append(cid1, "user", "chat", "hello")
        cid2 = store.create()
        store.append(cid2, "user", "chat", "world")
        store.append(cid2, "assistant", "chat", "hi")
        result = store.list_conversations()
        assert len(result) == 2
        ids = {c["id"] for c in result}
        assert ids == {cid1, cid2}
        conv2 = next(c for c in result if c["id"] == cid2)
        assert conv2["message_count"] == 2

    def test_empty_store(self, store):
        assert store.list_conversations() == []

    def test_empty_conversation_included(self, store):
        store.create()
        result = store.list_conversations()
        assert len(result) == 1
        assert result[0]["message_count"] == 0


class TestFindProposal:
    def test_finds_by_proposal_id(self, store):
        cid = store.create()
        store.append(cid, "assistant", "chat", "I'll do it.")
        store.append(
            cid,
            "system",
            "confirmation",
            "Confirm?",
            metadata={"proposal_id": "p123", "action": "test"},
        )
        result = store.find_proposal(cid, "p123")
        assert result is not None
        assert result["metadata"]["action"] == "test"

    def test_returns_none_when_not_found(self, store):
        cid = store.create()
        store.append(cid, "user", "chat", "hello")
        assert store.find_proposal(cid, "nonexistent") is None

    def test_finds_most_recent(self, store):
        cid = store.create()
        store.append(
            cid,
            "system",
            "confirmation",
            "First?",
            metadata={"proposal_id": "p1", "action": "first"},
        )
        store.append(
            cid,
            "system",
            "confirmation",
            "Second?",
            metadata={"proposal_id": "p1", "action": "second"},
        )
        result = store.find_proposal(cid, "p1")
        assert result["metadata"]["action"] == "second"
