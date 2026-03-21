"""Tests for ChatService and chat_message_handler."""

from unittest.mock import patch, MagicMock

import pytest

from app.chat import ChatService, ChatMessage, chat_message_handler
from app.conversation_store import ConversationStore


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "chats")


@pytest.fixture
def svc(store):
    return ChatService(store=store)


class TestChatServiceCreateConversation:
    def test_returns_hex_id(self, svc):
        cid = svc.create_conversation()
        assert isinstance(cid, str)
        assert len(cid) == 16
        int(cid, 16)  # validates hex

    def test_initializes_empty_history(self, svc):
        cid = svc.create_conversation()
        assert svc.get_history(cid) == []


class TestChatServiceGetHistory:
    def test_returns_messages_in_order(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "hello")
            svc.handle_message(cid, "world")
        history = svc.get_history(cid)
        assert len(history) == 2
        assert history[0].content == "hello"
        assert history[1].content == "world"

    def test_unknown_conversation_raises_keyerror(self, svc):
        with pytest.raises(KeyError):
            svc.get_history("nonexistent")


class TestChatServiceHandleMessage:
    def test_chat_message_stores_user_message_and_enqueues(self, svc, queue_dir):
        cid = svc.create_conversation()
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-123"
            result = svc.handle_message(cid, "Hello LLM")

        assert result["type"] == "chat"
        assert result["task_id"] == "task-123"

        # Verify enqueue was called with priority 1
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        payload = call_args[0][0]
        assert payload["type"] == "chat.message"
        assert payload["conversation_id"] == cid
        assert payload["llm_profile"] == "default"
        assert call_args[1]["priority"] == 1

        # User message stored in history
        history = svc.get_history(cid)
        assert len(history) == 1
        assert history[0].role == "user"
        assert history[0].content == "Hello LLM"
        assert history[0].type == "chat"

    def test_clear_command_returns_command_response(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")
            result = svc.handle_message(cid, "/clear")

        assert result["type"] == "command"
        assert isinstance(result["message"], ChatMessage)
        assert result["message"].content == "Conversation cleared."
        assert result["message"].type == "command"

        # History should be cleared
        assert svc.get_history(cid) == []

    def test_unknown_command_returns_system_message(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.queue"):
            result = svc.handle_message(cid, "/foo")

        assert result["type"] == "command"
        assert "Unknown command" in result["message"].content
        assert "/clear" in result["message"].content
        assert result["message"].type == "command"

    def test_command_parsing_is_case_sensitive(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.queue"):
            result = svc.handle_message(cid, "/CLEAR")
        assert result["type"] == "command"
        assert "Unknown command" in result["message"].content

    def test_unknown_conversation_raises_keyerror(self, svc):
        with pytest.raises(KeyError):
            svc.handle_message("nonexistent", "hello")

    def test_task_payload_structure(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.config") as mock_config, patch("app.chat.queue") as mock_queue:
            mock_config.chat.llm = "default"
            mock_config.chat.system_prompt = "Be helpful."
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")

        payload = mock_queue.enqueue.call_args[0][0]
        assert payload["type"] == "chat.message"
        assert payload["conversation_id"] == cid
        assert payload["llm_profile"] == "default"
        assert isinstance(payload["messages"], list)
        assert payload["on_result"] == [
            {"type": "chat_reply", "conversation_id": cid},
        ]


class TestChatServiceReceiveReply:
    def test_adds_assistant_message_to_history(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_reply(cid, "Hello from LLM")
        assert len(messages) == 1
        msg = messages[0]
        assert msg.role == "assistant"
        assert msg.content == "Hello from LLM"
        assert msg.type == "chat"
        history = svc.get_history(cid)
        assert len(history) == 1
        assert history[0].content == "Hello from LLM"


class TestChatServiceReceiveStructuredReply:
    def test_reply_only(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
            cid,
            {
                "reply": "Just a response.",
                "proposal": None,
            },
        )
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == "Just a response."

    def test_reply_with_proposal(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
            cid,
            {
                "reply": "I'll create that.",
                "proposal": {
                    "action": "create_github_issue",
                    "parameters": {"title": "Test", "repo": "org/repo"},
                    "description": "Create issue: Test",
                },
            },
        )
        assert len(messages) == 2
        assert messages[0].type == "chat"
        assert messages[0].content == "I'll create that."
        assert messages[1].type == "confirmation"
        assert messages[1].role == "system"
        assert messages[1].metadata is not None
        assert messages[1].metadata["proposal_id"]
        assert messages[1].metadata["action"] == "create_github_issue"
        assert messages[1].metadata["status"] == "pending"
        assert len(messages[1].metadata["options"]) >= 2

    def test_proposal_persisted_to_store(self, svc, store):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
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
        found = store.find_proposal(cid, proposal_id)
        assert found is not None
        assert found["metadata"]["action"] == "test_action"


class TestChatServiceHandleProposalResponse:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Isolate ACTION_REGISTRY mutations."""
        from app.chat import ACTION_REGISTRY

        original = dict(ACTION_REGISTRY)
        yield
        ACTION_REGISTRY.clear()
        ACTION_REGISTRY.update(original)

    def test_reject_proposal(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
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
        result = svc.handle_proposal_response(cid, proposal_id, "reject")
        assert result["type"] == "immediate"
        assert "cancel" in result["message"].content.lower()

    def test_approve_unknown_action(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
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
        result = svc.handle_proposal_response(cid, proposal_id, "approve")
        assert result["type"] == "immediate"
        assert "Unknown action" in result["message"].content

    def test_approve_registered_action_enqueues(self, svc):
        from app.chat import ACTION_REGISTRY

        ACTION_REGISTRY["test_action"] = "service.test.action"
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
            cid,
            {
                "reply": "Ok.",
                "proposal": {
                    "action": "test_action",
                    "parameters": {"key": "val"},
                    "description": "Do something",
                },
            },
        )
        proposal_id = messages[1].metadata["proposal_id"]
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-xyz"
            result = svc.handle_proposal_response(cid, proposal_id, "approve")
        assert result["type"] == "task"
        assert result["task_id"] == "task-xyz"
        payload = mock_queue.enqueue.call_args[0][0]
        assert payload["type"] == "service.test.action"
        assert payload["inputs"] == {"key": "val"}

    def test_invalid_option(self, svc):
        cid = svc.create_conversation()
        messages = svc.receive_structured_reply(
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
        with pytest.raises(ValueError, match="Invalid option"):
            svc.handle_proposal_response(cid, proposal_id, "invalid_opt")

    def test_missing_proposal(self, svc):
        cid = svc.create_conversation()
        with pytest.raises(ValueError, match="not found"):
            svc.handle_proposal_response(cid, "nonexistent", "approve")

    def test_missing_conversation(self, svc):
        with pytest.raises(KeyError):
            svc.handle_proposal_response("nonexistent", "p1", "approve")


class TestBuildActionPrompt:
    @pytest.fixture(autouse=True)
    def _clean_metadata(self):
        """Isolate ACTION_METADATA mutations."""
        from app.chat import ACTION_METADATA

        original = dict(ACTION_METADATA)
        ACTION_METADATA.clear()
        yield
        ACTION_METADATA.clear()
        ACTION_METADATA.update(original)

    def test_empty_when_no_actions(self):
        from app.chat import _build_action_prompt

        assert _build_action_prompt() == ""

    def test_includes_action_name_and_description(self):
        from app.chat import _build_action_prompt, ACTION_METADATA

        ACTION_METADATA["service.github.create_issue"] = {
            "description": "Create a GitHub issue",
            "input_schema": {
                "properties": {
                    "repo": {"type": "string", "description": "Repository in org/repo format"},
                    "title": {"type": "string", "description": "Issue title"},
                },
                "required": ["repo", "title"],
            },
        }
        result = _build_action_prompt()
        assert "service.github.create_issue" in result
        assert "Create a GitHub issue" in result
        assert "repo" in result
        assert "(required)" in result
        assert "title" in result
        assert "Issue title" in result

    def test_system_prompt_includes_actions(self, svc):
        from app.chat import ACTION_METADATA

        ACTION_METADATA["service.test.action"] = {
            "description": "Test action",
            "input_schema": {"properties": {"x": {"type": "string"}}, "required": []},
        }
        cid = svc.create_conversation()
        with patch("app.chat.config") as mock_config, patch("app.chat.queue") as mock_queue:
            mock_config.chat.system_prompt = "Be helpful."
            mock_config.chat.llm = "default"
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")
        payload = mock_queue.enqueue.call_args[0][0]
        system_msg = payload["messages"][0]["content"]
        assert "Be helpful." in system_msg
        assert "service.test.action" in system_msg


class TestBuildLLMMessages:
    @pytest.fixture(autouse=True)
    def _no_actions(self):
        """Isolate from globally registered actions."""
        from app.chat import ACTION_METADATA

        original = dict(ACTION_METADATA)
        ACTION_METADATA.clear()
        yield
        ACTION_METADATA.clear()
        ACTION_METADATA.update(original)

    def test_includes_system_prompt_and_chat_messages(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.config") as mock_config, patch("app.chat.queue") as mock_queue:
            mock_config.chat.system_prompt = "Be helpful."
            mock_config.chat.llm = "default"
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")

        # Check the messages in the enqueued payload
        payload = mock_queue.enqueue.call_args[0][0]
        messages = payload["messages"]
        assert messages[0] == {"role": "system", "content": "Be helpful."}
        assert messages[1] == {"role": "user", "content": "Hello"}

    def test_excludes_commands_from_llm_messages(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.config") as mock_config, patch("app.chat.queue") as mock_queue:
            mock_config.chat.system_prompt = None
            mock_config.chat.llm = "default"
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")
            svc.handle_message(cid, "/foo")  # unknown command, stored in history
            svc.handle_message(cid, "World")

        # Third call to enqueue (for "World")
        payload = mock_queue.enqueue.call_args[0][0]
        messages = payload["messages"]
        # Should have Hello and World, not the /foo command
        assert len(messages) == 2
        assert messages[0]["content"] == "Hello"
        assert messages[1]["content"] == "World"

    def test_no_system_prompt(self, svc):
        cid = svc.create_conversation()
        with patch("app.chat.config") as mock_config, patch("app.chat.queue") as mock_queue:
            mock_config.chat.system_prompt = None
            mock_config.chat.llm = "default"
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid, "Hello")

        payload = mock_queue.enqueue.call_args[0][0]
        messages = payload["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


class TestMultipleConversations:
    def test_conversations_are_independent(self, svc):
        cid1 = svc.create_conversation()
        cid2 = svc.create_conversation()
        with patch("app.chat.queue") as mock_queue:
            mock_queue.enqueue.return_value = "task-1"
            svc.handle_message(cid1, "Hello from 1")
            svc.handle_message(cid2, "Hello from 2")

        assert len(svc.get_history(cid1)) == 1
        assert len(svc.get_history(cid2)) == 1
        assert svc.get_history(cid1)[0].content == "Hello from 1"
        assert svc.get_history(cid2)[0].content == "Hello from 2"


class TestChatMessageHandler:
    def _make_task(self, conversation_id: str = "conv-1") -> dict:
        return {
            "id": "1_20260319T100000Z_abc123--def456--chat.message",
            "payload": {
                "type": "chat.message",
                "conversation_id": conversation_id,
                "llm_profile": "default",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        }

    def test_structured_response_with_proposal(self):
        import json

        structured = json.dumps(
            {
                "reply": "I'll create that issue.",
                "proposal": {
                    "action": "create_github_issue",
                    "parameters": {"title": "Test"},
                    "description": "Create issue: Test",
                },
            }
        )
        mock_response = MagicMock()
        mock_response.content = structured

        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.return_value = mock_response
            result = chat_message_handler(self._make_task())

        assert "structured" in result
        assert result["structured"]["reply"] == "I'll create that issue."
        assert result["structured"]["proposal"]["action"] == "create_github_issue"
        assert result["conversation_id"] == "conv-1"

    def test_structured_response_without_proposal(self):
        import json

        structured = json.dumps({"reply": "Just a chat response.", "proposal": None})
        mock_response = MagicMock()
        mock_response.content = structured

        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.return_value = mock_response
            result = chat_message_handler(self._make_task())

        assert "structured" in result
        assert result["structured"]["reply"] == "Just a chat response."
        assert result["structured"]["proposal"] is None

    def test_falls_back_to_plain_text_on_invalid_json(self):
        mock_response = MagicMock()
        mock_response.content = "Plain text, not JSON"

        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.return_value = mock_response
            result = chat_message_handler(self._make_task())

        assert "content" in result
        assert "structured" not in result
        assert result["content"] == "Plain text, not JSON"

    def test_falls_back_when_reply_key_missing(self):
        import json

        mock_response = MagicMock()
        mock_response.content = json.dumps({"something_else": "no reply key"})

        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.return_value = mock_response
            result = chat_message_handler(self._make_task())

        assert "content" in result
        assert "structured" not in result

    def test_passes_response_format_to_backend(self):
        import json

        mock_response = MagicMock()
        mock_response.content = json.dumps({"reply": "Hi", "proposal": None})

        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.return_value = mock_response
            chat_message_handler(self._make_task())

        call_kwargs = MockBackend.return_value.chat.call_args[1]
        assert "response_format" in call_kwargs
        assert call_kwargs["response_format"]["type"] == "json_schema"

    def test_propagates_llm_errors(self):
        with patch("app.chat.ChatCompletionsBackend") as MockBackend:
            MockBackend.return_value.chat.side_effect = ConnectionError("refused")
            with pytest.raises(ConnectionError):
                chat_message_handler(self._make_task())
