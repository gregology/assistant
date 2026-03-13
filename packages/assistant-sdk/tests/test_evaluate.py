"""Tests for assistant_sdk.evaluate — the safety-critical automation evaluation engine.

These tests import from assistant_sdk directly (not app.*) so they run
without loading the app config singleton.
"""

from assistant_sdk.evaluate import (
    MISSING,
    check_condition,
    check_deterministic_condition,
    conditions_match,
    eval_operator,
    evaluate_automations,
    resolve_action_provenance,
    unwrap_actions,
)
from assistant_sdk.models import (
    AutomationConfig,
    ClassificationConfig,
    ScriptAction,
    SimpleAction,
    YoloAction,
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
    "score": CONFIDENCE_CLS,
    "flag": BOOLEAN_CLS,
    "priority": ENUM_CLS,
}


def _make_resolver(**fields):
    """Create a resolve_value callable that resolves classification.* and flat keys."""
    def resolve_value(key, classification):
        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            return classification.get(cls_key, MISSING)
        if key in fields:
            return fields[key]
        return MISSING
    return resolve_value


# ---------------------------------------------------------------------------
# MISSING sentinel
# ---------------------------------------------------------------------------


class TestMissingSentinel:
    def test_is_unique(self):
        assert MISSING is not None
        assert MISSING is not False
        assert MISSING != 0
        assert MISSING != ""

    def test_identity_check(self):
        assert MISSING is MISSING


# ---------------------------------------------------------------------------
# eval_operator
# ---------------------------------------------------------------------------


class TestEvalOperator:
    def test_ge(self):
        assert eval_operator(0.8, ">=0.8") is True
        assert eval_operator(0.7, ">=0.8") is False

    def test_gt(self):
        assert eval_operator(0.9, ">0.8") is True
        assert eval_operator(0.8, ">0.8") is False

    def test_le(self):
        assert eval_operator(0.5, "<=0.5") is True
        assert eval_operator(0.6, "<=0.5") is False

    def test_lt(self):
        assert eval_operator(0.4, "<0.5") is True
        assert eval_operator(0.5, "<0.5") is False

    def test_eq(self):
        assert eval_operator(1.0, "==1.0") is True
        assert eval_operator(0.9, "==1.0") is False

    def test_whitespace(self):
        assert eval_operator(0.8, " >= 0.8 ") is True

    def test_invalid_operator(self):
        assert eval_operator(0.5, "!=0.5") is False

    def test_malformed(self):
        assert eval_operator(0.5, "not a number") is False
        assert eval_operator(0.5, "") is False


# ---------------------------------------------------------------------------
# check_condition
# ---------------------------------------------------------------------------


class TestCheckCondition:
    def test_boolean_match(self):
        assert check_condition(True, True, BOOLEAN_CLS) is True
        assert check_condition(False, False, BOOLEAN_CLS) is True

    def test_boolean_mismatch(self):
        assert check_condition(True, False, BOOLEAN_CLS) is False
        assert check_condition(False, True, BOOLEAN_CLS) is False

    def test_boolean_identity_not_equality(self):
        # 1 == True but 1 is not True
        assert check_condition(1, True, BOOLEAN_CLS) is False

    def test_confidence_numeric_threshold(self):
        assert check_condition(0.9, 0.8, CONFIDENCE_CLS) is True
        assert check_condition(0.8, 0.8, CONFIDENCE_CLS) is True
        assert check_condition(0.7, 0.8, CONFIDENCE_CLS) is False

    def test_confidence_string_operator(self):
        assert check_condition(0.9, ">0.8", CONFIDENCE_CLS) is True
        assert check_condition(0.8, ">0.8", CONFIDENCE_CLS) is False

    def test_confidence_unsupported_type(self):
        assert check_condition(0.9, [0.8], CONFIDENCE_CLS) is False

    def test_enum_exact(self):
        assert check_condition("high", "high", ENUM_CLS) is True
        assert check_condition("low", "high", ENUM_CLS) is False

    def test_enum_list(self):
        assert check_condition("high", ["high", "critical"], ENUM_CLS) is True
        assert check_condition("low", ["high", "critical"], ENUM_CLS) is False


# ---------------------------------------------------------------------------
# check_deterministic_condition
# ---------------------------------------------------------------------------


class TestCheckDeterministicCondition:
    def test_boolean_identity(self):
        assert check_deterministic_condition(True, True) is True
        assert check_deterministic_condition(True, False) is False

    def test_string_equality(self):
        assert check_deterministic_condition("a", "a") is True
        assert check_deterministic_condition("a", "b") is False

    def test_list_membership(self):
        assert check_deterministic_condition("a", ["a", "b"]) is True
        assert check_deterministic_condition("c", ["a", "b"]) is False

    def test_numeric_equality(self):
        assert check_deterministic_condition(42, 42) is True
        assert check_deterministic_condition(42, 43) is False

    def test_now_past(self):
        assert check_deterministic_condition("2020-01-01T00:00:00+00:00", "<now()") is True

    def test_now_future(self):
        assert check_deterministic_condition("2099-01-01T00:00:00+00:00", "<now()") is False

    def test_now_invalid(self):
        assert check_deterministic_condition("not-a-date", "<now()") is False


# ---------------------------------------------------------------------------
# conditions_match
# ---------------------------------------------------------------------------


class TestConditionsMatch:
    def test_empty_when_matches(self):
        resolver = _make_resolver()
        assert conditions_match({}, resolver, {}, CLASSIFICATIONS) is True

    def test_classification_match(self):
        resolver = _make_resolver()
        result = {"score": 0.9}
        assert conditions_match(
            {"classification.score": 0.8}, resolver, result, CLASSIFICATIONS,
        ) is True

    def test_classification_miss(self):
        resolver = _make_resolver()
        result = {"score": 0.3}
        assert conditions_match(
            {"classification.score": 0.8}, resolver, result, CLASSIFICATIONS,
        ) is False

    def test_missing_classification_key_returns_false(self):
        resolver = _make_resolver()
        result = {"score": 0.9}
        assert conditions_match(
            {"classification.nonexistent": True}, resolver, result, CLASSIFICATIONS,
        ) is False

    def test_missing_result_key_returns_false(self):
        resolver = _make_resolver()
        assert conditions_match(
            {"classification.score": 0.8}, resolver, {}, CLASSIFICATIONS,
        ) is False

    def test_deterministic_match(self):
        resolver = _make_resolver(org="myorg")
        assert conditions_match(
            {"org": "myorg"}, resolver, {}, CLASSIFICATIONS,
        ) is True

    def test_deterministic_miss(self):
        resolver = _make_resolver(org="myorg")
        assert conditions_match(
            {"org": "otherorg"}, resolver, {}, CLASSIFICATIONS,
        ) is False

    def test_mixed_conditions(self):
        resolver = _make_resolver(org="myorg")
        result = {"score": 0.9}
        assert conditions_match(
            {"org": "myorg", "classification.score": 0.8},
            resolver, result, CLASSIFICATIONS,
        ) is True

    def test_mixed_one_fails(self):
        resolver = _make_resolver(org="wrong")
        result = {"score": 0.9}
        assert conditions_match(
            {"org": "myorg", "classification.score": 0.8},
            resolver, result, CLASSIFICATIONS,
        ) is False

    def test_missing_deterministic_key_returns_false(self):
        resolver = _make_resolver()
        assert conditions_match(
            {"nonexistent_key": "value"}, resolver, {}, CLASSIFICATIONS,
        ) is False


# ---------------------------------------------------------------------------
# evaluate_automations
# ---------------------------------------------------------------------------


class TestEvaluateAutomations:
    def test_matching_returns_actions(self):
        resolver = _make_resolver()
        autos = [AutomationConfig(when={"classification.score": 0.5}, then=["log"])]
        result = {"score": 0.9}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert actions == [SimpleAction(action="log")]

    def test_non_matching_returns_empty(self):
        resolver = _make_resolver()
        autos = [AutomationConfig(when={"classification.score": 0.9}, then=["log"])]
        result = {"score": 0.3}
        assert evaluate_automations(autos, resolver, result, CLASSIFICATIONS) == []

    def test_multiple_automations_combine(self):
        resolver = _make_resolver()
        autos = [
            AutomationConfig(when={"classification.score": 0.5}, then=["a"]),
            AutomationConfig(when={"classification.flag": True}, then=["b"]),
        ]
        result = {"score": 0.9, "flag": True}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert SimpleAction(action="a") in actions
        assert SimpleAction(action="b") in actions

    def test_duplicate_string_actions_deduplicated(self):
        """Two rules both producing 'archive' yield a single 'archive' action."""
        resolver = _make_resolver()
        autos = [
            AutomationConfig(when={"classification.score": 0.5}, then=["archive"]),
            AutomationConfig(when={"classification.flag": True}, then=["archive"]),
        ]
        result = {"score": 0.9, "flag": True}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert actions == [SimpleAction(action="archive")]

    def test_duplicate_string_across_rules_preserves_order(self):
        """Dedup keeps first occurrence; non-duplicate actions pass through."""
        resolver = _make_resolver()
        autos = [
            AutomationConfig(when={"classification.score": 0.5}, then=["archive", "log"]),
            AutomationConfig(when={"classification.flag": True}, then=["archive", "alert"]),
        ]
        result = {"score": 0.9, "flag": True}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert actions == [
            SimpleAction(action="archive"),
            SimpleAction(action="log"),
            SimpleAction(action="alert"),
        ]

    def test_dict_actions_not_deduplicated(self):
        """Dict actions (service, script) are never deduplicated."""
        resolver = _make_resolver()
        svc = {"service": {"call": "a.b.c"}}
        autos = [
            AutomationConfig(when={"classification.score": 0.5}, then=[svc]),
            AutomationConfig(when={"classification.flag": True}, then=[svc]),
        ]
        result = {"score": 0.9, "flag": True}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert len(actions) == 2

    def test_yolo_string_not_deduped_with_plain(self):
        """A plain 'archive' and YoloAction('archive') are not treated as duplicates."""
        resolver = _make_resolver()
        autos = [
            AutomationConfig(when={"classification.score": 0.5}, then=["archive"]),
            AutomationConfig(when={"classification.flag": True}, then=[YoloAction("archive")]),
        ]
        result = {"score": 0.9, "flag": True}
        actions = evaluate_automations(autos, resolver, result, CLASSIFICATIONS)
        assert len(actions) == 2

    def test_no_automations(self):
        resolver = _make_resolver()
        assert evaluate_automations([], resolver, {"score": 0.9}, CLASSIFICATIONS) == []

    def test_deterministic_automation(self):
        resolver = _make_resolver(org="myorg")
        autos = [AutomationConfig(when={"org": "myorg"}, then=["log"])]
        actions = evaluate_automations(autos, resolver, {}, CLASSIFICATIONS)
        assert actions == [SimpleAction(action="log")]


# ---------------------------------------------------------------------------
# resolve_action_provenance
# ---------------------------------------------------------------------------


class TestResolveActionProvenance:
    DETERMINISTIC = frozenset({"org", "repo", "author"})

    def test_pure_rule(self):
        resolver = _make_resolver(org="myorg")
        autos = [AutomationConfig(when={"org": "myorg"}, then=["log"])]
        result = resolve_action_provenance(
            autos, resolver, {}, CLASSIFICATIONS, self.DETERMINISTIC,
        )
        assert result == "rule"

    def test_pure_llm(self):
        resolver = _make_resolver()
        autos = [AutomationConfig(when={"classification.score": 0.5}, then=["log"])]
        result = resolve_action_provenance(
            autos, resolver, {"score": 0.9}, CLASSIFICATIONS, self.DETERMINISTIC,
        )
        assert result == "llm"

    def test_hybrid(self):
        """Hybrid requires both a pure-rule and an LLM-influenced automation matching."""
        resolver = _make_resolver(org="myorg")
        autos = [
            AutomationConfig(when={"org": "myorg"}, then=["log"]),
            AutomationConfig(when={"classification.score": 0.5}, then=["alert"]),
        ]
        result = resolve_action_provenance(
            autos, resolver, {"score": 0.9}, CLASSIFICATIONS, self.DETERMINISTIC,
        )
        assert result == "hybrid"

    def test_single_mixed_automation_is_llm(self):
        """A single automation with mixed conditions is LLM-influenced at the aggregate level."""
        resolver = _make_resolver(org="myorg")
        autos = [
            AutomationConfig(
                when={"org": "myorg", "classification.score": 0.5},
                then=["log"],
            ),
        ]
        result = resolve_action_provenance(
            autos, resolver, {"score": 0.9}, CLASSIFICATIONS, self.DETERMINISTIC,
        )
        assert result == "llm"

    def test_no_matching_automations(self):
        resolver = _make_resolver()
        autos = [AutomationConfig(when={"classification.score": 0.99}, then=["log"])]
        result = resolve_action_provenance(
            autos, resolver, {"score": 0.1}, CLASSIFICATIONS, self.DETERMINISTIC,
        )
        assert result == "rule"


# ---------------------------------------------------------------------------
# unwrap_actions
# ---------------------------------------------------------------------------


class TestUnwrapActions:
    def test_plain_actions_unchanged(self):
        actions = [SimpleAction(action="a"), SimpleAction(action="b")]
        assert unwrap_actions(actions) == [SimpleAction(action="a"), SimpleAction(action="b")]

    def test_yolo_unwrapped(self):
        actions = [YoloAction("unsubscribe"), SimpleAction(action="archive")]
        result = unwrap_actions(actions)
        assert result == [SimpleAction(action="unsubscribe"), SimpleAction(action="archive")]

    def test_yolo_dict_unwrapped(self):
        d = {"script": {"name": "nuke"}}
        actions = [YoloAction(d)]
        result = unwrap_actions(actions)
        assert len(result) == 1
        assert isinstance(result[0], ScriptAction)
        assert result[0].script == {"name": "nuke"}

    def test_empty(self):
        assert unwrap_actions([]) == []
