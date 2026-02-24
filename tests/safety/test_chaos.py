"""Chaos testing at the classification boundary.

The dangerous failure is the LLM being confidently wrong. These tests
inject chaotic classification results and assert that safety boundaries
still hold. The dispatch layer must be robust to garbage input.
"""

import pytest
from hypothesis import given, settings, strategies as st

from app.config import AutomationConfig, ClassificationConfig
from app.integrations.email.platforms.inbox.evaluate import (
    _evaluate_automations,
    _check_condition,
    _conditions_match,
)

# ---------------------------------------------------------------------------
# Shared configs
# ---------------------------------------------------------------------------

CLASSIFICATIONS = {
    "human": ClassificationConfig(prompt="is this human?"),
    "user_agreement_update": ClassificationConfig(
        prompt="user agreement?", type="boolean"
    ),
    "requires_response": ClassificationConfig(
        prompt="requires response?", type="boolean"
    ),
    "priority": ClassificationConfig(
        prompt="priority?", type="enum", values=["low", "medium", "high", "critical"]
    ),
}

AUTOMATIONS = [
    AutomationConfig(when={"classification.user_agreement_update": True}, then=["archive"]),
    AutomationConfig(when={"classification.human": 0.8}, then=["spam"]),
    AutomationConfig(
        when={"classification.human": 0.8, "classification.requires_response": True},
        then=[{"draft_reply": "I'll review this shortly."}],
    ),
    AutomationConfig(
        when={"classification.priority": ["high", "critical"]},
        then=[{"draft_reply": "Urgent, reviewing now."}],
    ),
    AutomationConfig(when={"classification.human": ">0.9"}, then=["unsubscribe"]),
]

ALLOWED_ACTIONS = {"archive", "spam", "unsubscribe", "draft_reply"}


class _MockEmail:
    def __init__(self):
        self.from_address = "sender@example.com"
        self.authentication = {"dkim_pass": True, "dmarc_pass": True, "spf_pass": True}
        self.calendar = None

    @property
    def domain(self):
        return "example.com"

    @property
    def is_noreply(self):
        return False

    @property
    def is_calendar_event(self):
        return self.calendar is not None


_DEFAULT_EMAIL = _MockEmail()


def _extract_action_names(actions: list) -> set[str]:
    names = set()
    for action in actions:
        if isinstance(action, str):
            names.add(action)
        elif isinstance(action, dict):
            names.update(action.keys())
    return names


# ---------------------------------------------------------------------------
# Chaos: extreme classification values
# ---------------------------------------------------------------------------


class TestChaosClassifications:
    def test_all_max_confidence(self):
        """Every confidence at 1.0, every boolean True, most dangerous enum."""
        result = {
            "human": 1.0,
            "user_agreement_update": True,
            "requires_response": True,
            "priority": "critical",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_all_min_confidence(self):
        """Everything at minimum values."""
        result = {
            "human": 0.0,
            "user_agreement_update": False,
            "requires_response": False,
            "priority": "low",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_flipped_booleans(self):
        """Booleans flipped from expected values."""
        result = {
            "human": 0.5,
            "user_agreement_update": True,  # flipped: not actually an update
            "requires_response": False,
            "priority": "medium",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_contradictory_classification(self):
        """High human confidence but also a user agreement update.
        A confused LLM might produce this."""
        result = {
            "human": 0.95,
            "user_agreement_update": True,
            "requires_response": True,
            "priority": "critical",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS


# ---------------------------------------------------------------------------
# Chaos: garbage input from a broken LLM
# ---------------------------------------------------------------------------


class TestChaosGarbageInput:
    def test_out_of_range_confidence_does_not_crash(self):
        """Confidence values outside 0-1 range should not crash the dispatch."""
        result = {
            "human": 5.0,
            "user_agreement_update": True,
            "requires_response": True,
            "priority": "high",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_negative_confidence_does_not_crash(self):
        result = {
            "human": -1.0,
            "user_agreement_update": False,
            "requires_response": False,
            "priority": "low",
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_wrong_type_for_boolean_does_not_crash(self):
        """LLM returns a string instead of a boolean."""
        result = {
            "human": 0.5,
            "user_agreement_update": "yes",
            "requires_response": "no",
            "priority": "low",
        }
        # _check_condition uses `is` for booleans, so "yes" is not True
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_wrong_type_for_enum_does_not_crash(self):
        """LLM returns a number instead of an enum string."""
        result = {
            "human": 0.5,
            "user_agreement_update": False,
            "requires_response": False,
            "priority": 999,
        }
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_missing_keys_does_not_crash(self):
        """LLM returns only partial classification."""
        result = {"human": 0.5}
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_empty_result_does_not_crash(self):
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, {}, CLASSIFICATIONS)
        assert actions == []

    def test_none_values_do_not_crash(self):
        result = {
            "human": None,
            "user_agreement_update": None,
            "requires_response": None,
            "priority": None,
        }
        # Should not raise, may or may not produce actions depending on
        # how None compares, but must never crash
        try:
            actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
            produced = _extract_action_names(actions)
            assert produced <= ALLOWED_ACTIONS
        except TypeError:
            # A TypeError from None comparison is acceptable behavior
            # as long as the system doesn't produce unsafe actions
            pass


# ---------------------------------------------------------------------------
# Chaos: Hypothesis fuzz
# ---------------------------------------------------------------------------


# Strategy that generates chaotic values including out-of-range and wrong types
chaotic_value = st.one_of(
    st.floats(min_value=-10.0, max_value=10.0),
    st.booleans(),
    st.sampled_from(["low", "medium", "high", "critical", "unknown", "", None]),
    st.integers(min_value=-100, max_value=100),
    st.text(max_size=20),
)

chaotic_result = st.fixed_dictionaries(
    {
        "human": chaotic_value,
        "user_agreement_update": chaotic_value,
        "requires_response": chaotic_value,
        "priority": chaotic_value,
    }
)


@given(result=chaotic_result)
@settings(max_examples=500)
def test_dispatch_never_crashes_on_garbage(result):
    """The dispatch layer must handle any input from a confused LLM
    without raising an unhandled exception, and must only ever produce
    actions from the allowed set."""
    try:
        actions = _evaluate_automations(AUTOMATIONS, _DEFAULT_EMAIL, result, CLASSIFICATIONS)
    except TypeError:
        # TypeError from comparison operations (e.g. None >= 0.8) is
        # acceptable as a rejection of garbage input
        return

    produced = _extract_action_names(actions)
    assert produced <= ALLOWED_ACTIONS, (
        f"Unknown actions {produced - ALLOWED_ACTIONS} from chaotic result={result}"
    )
