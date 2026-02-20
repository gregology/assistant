"""Tests for github.evaluate — the deterministic automation dispatch layer.

All tests operate on PRSnapshot and classification dicts directly.
No filesystem, no LLM, no GitHub API calls.
"""

from app.config import AutomationConfig, ClassificationConfig
from app.integrations.github.evaluate import (
    PRSnapshot,
    _MISSING,
    _conditions_match,
    _evaluate_automations,
    _resolve_value,
)
from app.integrations.conditions import (
    check_condition as _check_condition,
    check_deterministic_condition as _check_deterministic_condition,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CONFIDENCE_CLS = ClassificationConfig(prompt="test confidence")
BOOLEAN_CLS = ClassificationConfig(prompt="test boolean", type="boolean")
ENUM_CLS = ClassificationConfig(
    prompt="test enum", type="enum", values=["low", "medium", "high", "critical"]
)

CLASSIFICATIONS = {
    "complexity": CONFIDENCE_CLS,
    "risk": CONFIDENCE_CLS,
    "documentation_only": CONFIDENCE_CLS,
}


def _pr(
    org="acme",
    repo="api",
    number=42,
    author="alice",
    title="Test PR",
    status="open",
    draft=False,
    additions=10,
    deletions=5,
    changed_files=2,
) -> PRSnapshot:
    return PRSnapshot(
        org=org,
        repo=repo,
        number=number,
        author=author,
        title=title,
        status=status,
        draft=draft,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )


# ---------------------------------------------------------------------------
# _resolve_value
# ---------------------------------------------------------------------------


class TestResolveValue:
    def test_classification_namespace(self):
        pr = _pr()
        result = {"risk": 0.9, "complexity": 0.5}
        assert _resolve_value("classification.risk", pr, result) == 0.9
        assert _resolve_value("classification.complexity", pr, result) == 0.5

    def test_missing_classification_key_returns_sentinel(self):
        pr = _pr()
        assert _resolve_value("classification.nonexistent", pr, {}) is _MISSING

    def test_direct_attribute_org(self):
        pr = _pr(org="myorg")
        assert _resolve_value("org", pr, {}) == "myorg"

    def test_direct_attribute_author(self):
        pr = _pr(author="bob")
        assert _resolve_value("author", pr, {}) == "bob"

    def test_direct_attribute_status(self):
        pr = _pr(status="open")
        assert _resolve_value("status", pr, {}) == "open"

    def test_direct_attribute_draft(self):
        pr = _pr(draft=True)
        assert _resolve_value("draft", pr, {}) is True

    def test_direct_attribute_additions(self):
        pr = _pr(additions=250)
        assert _resolve_value("additions", pr, {}) == 250

    def test_missing_attribute_returns_sentinel(self):
        pr = _pr()
        assert _resolve_value("nonexistent_field", pr, {}) is _MISSING

    def test_no_authentication_namespace(self):
        """GitHub has no authentication namespace — should return _MISSING."""
        pr = _pr()
        assert _resolve_value("authentication.dkim_pass", pr, {}) is _MISSING

    def test_no_calendar_namespace(self):
        """GitHub has no calendar namespace — should return _MISSING."""
        pr = _pr()
        assert _resolve_value("calendar.end", pr, {}) is _MISSING


# ---------------------------------------------------------------------------
# _conditions_match
# ---------------------------------------------------------------------------


class TestConditionsMatch:
    def test_classification_confidence_condition(self):
        pr = _pr()
        result = {"risk": 0.9, "complexity": 0.5, "documentation_only": 0.1}
        when = {"classification.risk": 0.8}
        assert _conditions_match(when, pr, result, CLASSIFICATIONS) is True

    def test_classification_confidence_below_threshold(self):
        pr = _pr()
        result = {"risk": 0.5}
        when = {"classification.risk": 0.8}
        assert _conditions_match(when, pr, result, CLASSIFICATIONS) is False

    def test_classification_string_operator(self):
        pr = _pr()
        result = {"risk": 0.9}
        assert _conditions_match({"classification.risk": "> 0.8"}, pr, result, CLASSIFICATIONS) is True
        assert _conditions_match({"classification.risk": "> 0.95"}, pr, result, CLASSIFICATIONS) is False

    def test_deterministic_draft_true(self):
        pr = _pr(draft=True)
        assert _conditions_match({"draft": True}, pr, {}, CLASSIFICATIONS) is True

    def test_deterministic_draft_false(self):
        pr = _pr(draft=False)
        assert _conditions_match({"draft": False}, pr, {}, CLASSIFICATIONS) is True
        assert _conditions_match({"draft": True}, pr, {}, CLASSIFICATIONS) is False

    def test_deterministic_status(self):
        pr = _pr(status="open")
        assert _conditions_match({"status": "open"}, pr, {}, CLASSIFICATIONS) is True
        assert _conditions_match({"status": "merged"}, pr, {}, CLASSIFICATIONS) is False

    def test_deterministic_author(self):
        pr = _pr(author="dependabot[bot]")
        assert _conditions_match({"author": "dependabot[bot]"}, pr, {}, CLASSIFICATIONS) is True
        assert _conditions_match({"author": "alice"}, pr, {}, CLASSIFICATIONS) is False

    def test_deterministic_author_list(self):
        pr = _pr(author="renovate[bot]")
        bots = ["dependabot[bot]", "renovate[bot]"]
        assert _conditions_match({"author": bots}, pr, {}, CLASSIFICATIONS) is True

    def test_missing_classification_key_returns_false(self):
        pr = _pr()
        result = {"risk": 0.9}
        when = {"classification.nonexistent": 0.5}
        assert _conditions_match(when, pr, result, CLASSIFICATIONS) is False

    def test_empty_when_matches_everything(self):
        pr = _pr()
        assert _conditions_match({}, pr, {}, CLASSIFICATIONS) is True

    def test_all_conditions_must_match(self):
        pr = _pr(draft=False)
        result = {"risk": 0.9}
        when = {"classification.risk": 0.8, "draft": False}
        assert _conditions_match(when, pr, result, CLASSIFICATIONS) is True

        when_fail = {"classification.risk": 0.8, "draft": True}
        assert _conditions_match(when_fail, pr, result, CLASSIFICATIONS) is False

    def test_mixed_deterministic_and_classification(self):
        pr = _pr(status="open", draft=False)
        result = {"risk": 0.9, "complexity": 0.5, "documentation_only": 0.1}
        when = {"status": "open", "draft": False, "classification.risk": "> 0.8"}
        assert _conditions_match(when, pr, result, CLASSIFICATIONS) is True

        pr_draft = _pr(status="open", draft=True)
        assert _conditions_match(when, pr_draft, result, CLASSIFICATIONS) is False

    def test_missing_result_value_returns_false(self):
        pr = _pr()
        when = {"classification.risk": 0.8}
        assert _conditions_match(when, pr, {}, CLASSIFICATIONS) is False


# ---------------------------------------------------------------------------
# _evaluate_automations
# ---------------------------------------------------------------------------


class TestEvaluateAutomations:
    def test_matching_returns_actions(self):
        pr = _pr()
        automations = [
            AutomationConfig(when={"classification.risk": 0.8}, then=["flag_for_review"]),
        ]
        result = {"risk": 0.9, "complexity": 0.5, "documentation_only": 0.1}
        actions = _evaluate_automations(automations, pr, result, CLASSIFICATIONS)
        assert actions == ["flag_for_review"]

    def test_non_matching_returns_empty(self):
        pr = _pr()
        automations = [
            AutomationConfig(when={"classification.risk": 0.8}, then=["flag_for_review"]),
        ]
        result = {"risk": 0.3}
        actions = _evaluate_automations(automations, pr, result, CLASSIFICATIONS)
        assert actions == []

    def test_no_automations_returns_empty(self):
        pr = _pr()
        result = {"risk": 0.9, "complexity": 0.8, "documentation_only": 0.1}
        actions = _evaluate_automations([], pr, result, CLASSIFICATIONS)
        assert actions == []

    def test_multiple_matching_combine_actions(self):
        pr = _pr(draft=False)
        automations = [
            AutomationConfig(when={"classification.risk": 0.5}, then=["action_a"]),
            AutomationConfig(when={"draft": False}, then=["action_b"]),
        ]
        result = {"risk": 0.9, "complexity": 0.5, "documentation_only": 0.1}
        actions = _evaluate_automations(automations, pr, result, CLASSIFICATIONS)
        assert "action_a" in actions
        assert "action_b" in actions

    def test_deterministic_draft_automation(self):
        """Automation that fires only for draft PRs."""
        pr_draft = _pr(draft=True)
        pr_open = _pr(draft=False)
        automations = [
            AutomationConfig(when={"draft": True}, then=["skip"]),
        ]
        assert _evaluate_automations(automations, pr_draft, {}, CLASSIFICATIONS) == ["skip"]
        assert _evaluate_automations(automations, pr_open, {}, CLASSIFICATIONS) == []

    def test_deterministic_author_automation(self):
        """Automation matching specific bot authors."""
        pr_bot = _pr(author="dependabot[bot]")
        pr_human = _pr(author="alice")
        bots = ["dependabot[bot]", "renovate[bot]"]
        automations = [
            AutomationConfig(when={"author": bots}, then=["auto_approve"]),
        ]
        assert _evaluate_automations(automations, pr_bot, {}, CLASSIFICATIONS) == ["auto_approve"]
        assert _evaluate_automations(automations, pr_human, {}, CLASSIFICATIONS) == []

    def test_empty_then_produces_no_actions(self):
        """Automations with empty then lists (Phase 4 placeholders) produce no actions."""
        pr = _pr()
        automations = [
            AutomationConfig(when={"draft": False}, then=[]),
        ]
        actions = _evaluate_automations(automations, pr, {}, CLASSIFICATIONS)
        assert actions == []

    def test_missing_key_never_fires(self):
        """Automation referencing a nonexistent condition key never fires."""
        pr = _pr()
        automations = [
            AutomationConfig(when={"nonexistent_field": True}, then=["some_action"]),
        ]
        actions = _evaluate_automations(automations, pr, {}, CLASSIFICATIONS)
        assert actions == []
