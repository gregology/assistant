"""Tests for gaas_sdk.runtime — registration pattern and RuntimeNotRegistered."""

import pytest

from gaas_sdk import runtime
from gaas_sdk.runtime import RuntimeNotRegistered


class TestRuntimeNotRegistered:
    def test_error_message_includes_function_name(self):
        err = RuntimeNotRegistered("enqueue")
        assert "enqueue" in str(err)
        assert "register()" in str(err)


class TestRuntimeBeforeRegistration:
    """All runtime functions raise RuntimeNotRegistered before register() is called."""

    def setup_method(self):
        # Save original state
        self._saved = {
            "_enqueue": runtime._enqueue,
            "_get_integration": runtime._get_integration,
            "_get_platform": runtime._get_platform,
            "_create_llm_conversation": runtime._create_llm_conversation,
            "_get_llm_config": runtime._get_llm_config,
            "_get_notes_dir": runtime._get_notes_dir,
        }
        # Reset to unregistered state
        runtime._enqueue = None
        runtime._get_integration = None
        runtime._get_platform = None
        runtime._create_llm_conversation = None
        runtime._get_llm_config = None
        runtime._get_notes_dir = None

    def teardown_method(self):
        # Restore original state
        for attr, val in self._saved.items():
            setattr(runtime, attr, val)

    def test_enqueue_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="enqueue"):
            runtime.enqueue({"type": "test"})

    def test_get_integration_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="get_integration"):
            runtime.get_integration("test.test")

    def test_get_platform_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="get_platform"):
            runtime.get_platform("test.test", "inbox")

    def test_create_llm_conversation_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="create_llm_conversation"):
            runtime.create_llm_conversation()

    def test_get_llm_config_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="get_llm_config"):
            runtime.get_llm_config()

    def test_get_notes_dir_raises(self):
        with pytest.raises(RuntimeNotRegistered, match="get_notes_dir"):
            runtime.get_notes_dir()


class TestRuntimeRegistration:
    def setup_method(self):
        self._saved = {
            "_enqueue": runtime._enqueue,
            "_get_integration": runtime._get_integration,
            "_get_platform": runtime._get_platform,
            "_create_llm_conversation": runtime._create_llm_conversation,
            "_get_llm_config": runtime._get_llm_config,
            "_get_notes_dir": runtime._get_notes_dir,
        }

    def teardown_method(self):
        for attr, val in self._saved.items():
            setattr(runtime, attr, val)

    def test_register_and_call(self):
        calls = []

        runtime.register(
            enqueue=lambda payload, priority=5, provenance=None: calls.append("enqueue") or "id_1",
            get_integration=lambda _iid: calls.append("get_integration"),
            get_platform=lambda _iid, _pn: calls.append("get_platform"),
            create_llm_conversation=lambda model="default", system=None: calls.append("create_llm"),
            get_llm_config=lambda profile="default": calls.append("get_llm_config"),
            get_notes_dir=lambda: calls.append("get_notes_dir"),
        )

        result = runtime.enqueue({"type": "test"})
        assert result == "id_1"
        assert "enqueue" in calls

        runtime.get_integration("test.test")
        assert "get_integration" in calls

        runtime.get_platform("test.test", "inbox")
        assert "get_platform" in calls

        runtime.create_llm_conversation()
        assert "create_llm" in calls

        runtime.get_llm_config()
        assert "get_llm_config" in calls

        runtime.get_notes_dir()
        assert "get_notes_dir" in calls
