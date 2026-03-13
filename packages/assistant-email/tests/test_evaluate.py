"""Tests for email evaluate module — snapshot construction and resolver patterns.

Tests focus on the deterministic dispatch boundary: snapshot construction from
frontmatter, value resolution, and automation evaluation. The LLM is not
involved — these test the decision boundary per the testing philosophy.
"""

from hypothesis import given, settings, strategies as st

from assistant_sdk.evaluate import MISSING, evaluate_automations
from assistant_sdk.models import AutomationConfig, SimpleAction
from assistant_email.platforms.inbox.evaluate import (
    EmailSnapshot,
    _make_resolver,
    _snapshot_from_frontmatter,
)
from assistant_email.platforms.inbox.const import (
    DEFAULT_CLASSIFICATIONS,
    DETERMINISTIC_SOURCES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FULL_FRONTMATTER = {
    "from_address": "alice@example.com",
    "domain": "example.com",
    "is_noreply": False,
    "is_calendar_event": True,
    "is_reply": True,
    "is_forward": False,
    "is_unsubscribable": True,
    "has_attachments": True,
    "is_read": True,
    "is_starred": False,
    "is_answered": True,
    "authentication": {"dkim_pass": True, "dmarc_pass": True, "spf_pass": False},
    "calendar": {
        "summary": "Team standup",
        "start": "2026-03-05T09:00:00Z",
        "end": "2026-03-05T09:30:00Z",
    },
}


def _full_snapshot() -> EmailSnapshot:
    return _snapshot_from_frontmatter(FULL_FRONTMATTER)


# ---------------------------------------------------------------------------
# EmailSnapshot
# ---------------------------------------------------------------------------


class TestEmailSnapshot:
    def test_from_complete_frontmatter(self):
        snap = _snapshot_from_frontmatter(FULL_FRONTMATTER)
        assert snap.from_address == "alice@example.com"
        assert snap.domain == "example.com"
        assert snap.is_noreply is False
        assert snap.is_calendar_event is True
        assert snap.is_reply is True
        assert snap.is_forward is False
        assert snap.is_unsubscribable is True
        assert snap.has_attachments is True
        assert snap.is_read is True
        assert snap.is_starred is False
        assert snap.is_answered is True
        assert snap.authentication == {"dkim_pass": True, "dmarc_pass": True, "spf_pass": False}
        assert snap.calendar == {
            "summary": "Team standup",
            "start": "2026-03-05T09:00:00Z",
            "end": "2026-03-05T09:30:00Z",
        }

    def test_defaults_for_missing_keys(self):
        meta = {"from_address": "bob@test.com", "domain": "test.com"}
        snap = _snapshot_from_frontmatter(meta)
        assert snap.from_address == "bob@test.com"
        assert snap.domain == "test.com"
        assert snap.is_noreply is False
        assert snap.is_calendar_event is False
        assert snap.is_reply is False
        assert snap.is_forward is False
        assert snap.is_unsubscribable is False
        assert snap.has_attachments is False
        assert snap.is_read is False
        assert snap.is_starred is False
        assert snap.is_answered is False
        assert snap.authentication == {}
        assert snap.calendar is None

    def test_empty_dict(self):
        snap = _snapshot_from_frontmatter({})
        assert snap.from_address == ""
        assert snap.domain == ""
        assert snap.is_noreply is False
        assert snap.is_calendar_event is False
        assert snap.is_reply is False
        assert snap.is_forward is False
        assert snap.is_unsubscribable is False
        assert snap.has_attachments is False
        assert snap.is_read is False
        assert snap.is_starred is False
        assert snap.is_answered is False
        assert snap.authentication == {}
        assert snap.calendar is None


# ---------------------------------------------------------------------------
# EmailResolver
# ---------------------------------------------------------------------------


class TestEmailResolver:
    def test_resolves_flat_snapshot_fields(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("domain", {}) == "example.com"
        assert resolver("from_address", {}) == "alice@example.com"
        assert resolver("is_noreply", {}) is False
        assert resolver("is_calendar_event", {}) is True
        assert resolver("is_reply", {}) is True
        assert resolver("is_forward", {}) is False
        assert resolver("is_unsubscribable", {}) is True
        assert resolver("has_attachments", {}) is True
        assert resolver("is_read", {}) is True
        assert resolver("is_starred", {}) is False
        assert resolver("is_answered", {}) is True

    def test_resolves_classification_keys(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        classification = {"human": 0.95, "priority": "high"}
        assert resolver("classification.human", classification) == 0.95
        assert resolver("classification.priority", classification) == "high"

    def test_missing_classification_returns_missing(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("classification.nonexistent", {}) is MISSING

    def test_resolves_authentication_keys(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("authentication.dkim_pass", {}) is True
        assert resolver("authentication.dmarc_pass", {}) is True
        assert resolver("authentication.spf_pass", {}) is False

    def test_missing_authentication_key_returns_missing(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("authentication.nonexistent", {}) is MISSING

    def test_resolves_calendar_keys(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("calendar.summary", {}) == "Team standup"
        assert resolver("calendar.start", {}) == "2026-03-05T09:00:00Z"
        assert resolver("calendar.end", {}) == "2026-03-05T09:30:00Z"

    def test_calendar_returns_missing_when_none(self):
        snap = _snapshot_from_frontmatter({})
        assert snap.calendar is None
        resolver = _make_resolver(snap)
        assert resolver("calendar.summary", {}) is MISSING
        assert resolver("calendar.start", {}) is MISSING

    def test_missing_calendar_key_returns_missing(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("calendar.nonexistent", {}) is MISSING

    def test_unknown_snapshot_field_returns_missing(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert resolver("nonexistent_field", {}) is MISSING


# ---------------------------------------------------------------------------
# Safety constants verification
# ---------------------------------------------------------------------------


class TestSafetyConstants:
    def test_deterministic_sources_resolve_from_full_snapshot(self):
        """Every DETERMINISTIC_SOURCES entry resolves to a non-MISSING value
        when the snapshot is fully populated. Catches drift between the const
        set and the resolver/snapshot."""
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        for source in DETERMINISTIC_SOURCES:
            value = resolver(source, {})
            assert value is not MISSING, (
                f"DETERMINISTIC_SOURCES has '{source}' but resolver returns MISSING"
            )

    def test_deterministic_sources_are_snapshot_fields(self):
        snap = _full_snapshot()
        for source in DETERMINISTIC_SOURCES:
            assert hasattr(snap, source), (
                f"DETERMINISTIC_SOURCES has '{source}' but EmailSnapshot has no such field"
            )

    def test_irreversible_actions_subset_of_simple(self):
        from assistant_email.platforms.inbox.const import IRREVERSIBLE_ACTIONS, SIMPLE_ACTIONS
        assert IRREVERSIBLE_ACTIONS <= SIMPLE_ACTIONS, (
            f"Irreversible actions not in SIMPLE_ACTIONS: {IRREVERSIBLE_ACTIONS - SIMPLE_ACTIONS}"
        )


# ---------------------------------------------------------------------------
# Email automation evaluation (end-to-end with real resolver)
# ---------------------------------------------------------------------------


class TestEmailAutomationEvaluation:
    def test_deterministic_domain_match(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(when={"domain": "example.com"}, then=["archive"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="archive")]

    def test_deterministic_domain_miss(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(when={"domain": "other.com"}, then=["archive"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == []

    def test_deterministic_is_noreply(self):
        meta = {**FULL_FRONTMATTER, "is_noreply": True}
        snap = _snapshot_from_frontmatter(meta)
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(when={"is_noreply": True}, then=["archive"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="archive")]

    def test_classification_match(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        classification = {
            "human": 0.95, "user_agreement_update": False,
            "requires_response": True, "priority": "high",
        }
        autos = [
            AutomationConfig(
                when={"classification.human": ">0.8"},
                then=["flag"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="flag")]

    def test_classification_boolean(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        classification = {
            "human": 0.5, "user_agreement_update": True,
            "requires_response": False, "priority": "low",
        }
        autos = [
            AutomationConfig(
                when={"classification.user_agreement_update": True},
                then=["archive"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="archive")]

    def test_mixed_deterministic_and_classification(self):
        meta = {**FULL_FRONTMATTER, "is_noreply": True}
        snap = _snapshot_from_frontmatter(meta)
        resolver = _make_resolver(snap)
        classification = {
            "human": 0.1, "user_agreement_update": True,
            "requires_response": False, "priority": "low",
        }
        autos = [
            AutomationConfig(
                when={"is_noreply": True, "classification.user_agreement_update": True},
                then=["trash"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, classification, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="trash")]

    def test_missing_classification_safe_default(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(
                when={"classification.nonexistent": True},
                then=["unsubscribe"],
            ),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == []

    def test_no_automations_empty(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        assert evaluate_automations([], resolver, {}, DEFAULT_CLASSIFICATIONS) == []

    def test_authentication_condition(self):
        snap = _full_snapshot()
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(when={"authentication.dkim_pass": True}, then=["log"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == [SimpleAction(action="log")]

    def test_authentication_miss(self):
        meta = {
            **FULL_FRONTMATTER,
            "authentication": {
                "dkim_pass": False, "dmarc_pass": False, "spf_pass": False,
            },
        }
        snap = _snapshot_from_frontmatter(meta)
        resolver = _make_resolver(snap)
        autos = [
            AutomationConfig(when={"authentication.dkim_pass": True}, then=["log"]),
        ]
        actions = evaluate_automations(autos, resolver, {}, DEFAULT_CLASSIFICATIONS)
        assert actions == []


# ---------------------------------------------------------------------------
# Property-based test (Hypothesis)
# ---------------------------------------------------------------------------

# Strategy: generate realistic frontmatter dicts with optional keys
_frontmatter_strategy = st.fixed_dictionaries(
    {
        "from_address": st.text(min_size=0, max_size=50),
        "domain": st.text(min_size=0, max_size=50),
        "is_noreply": st.booleans(),
        "is_calendar_event": st.booleans(),
        "is_reply": st.booleans(),
        "is_forward": st.booleans(),
        "is_unsubscribable": st.booleans(),
        "has_attachments": st.booleans(),
        "is_read": st.booleans(),
        "is_starred": st.booleans(),
        "is_answered": st.booleans(),
    },
    optional={
        "authentication": st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(st.booleans(), st.text(max_size=20)),
            max_size=5,
        ),
        "calendar": st.one_of(
            st.none(),
            st.dictionaries(
                keys=st.text(min_size=1, max_size=20),
                values=st.text(max_size=50),
                max_size=5,
            ),
        ),
    },
)

_classification_strategy = st.fixed_dictionaries(
    {
        "human": st.floats(min_value=0.0, max_value=1.0),
        "user_agreement_update": st.booleans(),
        "requires_response": st.booleans(),
        "priority": st.sampled_from(["low", "medium", "high", "critical"]),
    }
)

# Representative automations covering every namespace the resolver handles
_PROPERTY_AUTOMATIONS = [
    AutomationConfig(when={"domain": "example.com"}, then=["archive"]),
    AutomationConfig(when={"is_noreply": True}, then=["trash"]),
    AutomationConfig(when={"classification.human": ">0.9"}, then=["flag"]),
    AutomationConfig(when={"classification.user_agreement_update": True}, then=["archive"]),
    AutomationConfig(when={"authentication.dkim_pass": True}, then=["log"]),
    AutomationConfig(when={"calendar.summary": "standup"}, then=["log"]),
]


@given(meta=_frontmatter_strategy, classification=_classification_strategy)
@settings(max_examples=500)
def test_resolver_never_raises(meta, classification):
    """For any frontmatter input, building a snapshot + resolver and evaluating
    automations must never raise an unhandled exception."""
    snap = _snapshot_from_frontmatter(meta)
    resolver = _make_resolver(snap)
    # Exercise the resolver across all key namespaces
    resolver("domain", classification)
    resolver("is_noreply", classification)
    resolver("classification.human", classification)
    resolver("authentication.dkim_pass", classification)
    resolver("calendar.summary", classification)
    resolver("nonexistent_field", classification)
    # Exercise full automation evaluation
    evaluate_automations(_PROPERTY_AUTOMATIONS, resolver, classification, DEFAULT_CLASSIFICATIONS)
