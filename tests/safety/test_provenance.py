"""Tests for provenance derivation and safety validation.

The provenance system determines whether an automation's conditions
are deterministic (rule), non-deterministic (llm), or mixed (hybrid).
This is safety-critical because it gates irreversible actions.
"""

from unittest.mock import MagicMock

from app.config import (
    AutomationConfig,
    ScriptConfig,
    SimpleAction,
    YoloAction,
    _validate_automation_safety,
    resolve_provenance,
)
from gaas_email.platforms.inbox.const import DETERMINISTIC_SOURCES


# ---------------------------------------------------------------------------
# resolve_provenance
# ---------------------------------------------------------------------------


class TestResolveProvenance:
    def test_pure_deterministic_single(self):
        assert resolve_provenance({"domain": "x.com"}, DETERMINISTIC_SOURCES) == "rule"
        assert resolve_provenance({"authentication.dkim_pass": True}, DETERMINISTIC_SOURCES) == "rule"
        assert resolve_provenance({"is_noreply": True}, DETERMINISTIC_SOURCES) == "rule"
        assert resolve_provenance({"from_address": "x@y.com"}, DETERMINISTIC_SOURCES) == "rule"

    def test_pure_deterministic_multiple(self):
        when = {
            "authentication.dkim_pass": True,
            "authentication.spf_pass": True,
            "domain": "work.com",
        }
        assert resolve_provenance(when, DETERMINISTIC_SOURCES) == "rule"

    def test_pure_nondeterministic_single(self):
        assert resolve_provenance({"classification.human": 0.8}, DETERMINISTIC_SOURCES) == "llm"

    def test_pure_nondeterministic_multiple(self):
        when = {
            "classification.human": 0.8,
            "classification.requires_response": True,
        }
        assert resolve_provenance(when, DETERMINISTIC_SOURCES) == "llm"

    def test_hybrid(self):
        when = {
            "authentication.dkim_pass": True,
            "classification.human": "> 0.8",
        }
        assert resolve_provenance(when, DETERMINISTIC_SOURCES) == "hybrid"

    def test_empty_when_is_rule(self):
        assert resolve_provenance({}, DETERMINISTIC_SOURCES) == "rule"

    def test_all_deterministic_sources_recognized(self):
        for source in DETERMINISTIC_SOURCES:
            when = {source: "test_value"}
            assert resolve_provenance(when, DETERMINISTIC_SOURCES) == "rule", (
                f"{source} should be recognized as deterministic"
            )


# ---------------------------------------------------------------------------
# Safety validation
# ---------------------------------------------------------------------------


class _MockPlatform:
    def __init__(self, automations):
        self.automations = list(automations)
        self.classifications = {}


class _MockPlatforms:
    model_fields = {"inbox": None}

    def __init__(self, platform):
        self.inbox = platform


class _MockIntegration:
    def __init__(self, name, platforms):
        self.name = name
        self.type = "email"
        self.platforms = platforms


def _make_integration(name, automations):
    """Create a mock integration with platform-level automations.

    _validate_automation_safety iterates integrations → platforms → automations.
    Returns (integration, platform) so tests can assert on platform.automations.
    """
    platform = _MockPlatform(automations)
    platforms = _MockPlatforms(platform)
    integration = _MockIntegration(name, platforms)
    return integration, platform


class TestSafetyValidation:
    def test_deterministic_irreversible_allowed(self):
        """Irreversible action from deterministic provenance loads fine."""
        automations = [
            AutomationConfig(
                when={"is_noreply": True, "domain": "spam.com"},
                then=["unsubscribe"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_nondeterministic_irreversible_blocked(self):
        """Irreversible action from LLM provenance is blocked."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=["unsubscribe"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert "unsubscribe" in warnings[0]
        assert "disabled" in warnings[0]
        assert len(platform.automations) == 0

    def test_hybrid_irreversible_blocked(self):
        """Hybrid provenance treated as non-deterministic for safety."""
        automations = [
            AutomationConfig(
                when={"authentication.dkim_pass": True, "classification.human": 0.9},
                then=["unsubscribe"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert len(platform.automations) == 0

    def test_yolo_overrides_safety_block(self):
        """!yolo tag allows irreversible action from non-deterministic provenance."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction("unsubscribe")],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_reversible_action_always_allowed(self):
        """Reversible actions are never blocked regardless of provenance."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=["archive"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_only_unsafe_automations_blocked(self):
        """Only automations with irreversible non-deterministic actions are blocked."""
        automations = [
            AutomationConfig(
                when={"classification.human": 0.8},
                then=["archive"],
            ),
            AutomationConfig(
                when={"classification.human": "> 0.9"},
                then=["unsubscribe"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert len(platform.automations) == 1
        assert platform.automations[0].then == [SimpleAction(action="archive")]

    def test_mixed_actions_with_one_irreversible(self):
        """An automation with both reversible and irreversible actions is blocked
        if the irreversible action is not yolo-tagged."""
        automations = [
            AutomationConfig(
                when={"classification.human": 0.8},
                then=["archive", "unsubscribe"],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert len(platform.automations) == 0

    def test_integration_without_platforms_ignored(self):
        """Integrations without platforms attribute are skipped."""
        integration = MagicMock(spec=[])  # no attributes
        warnings = _validate_automation_safety([integration])
        assert warnings == []

    def test_warning_message_includes_details(self):
        """Warning message contains integration name, platform, action, and provenance."""
        automations = [
            AutomationConfig(
                when={"classification.robot": "> 0.9"},
                then=["unsubscribe"],
            ),
        ]
        integration, _platform = _make_integration("personal", automations)
        warnings = _validate_automation_safety([integration])
        assert "personal" in warnings[0]
        assert "unsubscribe" in warnings[0]
        assert "llm" in warnings[0]
        assert "!yolo" in warnings[0]

    def test_script_from_llm_provenance_blocked(self):
        """Script action without !yolo + LLM provenance is blocked."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"script": {"name": "research_tos", "inputs": {"domain": "{{ domain }}"}}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert "script:research_tos" in warnings[0]
        assert len(platform.automations) == 0

    def test_script_with_yolo_from_llm_provenance_allowed(self):
        """YoloAction wrapping a script action + LLM provenance is allowed."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction({"script": {"name": "research_tos", "inputs": {"domain": "{{ domain }}"}}})],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_script_from_deterministic_provenance_allowed(self):
        """Script action from rule provenance is allowed without !yolo."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"script": {"name": "research_tos", "inputs": {"domain": "{{ domain }}"}}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_reversible_script_from_llm_allowed(self):
        """Script with reversible=True + LLM provenance is allowed without !yolo."""
        scripts = {
            "safe_script": ScriptConfig(shell="echo safe", reversible=True),
        }
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"script": {"name": "safe_script"}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration], scripts=scripts)
        assert warnings == []
        assert len(platform.automations) == 1

    def test_dict_action_irreversible_from_llm_blocked(self):
        """Irreversible DictAction from LLM provenance is blocked."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"unsubscribe": True}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert "unsubscribe" in warnings[0]
        assert "disabled" in warnings[0]
        assert len(platform.automations) == 0

    def test_dict_action_irreversible_from_rule_allowed(self):
        """Irreversible DictAction from deterministic provenance loads fine."""
        automations = [
            AutomationConfig(
                when={"domain": "spam.com"},
                then=[{"unsubscribe": True}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_dict_action_reversible_from_llm_allowed(self):
        """Reversible DictAction from LLM provenance is allowed."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"draft_reply": "I'll get back to you."}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_dict_action_irreversible_yolo_from_llm_allowed(self):
        """YoloAction wrapping irreversible DictAction + LLM provenance is allowed."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction({"unsubscribe": True})],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_service_from_llm_provenance_blocked(self):
        """Non-reversible service action + LLM provenance is blocked."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"service": {"call": "unknown.default.action"}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert len(warnings) == 1
        assert "service:" in warnings[0]
        assert len(platform.automations) == 0

    def test_service_with_yolo_from_llm_allowed(self):
        """YoloAction wrapping a service action + LLM provenance is allowed."""
        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[YoloAction({"service": {"call": "gemini.default.web_research"}})],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_service_from_deterministic_provenance_allowed(self):
        """Service action from rule provenance is allowed without !yolo."""
        automations = [
            AutomationConfig(
                when={"domain": "example.com"},
                then=[{"service": {"call": "gemini.default.web_research"}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1

    def test_reversible_service_from_llm_allowed(self):
        """Service with reversible=True + LLM provenance is allowed without !yolo.

        Only local-only, read-only services should declare reversible=True.
        Services that transmit data externally (like web research) are
        irreversible even if they don't modify local state.
        """
        from gaas_sdk.manifest import ServiceManifest
        from unittest.mock import patch

        # Hypothetical local-only service (e.g., local file search)
        svc = ServiceManifest(
            name="Local Search",
            description="Search local notes",
            handler=".services.local_search.handle",
            reversible=True,
        )

        mock_manifest = MagicMock()
        mock_manifest.services = {"local_search": svc}

        automations = [
            AutomationConfig(
                when={"classification.human": "> 0.8"},
                then=[{"service": {"call": "tools.default.local_search"}}],
            ),
        ]
        integration, platform = _make_integration("test", automations)
        with patch("app.loader.get_manifests", return_value={"tools": mock_manifest}):
            warnings = _validate_automation_safety([integration])
        assert warnings == []
        assert len(platform.automations) == 1
