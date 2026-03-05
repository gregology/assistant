"""Tests for GitHub evaluate modules — snapshot construction and resolver patterns.

Tests focus on the deterministic dispatch boundary: snapshot construction from
frontmatter, value resolution, and automation evaluation. The LLM is not
involved — these test the decision boundary per the testing philosophy.
"""

from gaas_sdk.evaluate import MISSING, evaluate_automations
from gaas_sdk.models import AutomationConfig, SimpleAction
from gaas_github.platforms.pull_requests.evaluate import (
    PRSnapshot,
    _make_resolver as pr_make_resolver,
    _snapshot_from_frontmatter as pr_snapshot_from_fm,
)
from gaas_github.platforms.issues.evaluate import (
    IssueSnapshot,
    _make_resolver as issue_make_resolver,
    _snapshot_from_frontmatter as issue_snapshot_from_fm,
)
from gaas_github.platforms.pull_requests.const import (
    DEFAULT_CLASSIFICATIONS as PR_CLASSIFICATIONS,
    DETERMINISTIC_SOURCES as PR_DETERMINISTIC,
)
from gaas_github.platforms.issues.const import (
    DEFAULT_CLASSIFICATIONS as ISSUE_CLASSIFICATIONS,
    DETERMINISTIC_SOURCES as ISSUE_DETERMINISTIC,
)


# ---------------------------------------------------------------------------
# PRSnapshot
# ---------------------------------------------------------------------------


class TestPRSnapshot:
    def test_from_frontmatter(self):
        meta = {
            "org": "myorg",
            "repo": "myrepo",
            "number": 42,
            "author": "alice",
            "title": "Add feature",
            "status": "open",
            "additions": 50,
            "deletions": 10,
            "changed_files": 3,
        }
        snap = pr_snapshot_from_fm(meta)
        assert snap.org == "myorg"
        assert snap.repo == "myrepo"
        assert snap.number == 42
        assert snap.author == "alice"
        assert snap.title == "Add feature"
        assert snap.status == "open"
        assert snap.additions == 50
        assert snap.deletions == 10
        assert snap.changed_files == 3

    def test_defaults_for_missing_keys(self):
        snap = pr_snapshot_from_fm({})
        assert snap.org == ""
        assert snap.number == 0
        assert snap.status == "open"
        assert snap.additions == 0

    def test_int_coercion(self):
        meta = {"number": "42", "additions": "10", "deletions": "5", "changed_files": "2"}
        snap = pr_snapshot_from_fm(meta)
        assert snap.number == 42
        assert snap.additions == 10


class TestPRResolver:
    def test_resolves_snapshot_fields(self):
        snap = PRSnapshot(
            org="myorg", repo="myrepo", number=42, author="alice",
            title="Feature", status="open", additions=50, deletions=10,
            changed_files=3,
        )
        resolver = pr_make_resolver(snap)
        assert resolver("org", {}) == "myorg"
        assert resolver("author", {}) == "alice"
        assert resolver("additions", {}) == 50

    def test_resolves_classification_keys(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        classification = {"complexity": 0.8, "risk": 0.2}
        assert resolver("classification.complexity", classification) == 0.8
        assert resolver("classification.risk", classification) == 0.2

    def test_missing_classification_returns_missing(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        assert resolver("classification.nonexistent", {}) is MISSING

    def test_missing_snapshot_field_returns_missing(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        assert resolver("nonexistent_field", {}) is MISSING


# ---------------------------------------------------------------------------
# IssueSnapshot
# ---------------------------------------------------------------------------


class TestIssueSnapshot:
    def test_from_frontmatter(self):
        meta = {
            "org": "myorg",
            "repo": "myrepo",
            "number": 10,
            "author": "bob",
            "title": "Bug report",
            "state": "open",
            "labels": ["bug", "urgent"],
            "comment_count": 5,
        }
        snap = issue_snapshot_from_fm(meta)
        assert snap.org == "myorg"
        assert snap.number == 10
        assert snap.state == "open"
        assert snap.labels == ["bug", "urgent"]
        assert snap.comment_count == 5

    def test_defaults_for_missing_keys(self):
        snap = issue_snapshot_from_fm({})
        assert snap.org == ""
        assert snap.state == "open"
        assert snap.labels == []
        assert snap.comment_count == 0


class TestIssueResolver:
    def test_resolves_snapshot_fields(self):
        snap = IssueSnapshot(
            org="myorg", repo="myrepo", number=10, author="bob",
            title="Bug", state="open", labels=["bug"], comment_count=5,
        )
        resolver = issue_make_resolver(snap)
        assert resolver("org", {}) == "myorg"
        assert resolver("state", {}) == "open"
        assert resolver("labels", {}) == ["bug"]
        assert resolver("comment_count", {}) == 5

    def test_resolves_classification_keys(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=[], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        classification = {"urgency": 0.9, "actionable": True}
        assert resolver("classification.urgency", classification) == 0.9
        assert resolver("classification.actionable", classification) is True


# ---------------------------------------------------------------------------
# PR automation evaluation
# ---------------------------------------------------------------------------


class TestPRAutomationEvaluation:
    def test_deterministic_author_match(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="dependabot",
            title="Bump", status="open", additions=5, deletions=2,
            changed_files=1,
        )
        resolver = pr_make_resolver(snap)
        autos = [
            AutomationConfig(when={"author": "dependabot"}, then=["log"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, PR_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="log")]

    def test_deterministic_author_miss(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="alice",
            title="Feature", status="open", additions=100, deletions=50,
            changed_files=10,
        )
        resolver = pr_make_resolver(snap)
        autos = [
            AutomationConfig(when={"author": "dependabot"}, then=["log"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, PR_CLASSIFICATIONS)
        assert actions == []

    def test_classification_match(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="alice",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        classification = {"complexity": 0.9, "risk": 0.1, "documentation_only": False}
        autos = [
            AutomationConfig(
                when={"classification.complexity": ">0.8"},
                then=["needs_review"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, PR_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="needs_review")]

    def test_missing_classification_safe_default(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        autos = [
            AutomationConfig(
                when={"classification.nonexistent": True},
                then=["dangerous_action"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, {}, PR_CLASSIFICATIONS)
        assert actions == []

    def test_documentation_only_boolean(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        resolver = pr_make_resolver(snap)
        classification = {"complexity": 0.1, "risk": 0.0, "documentation_only": True}
        autos = [
            AutomationConfig(
                when={"classification.documentation_only": True},
                then=["auto_approve"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, PR_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="auto_approve")]

    def test_mixed_deterministic_and_classification(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="dependabot",
            title="Bump dep", status="open", additions=2, deletions=2,
            changed_files=1,
        )
        resolver = pr_make_resolver(snap)
        classification = {"complexity": 0.1, "risk": 0.1, "documentation_only": False}
        autos = [
            AutomationConfig(
                when={"author": "dependabot", "classification.risk": "<0.3"},
                then=["auto_merge"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, PR_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="auto_merge")]


# ---------------------------------------------------------------------------
# Issue automation evaluation
# ---------------------------------------------------------------------------


class TestIssueAutomationEvaluation:
    def test_label_match_scalar_in_list(self):
        """check_deterministic_condition treats list condition as any-of for scalars."""
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=["bug"], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        # Use a scalar deterministic field (e.g., author) with list condition
        autos = [
            AutomationConfig(
                when={"author": ["a", "b"]},
                then=["triage"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, {}, ISSUE_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="triage")]

    def test_state_match(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=["bug"], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        autos = [
            AutomationConfig(when={"state": "open"}, then=["process"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, ISSUE_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="process")]

    def test_urgency_classification(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=[], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        classification = {"urgency": 0.95, "actionable": True}
        autos = [
            AutomationConfig(
                when={"classification.urgency": ">0.9"},
                then=["alert"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, ISSUE_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="alert")]

    def test_actionable_boolean(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=[], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        classification = {"urgency": 0.5, "actionable": True}
        autos = [
            AutomationConfig(
                when={"classification.actionable": True},
                then=["add_to_board"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, ISSUE_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="add_to_board")]

    def test_no_automations_empty(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=[], comment_count=0,
        )
        resolver = issue_make_resolver(snap)
        assert evaluate_automations([], resolver, {}, ISSUE_CLASSIFICATIONS) == []


# ---------------------------------------------------------------------------
# Safety constants verification
# ---------------------------------------------------------------------------


class TestSafetyConstants:
    """Verify that safety constants are correctly defined for both platforms."""

    def test_pr_deterministic_sources_are_snapshot_fields(self):
        snap = PRSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", status="open", additions=0, deletions=0,
            changed_files=0,
        )
        for source in PR_DETERMINISTIC:
            assert hasattr(snap, source), f"PR DETERMINISTIC_SOURCES has '{source}' but PRSnapshot has no such field"

    def test_issue_deterministic_sources_are_snapshot_fields(self):
        snap = IssueSnapshot(
            org="o", repo="r", number=1, author="a",
            title="T", state="open", labels=[], comment_count=0,
        )
        for source in ISSUE_DETERMINISTIC:
            assert hasattr(snap, source), f"Issue DETERMINISTIC_SOURCES has '{source}' but IssueSnapshot has no such field"

    def test_pr_no_irreversible_actions(self):
        """PR platform is read-only — no irreversible actions defined."""
        from gaas_github.platforms.pull_requests.const import IRREVERSIBLE_ACTIONS, SIMPLE_ACTIONS
        assert len(IRREVERSIBLE_ACTIONS) == 0
        assert len(SIMPLE_ACTIONS) == 0

    def test_issue_no_irreversible_actions(self):
        """Issue platform is read-only — no irreversible actions defined."""
        from gaas_github.platforms.issues.const import IRREVERSIBLE_ACTIONS, SIMPLE_ACTIONS
        assert len(IRREVERSIBLE_ACTIONS) == 0
        assert len(SIMPLE_ACTIONS) == 0
