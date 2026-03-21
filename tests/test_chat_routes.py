"""Tests for chat API endpoints."""

from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.chat import chat_service
from app.conversation_store import ConversationStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_chat_store(tmp_path, monkeypatch):
    """Replace the singleton's store and clear task cache."""
    store = ConversationStore(tmp_path / "chats")
    monkeypatch.setattr(chat_service, "_store", store)
    chat_service._processed_tasks.clear()


class TestListConversations:
    def test_returns_empty_list(self):
        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 200
        assert resp.json() == {"conversations": []}

    def test_returns_created_conversations(self):
        client.post("/api/chat/conversations")
        client.post("/api/chat/conversations")
        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 200
        assert len(resp.json()["conversations"]) == 2


class TestCreateConversation:
    def test_returns_conversation_id(self):
        resp = client.post("/api/chat/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversation_id" in data
        assert len(data["conversation_id"]) == 16


class TestGetHistory:
    def test_returns_message_list(self):
        cid = chat_service.create_conversation()
        resp = client.get(f"/api/chat/conversations/{cid}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == cid
        assert data["messages"] == []

    def test_404_for_unknown_conversation(self):
        resp = client.get("/api/chat/conversations/nonexistent/history")
        assert resp.status_code == 404


class TestSendMessage:
    def test_chat_message_returns_task_id(self, queue_dir):
        cid = chat_service.create_conversation()
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-abc"
            resp = client.post(
                f"/api/chat/conversations/{cid}/messages",
                json={"content": "Hello"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "chat"
        assert data["task_id"] == "task-abc"

    def test_clear_command_returns_immediately(self):
        cid = chat_service.create_conversation()
        resp = client.post(
            f"/api/chat/conversations/{cid}/messages",
            json={"content": "/clear"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "command"
        assert data["message"]["content"] == "Conversation cleared."
        assert data["message"]["type"] == "command"

    def test_404_for_unknown_conversation(self):
        resp = client.post(
            "/api/chat/conversations/nonexistent/messages",
            json={"content": "Hello"},
        )
        assert resp.status_code == 404


class TestPollTask:
    def test_pending_status(self, queue_dir):
        task_id = "1_20260319T100000Z_abc123--def456--chat.message"
        pending_path = queue_dir / "pending" / f"{task_id}.yaml"
        pending_path.write_text(yaml.dump({"id": task_id, "status": "pending", "payload": {}}))

        resp = client.get(f"/api/chat/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "pending"}

    def test_done_status_returns_messages_list(self, queue_dir):
        task_id = "1_20260319T100000Z_abc123--def456--chat.message"
        cid = chat_service.create_conversation()
        done_path = queue_dir / "done" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "done",
            "result": {"content": "LLM response", "conversation_id": cid},
            "payload": {"type": "chat.message", "conversation_id": cid},
        }
        done_path.write_text(yaml.dump(task_data))

        resp = client.get(f"/api/chat/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "assistant"
        assert data["messages"][0]["content"] == "LLM response"
        assert data["messages"][0]["type"] == "chat"

    def test_done_poll_twice_appends_once(self, queue_dir):
        task_id = "1_20260319T100000Z_idempotent--def456--chat.message"
        cid = chat_service.create_conversation()
        done_path = queue_dir / "done" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "done",
            "result": {"content": "Only once", "conversation_id": cid},
            "payload": {"type": "chat.message", "conversation_id": cid},
        }
        done_path.write_text(yaml.dump(task_data))

        # Poll twice
        resp1 = client.get(f"/api/chat/tasks/{task_id}")
        resp2 = client.get(f"/api/chat/tasks/{task_id}")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["messages"] == resp2.json()["messages"]

        # Message should appear exactly once in conversation history
        history = chat_service.get_history(cid)
        matching = [m for m in history if m.content == "Only once"]
        assert len(matching) == 1

    def test_done_service_result_with_text(self, queue_dir):
        task_id = "1_20260319T100000Z_svc123--def456--service.github.create_issue"
        cid = chat_service.create_conversation()
        done_path = queue_dir / "done" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "done",
            "result": {
                "text": "Issue #42 created: https://github.com/org/repo/issues/42",
                "number": 42,
            },
            "payload": {
                "type": "service.github.create_issue",
                "on_result": [
                    {"type": "chat_reply", "conversation_id": cid},
                ],
            },
        }
        done_path.write_text(yaml.dump(task_data))

        resp = client.get(f"/api/chat/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "system"
        assert "Issue #42" in data["messages"][0]["content"]

        history = chat_service.get_history(cid)
        assert any("Issue #42" in m.content for m in history)

    def test_failed_poll_twice_appends_once(self, queue_dir):
        task_id = "1_20260319T100000Z_idem_fail--def456--chat.message"
        cid = chat_service.create_conversation()
        failed_path = queue_dir / "failed" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "failed",
            "error": "Timeout",
            "payload": {"type": "chat.message", "conversation_id": cid},
        }
        failed_path.write_text(yaml.dump(task_data))

        resp1 = client.get(f"/api/chat/tasks/{task_id}")
        resp2 = client.get(f"/api/chat/tasks/{task_id}")

        assert resp1.json()["messages"] == resp2.json()["messages"]

        history = chat_service.get_history(cid)
        error_msgs = [m for m in history if "Timeout" in m.content]
        assert len(error_msgs) == 1

    def test_done_structured_with_proposal(self, queue_dir):
        task_id = "1_20260319T100000Z_abc123--def456--chat.message"
        cid = chat_service.create_conversation()
        done_path = queue_dir / "done" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "done",
            "result": {
                "structured": {
                    "reply": "I'll create that.",
                    "proposal": {
                        "action": "create_github_issue",
                        "parameters": {"title": "Test"},
                        "description": "Create issue: Test",
                    },
                },
                "conversation_id": cid,
            },
            "payload": {"type": "chat.message", "conversation_id": cid},
        }
        done_path.write_text(yaml.dump(task_data))

        resp = client.get(f"/api/chat/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["type"] == "chat"
        assert data["messages"][1]["type"] == "confirmation"
        assert data["messages"][1]["metadata"]["action"] == "create_github_issue"

    def test_failed_status(self, queue_dir):
        task_id = "1_20260319T100000Z_abc123--def456--chat.message"
        cid = chat_service.create_conversation()
        failed_path = queue_dir / "failed" / f"{task_id}.yaml"
        task_data = {
            "id": task_id,
            "status": "failed",
            "error": "Connection refused",
            "payload": {"type": "chat.message", "conversation_id": cid},
        }
        failed_path.write_text(yaml.dump(task_data))

        resp = client.get(f"/api/chat/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert isinstance(data["messages"], list)
        assert data["messages"][0]["role"] == "system"
        assert "Connection refused" in data["messages"][0]["content"]
        assert data["messages"][0]["type"] == "system"

    def test_404_for_unknown_task(self, queue_dir):
        resp = client.get("/api/chat/tasks/nonexistent")
        assert resp.status_code == 404


class TestRespondToProposal:
    def test_reject_returns_immediate(self):
        cid = chat_service.create_conversation()
        messages = chat_service.receive_structured_reply(
            cid,
            {
                "reply": "Ok.",
                "proposal": {
                    "action": "test_action",
                    "parameters": {},
                    "description": "Do something",
                },
            },
        )
        proposal_id = messages[1].metadata["proposal_id"]
        resp = client.post(
            f"/api/chat/conversations/{cid}/proposals/{proposal_id}",
            json={"option": "reject"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "task_id" not in data

    def test_approve_registered_action_returns_task_id(self, queue_dir):
        from app.chat import ACTION_REGISTRY

        ACTION_REGISTRY["test_action"] = "service.test.action"
        try:
            cid = chat_service.create_conversation()
            messages = chat_service.receive_structured_reply(
                cid,
                {
                    "reply": "Ok.",
                    "proposal": {
                        "action": "test_action",
                        "parameters": {},
                        "description": "Do something",
                    },
                },
            )
            proposal_id = messages[1].metadata["proposal_id"]
            resp = client.post(
                f"/api/chat/conversations/{cid}/proposals/{proposal_id}",
                json={"option": "approve"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "message" in data
            assert "task_id" in data
        finally:
            ACTION_REGISTRY.pop("test_action", None)

    def test_404_for_unknown_conversation(self):
        resp = client.post(
            "/api/chat/conversations/nonexistent/proposals/p1",
            json={"option": "approve"},
        )
        assert resp.status_code == 404

    def test_400_for_invalid_option(self):
        cid = chat_service.create_conversation()
        messages = chat_service.receive_structured_reply(
            cid,
            {
                "reply": "Ok.",
                "proposal": {
                    "action": "test_action",
                    "parameters": {},
                    "description": "Do something",
                },
            },
        )
        proposal_id = messages[1].metadata["proposal_id"]
        resp = client.post(
            f"/api/chat/conversations/{cid}/proposals/{proposal_id}",
            json={"option": "invalid_choice"},
        )
        assert resp.status_code == 400


class TestChatConfig:
    def test_chat_config_defaults(self):
        from app.config import ChatConfig

        cfg = ChatConfig()
        assert cfg.llm == "default"
        assert cfg.system_prompt is None

    def test_app_config_has_chat(self):
        from app.config import config

        assert hasattr(config, "chat")
        assert config.chat.llm == "default"


class TestChatReplyResultRoute:
    def test_chat_reply_handled_without_error(self):
        from app.result_routes import route_results

        result = {"content": "Hello from LLM", "conversation_id": "conv-1"}
        task = {
            "id": "1_20260319T100000Z_abc123--def456--chat.message",
            "payload": {
                "type": "chat.message",
                "on_result": [
                    {"type": "chat_reply", "conversation_id": "conv-1"},
                ],
            },
        }
        # Should not raise
        route_results(result, task)

    def test_chat_reply_logs_human_entry(self):
        from app.result_routes import route_results

        result = {"content": "Hello from LLM", "conversation_id": "conv-1"}
        task = {
            "id": "1_20260319T100000Z_abc123--def456--chat.message",
            "payload": {
                "type": "chat.message",
                "on_result": [
                    {"type": "chat_reply", "conversation_id": "conv-1"},
                ],
            },
        }
        with patch("app.result_routes.log") as mock_log:
            route_results(result, task)
        mock_log.human.assert_called_once()
        call_args = mock_log.human.call_args[0]
        assert "Chat reply" in call_args[0]
