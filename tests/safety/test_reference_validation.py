"""Tests for script and service reference validation.

These functions warn (but don't disable) automations that reference
scripts or services that don't exist. Unlike the safety validation in
test_provenance.py (which disables automations), these produce warnings
only -- but the warnings are the only signal a user gets that their
automation will silently do nothing.
"""

from typing import ClassVar
from unittest.mock import MagicMock, patch

from app.config import (
    AutomationConfig,
    ScriptConfig,
    YoloAction,
    _validate_script_references,
    _validate_service_references,
)
from gaas_sdk.manifest import ServiceManifest


# ---------------------------------------------------------------------------
# Mock infrastructure (same pattern as test_provenance.py)
# ---------------------------------------------------------------------------


class _MockPlatform:
    def __init__(self, automations):
        self.automations = list(automations)
        self.classifications = {}


class _MockPlatforms:
    model_fields: ClassVar[dict] = {"inbox": None}

    def __init__(self, platform):
        self.inbox = platform


class _MockIntegration:
    def __init__(self, name, platforms):
        self.name = name
        self.type = "email"
        self.platforms = platforms


def _make_integration(name, automations):
    platform = _MockPlatform(automations)
    platforms = _MockPlatforms(platform)
    integration = _MockIntegration(name, platforms)
    return integration, platform


# ---------------------------------------------------------------------------
# _validate_script_references
# ---------------------------------------------------------------------------


class TestScriptReferenceValidation:
    def test_undefined_script_warns(self):
        """Reference to undefined script produces warning with name and path."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"script": {"name": "research_tos", "inputs": {"domain": "{{ domain }}"}}}],
            ),
        ]
        integration, _ = _make_integration("personal", automations)
        warnings = _validate_script_references([integration], scripts={})
        assert len(warnings) == 1
        assert "research_tos" in warnings[0]
        assert "personal.inbox" in warnings[0]

    def test_defined_script_no_warning(self):
        """Reference to a defined script produces no warning."""
        scripts = {
            "research_tos": ScriptConfig(shell="echo ok"),
        }
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"script": {"name": "research_tos"}}],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        warnings = _validate_script_references([integration], scripts=scripts)
        assert warnings == []

    def test_yolo_wrapped_undefined_script_still_warns(self):
        """!yolo overrides safety provenance checks, not existence checks."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction({"script": {"name": "nonexistent"}})],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        warnings = _validate_script_references([integration], scripts={})
        assert len(warnings) == 1
        assert "nonexistent" in warnings[0]

    def test_string_form_script_reference(self):
        """Script reference in string form (not dict) is handled correctly."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"script": "my_script"}],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        warnings = _validate_script_references([integration], scripts={})
        assert len(warnings) == 1
        assert "my_script" in warnings[0]

    def test_integration_without_platforms_no_crash(self):
        """Integrations without platforms attribute are skipped safely."""
        integration = MagicMock(spec=[])  # no attributes
        warnings = _validate_script_references([integration], scripts={})
        assert warnings == []


# ---------------------------------------------------------------------------
# _validate_service_references
# ---------------------------------------------------------------------------


class TestServiceReferenceValidation:
    def _make_manifest(self, service_names):
        """Build a mock IntegrationManifest with the given service names."""
        manifest = MagicMock()
        manifest.services = {
            name: ServiceManifest(
                name=name,
                description=f"Test {name}",
                handler=f".services.{name}.handle",
            )
            for name in service_names
        }
        return manifest

    def test_unknown_service_type_warns(self):
        """Service call with no matching manifest type produces warning."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"service": {"call": "nonexistent.default.action"}}],
            ),
        ]
        integration, _ = _make_integration("personal", automations)
        with patch("app.loader.get_manifests", return_value={}):
            warnings = _validate_service_references([integration])
        assert len(warnings) == 1
        assert "nonexistent.default.action" in warnings[0]
        assert "personal.inbox" in warnings[0]

    def test_known_type_unknown_service_warns(self):
        """Service type exists but service name is not in manifest."""
        manifest = self._make_manifest(["web_research"])
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"service": {"call": "gemini.default.typo_service"}}],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        with patch("app.loader.get_manifests", return_value={"gemini": manifest}):
            warnings = _validate_service_references([integration])
        assert len(warnings) == 1
        assert "typo_service" in warnings[0]

    def test_valid_known_service_no_warning(self):
        """Fully valid service reference produces no warning."""
        manifest = self._make_manifest(["web_research"])
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"service": {"call": "gemini.default.web_research"}}],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        with patch("app.loader.get_manifests", return_value={"gemini": manifest}):
            warnings = _validate_service_references([integration])
        assert warnings == []

    def test_yolo_wrapped_unknown_service_still_warns(self):
        """!yolo overrides safety provenance checks, not existence checks."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction({"service": {"call": "missing.default.action"}})],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        with patch("app.loader.get_manifests", return_value={}):
            warnings = _validate_service_references([integration])
        assert len(warnings) == 1
        assert "missing.default.action" in warnings[0]

    def test_malformed_call_format_warns(self):
        """Service call with wrong number of dots produces warning."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"service": {"call": "just_one_part"}}],
            ),
        ]
        integration, _ = _make_integration("test", automations)
        with patch("app.loader.get_manifests", return_value={}):
            warnings = _validate_service_references([integration])
        assert len(warnings) == 1
        assert "just_one_part" in warnings[0]

    def test_integration_without_platforms_no_crash(self):
        """Integrations without platforms attribute are skipped safely."""
        integration = MagicMock(spec=[])  # no attributes
        with patch("app.loader.get_manifests", return_value={}):
            warnings = _validate_service_references([integration])
        assert warnings == []
