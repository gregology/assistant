"""Property-based safety tests.

For ALL possible classification outputs, assert that the automation
dispatch layer never produces unknown actions and that the blast
radius is bounded.
"""

from hypothesis import given, settings, strategies as st

from app.config import AutomationConfig, ClassificationConfig
from app.evaluate import MISSING, evaluate_automations

# ---------------------------------------------------------------------------
# Classification configs matching the default set
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

# Realistic automation configs covering every action type
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

# ---------------------------------------------------------------------------
# Hypothesis strategy: generate any possible classification result
# ---------------------------------------------------------------------------

classification_result = st.fixed_dictionaries(
    {
        "human": st.floats(min_value=0.0, max_value=1.0),
        "user_agreement_update": st.booleans(),
        "requires_response": st.booleans(),
        "priority": st.sampled_from(["low", "medium", "high", "critical"]),
    }
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


def _extract_action_names(actions: list) -> set[str]:
    """Extract the action name from both string and dict actions."""
    names = set()
    for action in actions:
        if isinstance(action, str):
            names.add(action)
        elif isinstance(action, dict):
            names.update(action.keys())
    return names


@given(result=classification_result)
@settings(max_examples=500)
def test_only_known_actions_produced(result):
    """For ALL possible classification outputs, every action produced
    must be in the allowed set. No unknown action can ever appear."""
    actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
    produced = _extract_action_names(actions)
    unknown = produced - ALLOWED_ACTIONS
    assert not unknown, f"Unknown actions produced: {unknown} from result={result}"


@given(result=classification_result)
@settings(max_examples=500)
def test_action_count_bounded(result):
    """The total number of actions from a single evaluation should never
    exceed the sum of all possible automation outputs."""
    max_possible = sum(len(a.then) for a in AUTOMATIONS)
    actions = evaluate_automations(AUTOMATIONS, _DEFAULT_RESOLVER, result, CLASSIFICATIONS)
    assert len(actions) <= max_possible, (
        f"Produced {len(actions)} actions, max possible is {max_possible}"
    )


@given(result=classification_result)
@settings(max_examples=500)
def test_missing_key_never_matches(result):
    """An automation referencing a classification key not present in the
    result should never fire."""
    automation_with_missing = AutomationConfig(
        when={"classification.nonexistent_classification": True},
        then=["archive"],
    )
    actions = evaluate_automations(
        [automation_with_missing], _DEFAULT_RESOLVER, result, CLASSIFICATIONS
    )
    assert actions == [], (
        f"Automation with nonexistent key fired: actions={actions}"
    )
