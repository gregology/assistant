"""Tests for service support: manifest parsing, handler registration,
action detection, and enqueue_actions with service actions."""

import logging
from unittest.mock import patch

import yaml

from app.loader import _load_manifest
from assistant_sdk.actions import is_service_action, enqueue_actions
from assistant_sdk.evaluate import MISSING
from assistant_sdk.manifest import ServiceManifest
from assistant_sdk.models import ServiceAction, SimpleAction


def _make_resolver(**fields):
    def resolve_value(key, classification):
        if key in fields:
            return fields[key]
        return MISSING
    return resolve_value


class TestServiceManifestParsing:
    def test_services_parsed_from_manifest(self, tmp_path):
        """Services section in manifest.yaml is parsed into ServiceManifest objects."""
        integration_dir = tmp_path / "gemini"
        integration_dir.mkdir()
        manifest_yaml = {
            "domain": "gemini",
            "name": "Google Gemini",
            "version": "0.1.0",
            "entry_task": "",
            "dependencies": [],
            "config_schema": {},
            "platforms": {},
            "services": {
                "web_research": {
                    "name": "Web Research",
                    "description": "Grounded web research",
                    "handler": ".services.web_research.handle",
                    "reversible": True,
                    "input_schema": {
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                    },
                },
            },
        }
        (integration_dir / "manifest.yaml").write_text(yaml.dump(manifest_yaml))
        manifest = _load_manifest(integration_dir, builtin=False)

        assert manifest is not None
        assert "web_research" in manifest.services
        svc = manifest.services["web_research"]
        assert isinstance(svc, ServiceManifest)
        assert svc.name == "Web Research"
        assert svc.reversible is True
        assert svc.handler == ".services.web_research.handle"

    def test_no_services_section(self, tmp_path):
        """Manifest without services section has empty services dict."""
        integration_dir = tmp_path / "basic"
        integration_dir.mkdir()
        manifest_yaml = {
            "domain": "basic",
            "name": "Basic",
            "version": "0.1.0",
            "config_schema": {},
            "platforms": {},
        }
        (integration_dir / "manifest.yaml").write_text(yaml.dump(manifest_yaml))
        manifest = _load_manifest(integration_dir, builtin=False)

        assert manifest is not None
        assert manifest.services == {}

    def test_service_defaults(self, tmp_path):
        """ServiceManifest defaults: reversible=False, input_schema={}."""
        integration_dir = tmp_path / "minimal"
        integration_dir.mkdir()
        manifest_yaml = {
            "domain": "minimal",
            "name": "Minimal",
            "version": "0.1.0",
            "config_schema": {},
            "platforms": {},
            "services": {
                "do_thing": {
                    "name": "Do Thing",
                    "description": "Does a thing",
                    "handler": ".do.handle",
                },
            },
        }
        (integration_dir / "manifest.yaml").write_text(yaml.dump(manifest_yaml))
        manifest = _load_manifest(integration_dir, builtin=False)

        svc = manifest.services["do_thing"]
        assert svc.reversible is False
        assert svc.input_schema == {}


class TestIsServiceAction:
    def test_service_action(self):
        assert is_service_action({"service": {"call": "gemini.default.web_research"}}) is True

    def test_script_action(self):
        assert is_service_action({"script": {"name": "test"}}) is False

    def test_string_action(self):
        assert is_service_action("archive") is False


class TestEnqueueServiceActions:
    def test_service_action_enqueued(self):
        """Service actions are enqueued with correct type and integration."""
        resolver = _make_resolver(domain="test.com")
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "research {{ domain }}"},
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock_enqueue.call_count == 1
            payload = mock_enqueue.call_args[0][0]
            assert payload["type"] == "service.gemini.web_research"
            assert payload["integration"] == "gemini.default"
            assert payload["inputs"] == {"prompt": "research test.com"}

    def test_service_input_resolution(self):
        """{{ field }} references in service inputs are resolved."""
        resolver = _make_resolver(domain="test.com")
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "{{ domain }}"},
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            payload = mock_enqueue.call_args[0][0]
            assert payload["inputs"] == {"prompt": "test.com"}

    def test_invalid_service_call_format(self, caplog):
        """Invalid call format (not 3 parts) logs warning and doesn't enqueue."""
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            with caplog.at_level(logging.WARNING):
                enqueue_actions(
                    actions=[ServiceAction(service={"call": "invalid"})],
                    platform_payload={"type": "email.inbox.act", "uid": "123"},
                    resolve_value=resolver,
                    classification={},
                    provenance="rule",
                )
            mock_enqueue.assert_not_called()
            assert "Invalid service call format: 'invalid'" in caplog.text

    def test_service_malformed_call_two_parts(self, caplog):
        """Two-part call format (missing service name) logs warning and doesn't enqueue."""
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            with caplog.at_level(logging.WARNING):
                enqueue_actions(
                    actions=[ServiceAction(service={"call": "only.two_parts"})],
                    platform_payload={"type": "email.inbox.act", "uid": "123"},
                    resolve_value=resolver,
                    classification={},
                    provenance="rule",
                )
            mock_enqueue.assert_not_called()
            assert "Invalid service call format: 'only.two_parts'" in caplog.text

    def test_mixed_service_and_platform(self):
        """Service + platform actions produce separate tasks."""
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[
                    SimpleAction(action="archive"),
                    ServiceAction(service={"call": "gemini.default.web_research", "inputs": {}}),
                ],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            assert mock_enqueue.call_count == 2
            types = [c[0][0]["type"] for c in mock_enqueue.call_args_list]
            assert "service.gemini.web_research" in types
            assert "email.inbox.act" in types

    def test_default_on_result_for_service(self):
        """Service tasks include default on_result routing."""
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "test"},
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            payload = mock_enqueue.call_args[0][0]
            assert payload["on_result"] == [{"type": "note"}]

    def test_custom_on_result_from_action(self):
        """Service action can override on_result routing."""
        resolver = _make_resolver()
        custom_routes = [{"type": "note", "path": "research/"}]
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "test"},
                    "on_result": custom_routes,
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            payload = mock_enqueue.call_args[0][0]
            assert payload["on_result"] == custom_routes


class TestServiceHumanLog:
    def test_human_log_from_config(self):
        """Config-level human_log is resolved and included in payload."""
        resolver = _make_resolver(domain="questrade.com")
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "test"},
                    "human_log": "Privacy Policy update for {{ domain }}",
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            payload = mock_enqueue.call_args[0][0]
            assert payload["human_log"] == "Privacy Policy update for questrade.com"

    def test_human_log_from_manifest_fallback(self):
        """Manifest-level human_log is used when config doesn't specify one."""
        from assistant_sdk import runtime
        runtime.set_service_log_template(
            "service.gemini.web_research",
            "Web research: {{ prompt | truncate(80) }}",
        )
        try:
            resolver = _make_resolver()
            with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
                mock_enqueue.return_value = "task_1"
                enqueue_actions(
                    actions=[ServiceAction(service={
                        "call": "gemini.default.web_research",
                        "inputs": {"prompt": "research something important"},
                    })],
                    platform_payload={"type": "email.inbox.act", "uid": "123"},
                    resolve_value=resolver,
                    classification={},
                    provenance="rule",
                )
                payload = mock_enqueue.call_args[0][0]
                assert payload["human_log"] == "Web research: research something important"
        finally:
            runtime._service_log_templates.pop("service.gemini.web_research", None)

    def test_config_human_log_overrides_manifest(self):
        """Config human_log takes precedence over manifest default."""
        from assistant_sdk import runtime
        runtime.set_service_log_template(
            "service.gemini.web_research",
            "Web research: {{ prompt | truncate(80) }}",
        )
        try:
            resolver = _make_resolver(domain="example.com")
            with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
                mock_enqueue.return_value = "task_1"
                enqueue_actions(
                    actions=[ServiceAction(service={
                        "call": "gemini.default.web_research",
                        "inputs": {"prompt": "test"},
                        "human_log": "Custom: {{ domain }}",
                    })],
                    platform_payload={"type": "email.inbox.act", "uid": "123"},
                    resolve_value=resolver,
                    classification={},
                    provenance="rule",
                )
                payload = mock_enqueue.call_args[0][0]
                assert payload["human_log"] == "Custom: example.com"
        finally:
            runtime._service_log_templates.pop("service.gemini.web_research", None)

    def test_no_human_log_omitted_from_payload(self):
        """When no human_log is configured, the key is absent from payload."""
        resolver = _make_resolver()
        with patch("assistant_sdk.runtime._enqueue") as mock_enqueue:
            mock_enqueue.return_value = "task_1"
            enqueue_actions(
                actions=[ServiceAction(service={
                    "call": "gemini.default.web_research",
                    "inputs": {"prompt": "test"},
                })],
                platform_payload={"type": "email.inbox.act", "uid": "123"},
                resolve_value=resolver,
                classification={},
                provenance="rule",
            )
            payload = mock_enqueue.call_args[0][0]
            assert "human_log" not in payload
