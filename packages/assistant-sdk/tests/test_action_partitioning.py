"""Tests for assistant_sdk.actions — action detection, input resolution, and partitioning."""

from unittest.mock import patch

from assistant_sdk.actions import (
    enqueue_actions,
    is_script_action,
    is_service_action,
    resolve_inputs,
)
from assistant_sdk.evaluate import MISSING
from assistant_sdk.models import ScriptAction, ServiceAction, SimpleAction


def _make_resolver(**fields):
    def resolve_value(key, classification):
        if key in fields:
            return fields[key]
        return MISSING
    return resolve_value


# ---------------------------------------------------------------------------
# is_script_action / is_service_action
# ---------------------------------------------------------------------------


class TestIsScriptAction:
    def test_script_dict(self):
        assert is_script_action({"script": {"name": "test"}}) is True

    def test_string(self):
        assert is_script_action("archive") is False

    def test_other_dict(self):
        assert is_script_action({"draft_reply": "hi"}) is False

    def test_none(self):
        assert is_script_action(None) is False


class TestIsServiceAction:
    def test_service_dict(self):
        assert is_service_action({"service": {"call": "gemini.research.web_search"}}) is True

    def test_string(self):
        assert is_service_action("archive") is False

    def test_script_dict(self):
        assert is_service_action({"script": {"name": "test"}}) is False

    def test_none(self):
        assert is_service_action(None) is False


# ---------------------------------------------------------------------------
# resolve_inputs
# ---------------------------------------------------------------------------


class TestResolveScriptInputs:
    def test_field_resolution(self):
        resolver = _make_resolver(domain="example.com")
        result = resolve_inputs({"d": "{{ domain }}"}, resolver, {})
        assert result == {"d": "example.com"}

    def test_literal_passthrough(self):
        resolver = _make_resolver()
        result = resolve_inputs({"k": "literal"}, resolver, {})
        assert result == {"k": "literal"}

    def test_missing_field_empty_string(self):
        resolver = _make_resolver()
        result = resolve_inputs({"m": "{{ missing }}"}, resolver, {})
        assert result == {"m": ""}

    def test_non_string_converted(self):
        resolver = _make_resolver(count=42)
        result = resolve_inputs({"c": "{{ count }}"}, resolver, {})
        assert result == {"c": "42"}

    def test_none_value_becomes_empty(self):
        resolver = _make_resolver()
        result = resolve_inputs({"n": None}, resolver, {})
        assert result == {"n": ""}

    def test_classification_field(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"s": "{{ classification.score }}"}, resolver, {"score": 0.9},
        )
        assert result == {"s": "0.9"}


# ---------------------------------------------------------------------------
# enqueue_actions
# ---------------------------------------------------------------------------


class TestEnqueueActions:
    def test_platform_only(self):
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock:
            mock.return_value = "t1"
            enqueue_actions(
                actions=[SimpleAction(action="archive"), SimpleAction(action="log")],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock.call_count == 1
            payload = mock.call_args[0][0]
            assert payload["type"] == "test.act"
            assert payload["actions"] == ["archive", "log"]

    def test_script_only(self):
        resolver = _make_resolver(domain="test.com")
        with patch("assistant_sdk.runtime._enqueue") as mock:
            mock.return_value = "t1"
            enqueue_actions(
                actions=[ScriptAction(
                    script={"name": "research", "inputs": {"d": "{{ domain }}"}}
                )],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock.call_count == 1
            payload = mock.call_args[0][0]
            assert payload["type"] == "script.run"
            assert payload["script_name"] == "research"
            assert payload["inputs"] == {"d": "test.com"}

    def test_service_action(self):
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock:
            mock.return_value = "t1"
            enqueue_actions(
                actions=[ServiceAction(
                    service={"call": "gemini.research.web_search", "inputs": {}}
                )],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock.call_count == 1
            payload = mock.call_args[0][0]
            assert payload["type"] == "service.gemini.web_search"
            assert payload["integration"] == "gemini.research"

    def test_mixed_actions(self):
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock:
            mock.return_value = "t1"
            enqueue_actions(
                actions=[
                    SimpleAction(action="archive"),
                    ScriptAction(script={"name": "s1"}),
                    ServiceAction(service={"call": "a.b.c", "inputs": {}}),
                ],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock.call_count == 3

    def test_empty_actions(self):
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock:
            enqueue_actions(
                actions=[],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            mock.assert_not_called()

    def test_provenance_passed_through(self):
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock:
            mock.return_value = "t1"
            enqueue_actions(
                actions=[SimpleAction(action="log")],
                platform_payload={"type": "test.act", "id": "1"},
                resolve_value=resolver,
                classification={},
                provenance="llm",
                priority=9,
            )
            kwargs = mock.call_args[1]
            assert kwargs["provenance"] == "llm"
            assert kwargs["priority"] == 9
