"""Tests for shared action partitioning and input resolution."""

from unittest.mock import patch

from app.actions import enqueue_actions, is_script_action, resolve_inputs
from gaas_sdk.actions import _render_template
from app.evaluate import MISSING


def _make_resolver(**fields):
    """Create a resolve_value callable that returns fields by name."""
    def resolve_value(key, classification):
        if key in fields:
            return fields[key]
        return MISSING
    return resolve_value


class TestIsScriptAction:
    def test_script_dict(self):
        assert is_script_action({"script": {"name": "test"}}) is True

    def test_string_action(self):
        assert is_script_action("archive") is False

    def test_other_dict(self):
        assert is_script_action({"draft_reply": "hello"}) is False

    def test_none(self):
        assert is_script_action(None) is False


class TestResolveScriptInputs:
    def test_field_resolution(self):
        resolver = _make_resolver(domain="example.com", author="alice")
        result = resolve_inputs(
            {"domain": "{{ domain }}", "author": "{{ author }}"},
            resolver,
            {},
        )
        assert result == {"domain": "example.com", "author": "alice"}

    def test_literal_passthrough(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"key": "literal_value"},
            resolver,
            {},
        )
        assert result == {"key": "literal_value"}

    def test_missing_field_empty_string(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"missing": "{{ nonexistent }}"},
            resolver,
            {},
        )
        assert result == {"missing": ""}

    def test_mixed_inputs(self):
        resolver = _make_resolver(domain="test.com")
        result = resolve_inputs(
            {"domain": "{{ domain }}", "mode": "full"},
            resolver,
            {},
        )
        assert result == {"domain": "test.com", "mode": "full"}

    def test_non_string_value_converted(self):
        resolver = _make_resolver(count=42)
        result = resolve_inputs(
            {"count": "{{ count }}"},
            resolver,
            {},
        )
        assert result == {"count": "42"}

    def test_embedded_field_in_string(self):
        resolver = _make_resolver(domain="questrade.com")
        result = resolve_inputs(
            {"prompt": "research {{ domain }} privacy policy"},
            resolver,
            {},
        )
        assert result == {"prompt": "research questrade.com privacy policy"}

    def test_multiple_embedded_fields(self):
        resolver = _make_resolver(domain="example.com", author="alice")
        result = resolve_inputs(
            {"prompt": "{{ author }} asked about {{ domain }} policy"},
            resolver,
            {},
        )
        assert result == {"prompt": "alice asked about example.com policy"}

    def test_embedded_missing_field_empty_string(self):
        resolver = _make_resolver(domain="example.com")
        result = resolve_inputs(
            {"prompt": "research {{ domain }} by {{ unknown }}"},
            resolver,
            {},
        )
        assert result == {"prompt": "research example.com by "}

    def test_no_template_markers_passthrough(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"prompt": "no references here"},
            resolver,
            {},
        )
        assert result == {"prompt": "no references here"}

    def test_jinja2_filter(self):
        resolver = _make_resolver(domain="example.com")
        result = resolve_inputs(
            {"prompt": "{{ domain | upper }}"},
            resolver,
            {},
        )
        assert result == {"prompt": "EXAMPLE.COM"}

    def test_jinja2_conditional(self):
        resolver = _make_resolver(domain="example.com")
        result = resolve_inputs(
            {"prompt": "{% if domain %}{{ domain }}{% else %}unknown{% endif %}"},
            resolver,
            {},
        )
        assert result == {"prompt": "example.com"}

    def test_jinja2_conditional_missing(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"prompt": "{% if domain %}{{ domain }}{% else %}unknown{% endif %}"},
            resolver,
            {},
        )
        assert result == {"prompt": "unknown"}

    def test_sandbox_blocks_dunder_access(self):
        resolver = _make_resolver(x="hello")
        result = resolve_inputs(
            {"prompt": "{{ x.__class__ }}"},
            resolver,
            {},
        )
        # SandboxedEnvironment blocks __class__ access; ChainableUndefined
        # renders it as empty string rather than raising.
        assert result == {"prompt": ""}

    def test_classification_dot_access(self):
        resolver = _make_resolver()
        result = resolve_inputs(
            {"score": "{{ classification.human }}"},
            resolver,
            {"human": 0.85},
        )
        assert result == {"score": "0.85"}


class TestRenderTemplate:
    def test_plain_string_passthrough(self):
        resolver = _make_resolver()
        result = _render_template("no templates here", resolver, {})
        assert result == "no templates here"

    def test_resolver_variable(self):
        resolver = _make_resolver(domain="example.com")
        result = _render_template("Update for {{ domain }}", resolver, {})
        assert result == "Update for example.com"

    def test_extra_context(self):
        resolver = _make_resolver()
        result = _render_template(
            "Research: {{ prompt | truncate(80) }}",
            resolver, {},
            extra={"prompt": "a very long research prompt"},
        )
        assert result == "Research: a very long research prompt"

    def test_extra_overrides_resolver(self):
        resolver = _make_resolver(prompt="from resolver")
        result = _render_template(
            "{{ prompt }}", resolver, {},
            extra={"prompt": "from extra"},
        )
        assert result == "from extra"


class TestEnqueueActions:
    def test_platform_only(self, queue_dir):
        """Platform-only actions produce a single platform task, no script tasks."""
        resolver = _make_resolver()
        with patch("gaas_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=["archive", "spam"],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock_enqueue.call_count == 1
            call_args = mock_enqueue.call_args
            assert call_args[0][0]["type"] == "email.inbox.act"
            assert call_args[0][0]["actions"] == ["archive", "spam"]

    def test_script_only(self, queue_dir):
        """Script-only actions produce script tasks, no platform task."""
        resolver = _make_resolver(domain="test.com")
        with patch("gaas_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[{"script": {"name": "research", "inputs": {"domain": "{{ domain }}"}}}],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock_enqueue.call_count == 1
            call_args = mock_enqueue.call_args
            assert call_args[0][0]["type"] == "script.run"
            assert call_args[0][0]["script_name"] == "research"
            assert call_args[0][0]["inputs"] == {"domain": "test.com"}

    def test_mixed_actions(self, queue_dir):
        """Mixed actions produce both script and platform tasks."""
        resolver = _make_resolver(domain="test.com")
        with patch("gaas_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[
                    "archive",
                    {"script": {"name": "research", "inputs": {"domain": "{{ domain }}"}}},
                ],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock_enqueue.call_count == 2
            # First call is script.run, second is platform act
            script_call = mock_enqueue.call_args_list[0]
            platform_call = mock_enqueue.call_args_list[1]
            assert script_call[0][0]["type"] == "script.run"
            assert platform_call[0][0]["type"] == "email.inbox.act"

    def test_empty_actions(self, queue_dir):
        """Empty actions produce no tasks."""
        resolver = _make_resolver()
        with patch("gaas_sdk.runtime._enqueue") as mock_enqueue:
            enqueue_actions(
                actions=[],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            mock_enqueue.assert_not_called()

    def test_provenance_passed_through(self, queue_dir):
        """Provenance is passed to all enqueued tasks."""
        resolver = _make_resolver()
        with patch("gaas_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=["archive"],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="llm",
                priority=7,
            )
            call_kwargs = mock_enqueue.call_args
            assert call_kwargs[1]["provenance"] == "llm"
            assert call_kwargs[1]["priority"] == 7
