import json

import pytest

import gaas_sdk.runtime
from app.llm import (
    LLMConversation,
    LLMResponse,
    Message,
    MessageList,
    Role,
    SchemaValidationError,
    _validate_schema,
    _wrap_schema,
)


# ---------------------------------------------------------------------------
# MessageList
# ---------------------------------------------------------------------------


class TestMessageList:
    def test_append_and_all(self):
        ml = MessageList()
        ml.append(Message(role=Role.USER, content="hello"))
        ml.append(Message(role=Role.ASSISTANT, content="hi"))
        assert len(ml.all()) == 2

    def test_first_last(self):
        ml = MessageList()
        ml.append(Message(role=Role.USER, content="first"))
        ml.append(Message(role=Role.ASSISTANT, content="last"))
        assert ml.first().content == "first"
        assert ml.last().content == "last"

    def test_empty_returns_none(self):
        ml = MessageList()
        assert ml.first() is None
        assert ml.last() is None
        assert ml.last_user() is None
        assert ml.last_agent() is None

    def test_last_user_and_last_agent(self):
        ml = MessageList()
        ml.append(Message(role=Role.USER, content="u1"))
        ml.append(Message(role=Role.ASSISTANT, content="a1"))
        ml.append(Message(role=Role.USER, content="u2"))
        assert ml.last_user().content == "u2"
        assert ml.last_agent().content == "a1"

    def test_to_api_format(self):
        ml = MessageList()
        ml.append(Message(role=Role.SYSTEM, content="sys"))
        ml.append(Message(role=Role.USER, content="prompt"))
        api = ml.to_api_format()
        assert api == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "prompt"},
        ]

    def test_len_and_iter(self):
        ml = MessageList()
        ml.append(Message(role=Role.USER, content="a"))
        ml.append(Message(role=Role.USER, content="b"))
        assert len(ml) == 2
        contents = [m.content for m in ml]
        assert contents == ["a", "b"]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


SAMPLE_SCHEMA = {
    "properties": {
        "human": {"type": "number"},
        "urgent": {"type": "boolean"},
        "priority": {"type": "string", "enum": ["low", "high"]},
    },
    "required": ["human", "urgent", "priority"],
}


class TestValidateSchema:
    def test_valid_data_returns_no_errors(self):
        data = {"human": 0.9, "urgent": True, "priority": "high"}
        assert _validate_schema(data, SAMPLE_SCHEMA) == []

    def test_missing_required_field(self):
        data = {"human": 0.9, "urgent": True}
        errors = _validate_schema(data, SAMPLE_SCHEMA)
        assert len(errors) > 0
        assert any("priority" in e for e in errors)

    def test_wrong_type(self):
        data = {"human": "not a number", "urgent": True, "priority": "low"}
        errors = _validate_schema(data, SAMPLE_SCHEMA)
        assert len(errors) > 0

    def test_invalid_enum_value(self):
        data = {"human": 0.5, "urgent": False, "priority": "critical"}
        errors = _validate_schema(data, SAMPLE_SCHEMA)
        assert len(errors) > 0


class TestWrapSchema:
    def test_structure(self):
        wrapped = _wrap_schema(SAMPLE_SCHEMA)
        assert wrapped["type"] == "json_schema"
        inner = wrapped["json_schema"]["schema"]
        assert inner["type"] == "object"
        assert "human" in inner["properties"]


# ---------------------------------------------------------------------------
# LLMConversation with fake backend
# ---------------------------------------------------------------------------


class FakeLLMBackend:
    """Returns preconfigured responses in order."""

    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def chat(self, messages, model, parameters=None, response_format=None) -> LLMResponse:
        return LLMResponse(
            content=next(self._responses),
            model=model or "test",
            prompt_tokens=0,
            completion_tokens=0,
            duration_s=0.0,
        )


class TestLLMConversation:
    def _make_conversation(self, responses: list[str]) -> LLMConversation:
        backend = FakeLLMBackend(responses)
        conv = LLMConversation.__new__(LLMConversation)
        conv.model = "test"
        conv._parameters = {}
        conv.messages = MessageList()
        conv._backend = backend
        return conv

    def test_plain_message(self):
        conv = self._make_conversation(["hello back"])
        result = conv.message("hello")
        assert result == "hello back"
        assert len(conv.messages) == 2

    def test_structured_output_valid_on_first_try(self):
        valid = json.dumps({"human": 0.8, "urgent": True, "priority": "low"})
        conv = self._make_conversation([valid])
        result = conv.message("classify", schema=SAMPLE_SCHEMA)
        assert result == {"human": 0.8, "urgent": True, "priority": "low"}

    def test_structured_output_retries_on_invalid_json(self):
        valid = json.dumps({"human": 0.5, "urgent": False, "priority": "high"})
        conv = self._make_conversation(["not json at all", valid])
        result = conv.message("classify", schema=SAMPLE_SCHEMA)
        assert result["human"] == 0.5

    def test_structured_output_retries_on_schema_mismatch(self):
        bad = json.dumps({"human": "wrong type", "urgent": True, "priority": "low"})
        good = json.dumps({"human": 0.9, "urgent": True, "priority": "low"})
        conv = self._make_conversation([bad, good])
        result = conv.message("classify", schema=SAMPLE_SCHEMA)
        assert result["human"] == 0.9

    def test_raises_after_max_retries(self):
        bad_responses = ["garbage"] * 3
        conv = self._make_conversation(bad_responses)
        with pytest.raises(SchemaValidationError) as exc_info:
            conv.message("classify", schema=SAMPLE_SCHEMA)
        assert "3 attempts" in str(exc_info.value)

    def test_dangling_user_message_removed_on_failure(self):
        conv = self._make_conversation(["garbage"] * 3)
        with pytest.raises(SchemaValidationError):
            conv.message("classify", schema=SAMPLE_SCHEMA)
        # The user message should have been removed
        if len(conv.messages) > 0:
            assert conv.messages.last().role != Role.USER

    def test_shared_backend_is_reused(self):
        """Multiple conversations sharing a backend use the same instance."""
        backend = FakeLLMBackend(["resp1", "resp2"])
        conv1 = LLMConversation.__new__(LLMConversation)
        conv1.model = "test"
        conv1._parameters = {}
        conv1.messages = MessageList()
        conv1._backend = backend

        conv2 = LLMConversation.__new__(LLMConversation)
        conv2.model = "test"
        conv2._parameters = {}
        conv2.messages = MessageList()
        conv2._backend = backend

        assert conv1._backend is conv2._backend


# ---------------------------------------------------------------------------
# Shared backend via runtime registration
# ---------------------------------------------------------------------------


class TestSharedBackendViaRuntime:
    def test_runtime_conversations_share_backend(self):
        """Conversations created via runtime.create_llm_conversation share a backend."""
        conv1 = gaas_sdk.runtime.create_llm_conversation(model="default")
        conv2 = gaas_sdk.runtime.create_llm_conversation(model="default")
        assert conv1._backend is conv2._backend
