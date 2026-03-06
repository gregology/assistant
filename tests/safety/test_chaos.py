"""Chaos testing at the classification boundary.

The dangerous failure is the LLM being confidently wrong. These tests
inject chaotic classification results and assert that safety boundaries
still hold. The dispatch layer must be robust to garbage input.
"""

from hypothesis import given, settings, strategies as st

from app.config import AutomationConfig, ClassificationConfig
from gaas_sdk.evaluate import MISSING, evaluate_automations
from gaas_sdk.models import DictAction, ScriptAction, ServiceAction, SimpleAction

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
    AutomationConfig(
        when={"classification.user_agreement_update": True},
        then=[{"script": {"name": "research_tos", "inputs": {"domain": "{{ domain }}"}}}],
    ),
]

ALLOWED_ACTIONS = {"archive", "spam", "unsubscribe", "draft_reply", "script"}


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


def _make_email_resolver(email):
    def resolve_value(key, classification):
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            return classification.get(cls_key, MISSING)
        if key.startswith("authentication."):
            auth_key = key[len("authentication."):]
            return email.authentication.get(auth_key, MISSING)
        if key.startswith("calendar."):
            if email.calendar is None:
                return MISSING
            cal_key = key[len("calendar."):]
            return email.calendar.get(cal_key, MISSING)
        return getattr(email, key, MISSING)
    return resolve_value


_DEFAULT_RESOLVER = _make_email_resolver(_DEFAULT_EMAIL)


def _extract_action_names(actions: list) -> set[str]:
    names = set()
    for action in actions:
        if isinstance(action, SimpleAction):
            names.add(action.action)
        elif isinstance(action, ScriptAction):
            names.add("script")
        elif isinstance(action, ServiceAction):
            names.add("service")
        elif isinstance(action, DictAction):
            names.update(action.data.keys())
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_negative_confidence_does_not_crash(self):
        result = {
            "human": -1.0,
            "user_agreement_update": False,
            "requires_response": False,
            "priority": "low",
        }
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        # check_condition uses `is` for booleans, so "yes" is not True
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
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
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_missing_keys_does_not_crash(self):
        """LLM returns only partial classification."""
        result = {"human": 0.5}
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS

    def test_empty_result_does_not_crash(self):
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, {}, CLASSIFICATIONS)
        assert actions == []

    def test_none_confidence_does_not_block_boolean_automation(self):
        """A None confidence value must not prevent a boolean automation
        from firing. Per-automation false, not global crash."""
        result = {
            "human": None,
            "user_agreement_update": True,
            "requires_response": True,
            "priority": "high",
        }
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS
        # Boolean automation fires despite None confidence
        assert "archive" in produced
        # Confidence-gated automations do not fire
        assert "spam" not in produced
        assert "unsubscribe" not in produced

    def test_none_values_do_not_crash(self):
        """None-valued fields must not block unrelated automations.

        A None classification should behave like a missing key: the
        automation referencing it doesn't fire, but other automations
        still evaluate normally.
        """
        result = {
            "human": None,
            "user_agreement_update": True,
            "requires_response": None,
            "priority": None,
        }
        actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
        produced = _extract_action_names(actions)
        assert produced <= ALLOWED_ACTIONS
        # The user_agreement_update automation should still fire
        assert "archive" in produced


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
    actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
    produced = _extract_action_names(actions)
    assert produced <= ALLOWED_ACTIONS, (
        f"Unknown actions {produced - ALLOWED_ACTIONS} from chaotic result={result}"
    )
