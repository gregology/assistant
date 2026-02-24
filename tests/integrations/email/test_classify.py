from app.config import AutomationConfig, ClassificationConfig
from app.integrations.email.platforms.inbox.classify import _build_schema
from app.integrations.email.platforms.inbox.evaluate import (
    _check_condition,
    _check_deterministic_condition,
    _conditions_match,
    _eval_operator,
    _evaluate_automations,
)

# ---------------------------------------------------------------------------
# Shared classification configs
# ---------------------------------------------------------------------------

CONFIDENCE_CLS = ClassificationConfig(prompt="test confidence")
BOOLEAN_CLS = ClassificationConfig(prompt="test boolean", type="boolean")
ENUM_CLS = ClassificationConfig(
    prompt="test enum", type="enum", values=["low", "medium", "high", "critical"]
)

CLASSIFICATIONS = {
    "human": CONFIDENCE_CLS,
    "requires_response": BOOLEAN_CLS,
    "priority": ENUM_CLS,
}


class _MockEmail:
    def __init__(self, **kwargs):
        self.from_address = kwargs.get("from_address", "sender@example.com")
        self.authentication = kwargs.get("authentication", {
            "dkim_pass": True, "dmarc_pass": True, "spf_pass": True,
        })
        self.calendar = kwargs.get("calendar", None)

    @property
    def domain(self):
        _, _, d = self.from_address.partition("@")
        return d.lower()

    @property
    def is_noreply(self):
        import re
        return bool(re.match(
            r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster)@",
            self.from_address,
            re.IGNORECASE,
        ))

    @property
    def is_calendar_event(self):
        return self.calendar is not None


# ---------------------------------------------------------------------------
# _eval_operator
# ---------------------------------------------------------------------------


class TestEvalOperator:
    def test_ge(self):
        assert _eval_operator(0.8, ">=0.8") is True
        assert _eval_operator(0.7, ">=0.8") is False

    def test_gt(self):
        assert _eval_operator(0.9, ">0.8") is True
        assert _eval_operator(0.8, ">0.8") is False

    def test_le(self):
        assert _eval_operator(0.5, "<=0.5") is True
        assert _eval_operator(0.6, "<=0.5") is False

    def test_lt(self):
        assert _eval_operator(0.4, "<0.5") is True
        assert _eval_operator(0.5, "<0.5") is False

    def test_eq(self):
        assert _eval_operator(1.0, "==1.0") is True
        assert _eval_operator(0.9, "==1.0") is False

    def test_whitespace_tolerance(self):
        assert _eval_operator(0.8, " >= 0.8 ") is True

    def test_invalid_operator_returns_false(self):
        assert _eval_operator(0.5, "!=0.5") is False

    def test_malformed_expression_returns_false(self):
        assert _eval_operator(0.5, "not a number") is False
        assert _eval_operator(0.5, "") is False


# ---------------------------------------------------------------------------
# _check_condition
# ---------------------------------------------------------------------------


class TestCheckCondition:
    # Boolean
    def test_boolean_true_match(self):
        assert _check_condition(True, True, BOOLEAN_CLS) is True

    def test_boolean_false_match(self):
        assert _check_condition(False, False, BOOLEAN_CLS) is True

    def test_boolean_mismatch(self):
        assert _check_condition(True, False, BOOLEAN_CLS) is False
        assert _check_condition(False, True, BOOLEAN_CLS) is False

    # Confidence with numeric threshold
    def test_confidence_meets_threshold(self):
        assert _check_condition(0.9, 0.8, CONFIDENCE_CLS) is True

    def test_confidence_below_threshold(self):
        assert _check_condition(0.7, 0.8, CONFIDENCE_CLS) is False

    def test_confidence_exact_threshold(self):
        assert _check_condition(0.8, 0.8, CONFIDENCE_CLS) is True

    # Confidence with string operator
    def test_confidence_string_operator(self):
        assert _check_condition(0.9, ">0.8", CONFIDENCE_CLS) is True
        assert _check_condition(0.8, ">0.8", CONFIDENCE_CLS) is False
        assert _check_condition(0.3, "<0.5", CONFIDENCE_CLS) is True

    # Confidence with unsupported condition type
    def test_confidence_unsupported_type_returns_false(self):
        assert _check_condition(0.9, [0.8], CONFIDENCE_CLS) is False

    # Enum exact match
    def test_enum_exact_match(self):
        assert _check_condition("high", "high", ENUM_CLS) is True

    def test_enum_mismatch(self):
        assert _check_condition("low", "high", ENUM_CLS) is False

    # Enum list (any-of)
    def test_enum_list_any_match(self):
        assert _check_condition("high", ["high", "critical"], ENUM_CLS) is True
        assert _check_condition("critical", ["high", "critical"], ENUM_CLS) is True

    def test_enum_list_no_match(self):
        assert _check_condition("low", ["high", "critical"], ENUM_CLS) is False


# ---------------------------------------------------------------------------
# _check_deterministic_condition
# ---------------------------------------------------------------------------


class TestCheckDeterministicCondition:
    def test_boolean_identity(self):
        assert _check_deterministic_condition(True, True) is True
        assert _check_deterministic_condition(False, False) is True
        assert _check_deterministic_condition(True, False) is False

    def test_string_equality(self):
        assert _check_deterministic_condition("example.com", "example.com") is True
        assert _check_deterministic_condition("other.com", "example.com") is False

    def test_list_membership(self):
        assert _check_deterministic_condition("work.com", ["work.com", "home.com"]) is True
        assert _check_deterministic_condition("other.com", ["work.com", "home.com"]) is False

    def test_now_lt_past_datetime(self):
        past = "2020-01-01T00:00:00+00:00"
        assert _check_deterministic_condition(past, "<now()") is True

    def test_now_lt_future_datetime(self):
        future = "2099-01-01T00:00:00+00:00"
        assert _check_deterministic_condition(future, "<now()") is False

    def test_now_gt_future_datetime(self):
        future = "2099-01-01T00:00:00+00:00"
        assert _check_deterministic_condition(future, ">now()") is True

    def test_now_gt_past_datetime(self):
        past = "2020-01-01T00:00:00+00:00"
        assert _check_deterministic_condition(past, ">now()") is False

    def test_now_date_only_value(self):
        assert _check_deterministic_condition("2020-01-01", "<now()") is True

    def test_now_invalid_value_returns_false(self):
        assert _check_deterministic_condition("not-a-date", "<now()") is False

    def test_now_whitespace_tolerance(self):
        past = "2020-01-01T00:00:00+00:00"
        assert _check_deterministic_condition(past, " < now() ") is True


# ---------------------------------------------------------------------------
# _conditions_match
# ---------------------------------------------------------------------------


class TestConditionsMatch:
    def test_all_classification_conditions_must_match(self):
        email = _MockEmail()
        result = {"human": 0.9, "requires_response": True, "priority": "high"}
        when = {"classification.human": 0.8, "classification.requires_response": True}
        assert _conditions_match(when, email, result, CLASSIFICATIONS) is True

        when_fail = {"classification.human": 0.8, "classification.requires_response": False}
        assert _conditions_match(when_fail, email, result, CLASSIFICATIONS) is False

    def test_missing_classification_key_returns_false(self):
        email = _MockEmail()
        result = {"human": 0.9}
        when = {"classification.human": 0.8, "classification.nonexistent_key": True}
        assert _conditions_match(when, email, result, CLASSIFICATIONS) is False

    def test_missing_result_key_returns_false(self):
        email = _MockEmail()
        result = {}
        when = {"classification.human": 0.8}
        assert _conditions_match(when, email, result, CLASSIFICATIONS) is False

    def test_empty_when_matches_everything(self):
        email = _MockEmail()
        result = {"human": 0.5, "requires_response": False, "priority": "low"}
        assert _conditions_match({}, email, result, CLASSIFICATIONS) is True

    def test_domain_condition(self):
        email = _MockEmail(from_address="user@work.com")
        when = {"domain": "work.com"}
        assert _conditions_match(when, email, {}, CLASSIFICATIONS) is True
        assert _conditions_match({"domain": "other.com"}, email, {}, CLASSIFICATIONS) is False

    def test_authentication_condition(self):
        email = _MockEmail(authentication={"dkim_pass": True, "spf_pass": False})
        assert _conditions_match(
            {"authentication.dkim_pass": True}, email, {}, CLASSIFICATIONS,
        ) is True
        assert _conditions_match(
            {"authentication.spf_pass": True}, email, {}, CLASSIFICATIONS,
        ) is False

    def test_is_noreply_condition(self):
        noreply = _MockEmail(from_address="noreply@service.com")
        assert _conditions_match({"is_noreply": True}, noreply, {}, CLASSIFICATIONS) is True

        human = _MockEmail(from_address="alice@example.com")
        assert _conditions_match({"is_noreply": True}, human, {}, CLASSIFICATIONS) is False

    def test_mixed_deterministic_and_classification(self):
        email = _MockEmail(from_address="user@work.com")
        result = {"human": 0.9, "requires_response": True, "priority": "high"}
        when = {"domain": "work.com", "classification.human": 0.8}
        assert _conditions_match(when, email, result, CLASSIFICATIONS) is True

        when_fail = {"domain": "other.com", "classification.human": 0.8}
        assert _conditions_match(when_fail, email, result, CLASSIFICATIONS) is False

    def test_missing_authentication_key_returns_false(self):
        email = _MockEmail(authentication={"dkim_pass": True})
        assert _conditions_match(
            {"authentication.nonexistent": True}, email, {}, CLASSIFICATIONS,
        ) is False

    def test_is_calendar_event_condition(self):
        cal_email = _MockEmail(calendar={"start": "2026-03-01T14:00:00Z", "end": "2026-03-01T15:00:00Z", "guest_count": 3})
        assert _conditions_match({"is_calendar_event": True}, cal_email, {}, CLASSIFICATIONS) is True

        normal_email = _MockEmail()
        assert _conditions_match({"is_calendar_event": True}, normal_email, {}, CLASSIFICATIONS) is False

    def test_calendar_guest_count_condition(self):
        cal_email = _MockEmail(calendar={"start": "2026-03-01T14:00:00Z", "end": "2026-03-01T15:00:00Z", "guest_count": 3})
        assert _conditions_match({"calendar.guest_count": 3}, cal_email, {}, CLASSIFICATIONS) is True
        assert _conditions_match({"calendar.guest_count": 5}, cal_email, {}, CLASSIFICATIONS) is False

    def test_calendar_key_returns_missing_when_no_calendar(self):
        email = _MockEmail()
        assert _conditions_match({"calendar.guest_count": 3}, email, {}, CLASSIFICATIONS) is False

    def test_calendar_end_past_event_matches_lt_now(self):
        past_cal = _MockEmail(calendar={"start": "2020-01-01T14:00:00+00:00", "end": "2020-01-01T15:00:00+00:00", "guest_count": 1})
        assert _conditions_match({"calendar.end": "<now()"}, past_cal, {}, CLASSIFICATIONS) is True

    def test_calendar_end_future_event_does_not_match_lt_now(self):
        future_cal = _MockEmail(calendar={"start": "2099-01-01T14:00:00+00:00", "end": "2099-01-01T15:00:00+00:00", "guest_count": 1})
        assert _conditions_match({"calendar.end": "<now()"}, future_cal, {}, CLASSIFICATIONS) is False


# ---------------------------------------------------------------------------
# _evaluate_automations
# ---------------------------------------------------------------------------


class TestEvaluateAutomations:
    def test_matching_automation_returns_actions(self):
        email = _MockEmail()
        automations = [
            AutomationConfig(when={"classification.human": 0.8}, then=["archive"]),
        ]
        result = {"human": 0.9, "requires_response": False, "priority": "low"}
        actions = _evaluate_automations(automations, email, result, CLASSIFICATIONS)
        assert actions == ["archive"]

    def test_non_matching_automation_returns_empty(self):
        email = _MockEmail()
        automations = [
            AutomationConfig(when={"classification.human": 0.8}, then=["archive"]),
        ]
        result = {"human": 0.3, "requires_response": False, "priority": "low"}
        actions = _evaluate_automations(automations, email, result, CLASSIFICATIONS)
        assert actions == []

    def test_multiple_matching_automations_combine_actions(self):
        email = _MockEmail()
        automations = [
            AutomationConfig(when={"classification.human": 0.5}, then=["archive"]),
            AutomationConfig(
                when={"classification.requires_response": True},
                then=[{"draft_reply": "noted"}],
            ),
        ]
        result = {"human": 0.9, "requires_response": True, "priority": "low"}
        actions = _evaluate_automations(automations, email, result, CLASSIFICATIONS)
        assert "archive" in actions
        assert {"draft_reply": "noted"} in actions

    def test_no_automations_returns_empty(self):
        email = _MockEmail()
        result = {"human": 0.9, "requires_response": True, "priority": "high"}
        actions = _evaluate_automations([], email, result, CLASSIFICATIONS)
        assert actions == []

    def test_deterministic_automation(self):
        email = _MockEmail(from_address="noreply@spam.com")
        automations = [
            AutomationConfig(when={"is_noreply": True}, then=["archive"]),
        ]
        actions = _evaluate_automations(automations, email, {}, CLASSIFICATIONS)
        assert actions == ["archive"]


# ---------------------------------------------------------------------------
# _build_schema
# ---------------------------------------------------------------------------


class TestBuildSchema:
    def test_confidence_schema(self):
        cls = {"human": CONFIDENCE_CLS}
        schema = _build_schema(cls)
        assert schema["properties"]["human"] == {"type": "number"}
        assert "human" in schema["required"]

    def test_boolean_schema(self):
        cls = {"flag": BOOLEAN_CLS}
        schema = _build_schema(cls)
        assert schema["properties"]["flag"] == {"type": "boolean"}

    def test_enum_schema(self):
        cls = {"priority": ENUM_CLS}
        schema = _build_schema(cls)
        assert schema["properties"]["priority"] == {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        }

    def test_mixed_schema(self):
        schema = _build_schema(CLASSIFICATIONS)
        assert len(schema["properties"]) == 3
        assert len(schema["required"]) == 3
