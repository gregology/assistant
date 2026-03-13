"""Tests for assistant_sdk.models — Pydantic config models and YoloAction."""

import pytest
from pydantic import ValidationError

from assistant_sdk.models import (
    AutomationConfig,
    BaseIntegrationConfig,
    BasePlatformConfig,
    ClassificationConfig,
    DictAction,
    ScriptConfig,
    ScheduleConfig,
    SimpleAction,
    YoloAction,
)


# ---------------------------------------------------------------------------
# YoloAction
# ---------------------------------------------------------------------------


class TestYoloAction:
    def test_wraps_string_value(self):
        y = YoloAction("unsubscribe")
        assert y.value == "unsubscribe"

    def test_wraps_dict_value(self):
        d = {"script": {"name": "nuke"}}
        y = YoloAction(d)
        assert y.value == d

    def test_repr(self):
        assert "unsubscribe" in repr(YoloAction("unsubscribe"))

    def test_equality_same_value(self):
        assert YoloAction("x") == YoloAction("x")

    def test_equality_different_value(self):
        assert YoloAction("x") != YoloAction("y")

    def test_equality_non_yolo(self):
        assert YoloAction("x") != "x"

    def test_hashable(self):
        s = {YoloAction("a"), YoloAction("a"), YoloAction("b")}
        assert len(s) == 2

    def test_dict_equality(self):
        d = {"script": "run"}
        assert YoloAction(d) == YoloAction(d)


# ---------------------------------------------------------------------------
# ClassificationConfig
# ---------------------------------------------------------------------------


class TestClassificationConfig:
    def test_confidence_default(self):
        c = ClassificationConfig(prompt="test")
        assert c.type == "confidence"
        assert c.values is None

    def test_boolean_type(self):
        c = ClassificationConfig(prompt="test", type="boolean")
        assert c.type == "boolean"

    def test_enum_requires_values(self):
        with pytest.raises(ValidationError, match="values"):
            ClassificationConfig(prompt="test", type="enum")

    def test_enum_with_values(self):
        c = ClassificationConfig(prompt="test", type="enum", values=["a", "b"])
        assert c.values == ["a", "b"]

    def test_confidence_rejects_values(self):
        with pytest.raises(ValidationError, match="values"):
            ClassificationConfig(prompt="test", type="confidence", values=["a"])

    def test_boolean_rejects_values(self):
        with pytest.raises(ValidationError, match="values"):
            ClassificationConfig(prompt="test", type="boolean", values=["a"])


# ---------------------------------------------------------------------------
# AutomationConfig
# ---------------------------------------------------------------------------


class TestAutomationConfig:
    def test_single_string_then_normalizes_to_list(self):
        a = AutomationConfig(when={"domain": "x"}, then="archive")
        assert a.then == [SimpleAction(action="archive")]

    def test_single_dict_then_normalizes_to_list(self):
        a = AutomationConfig(when={"domain": "x"}, then={"draft_reply": "hi"})
        assert a.then == [DictAction(data={"draft_reply": "hi"})]

    def test_list_then_preserved(self):
        a = AutomationConfig(when={"domain": "x"}, then=["archive", "spam"])
        assert a.then == [SimpleAction(action="archive"), SimpleAction(action="spam")]

    def test_yolo_action_in_then(self):
        y = YoloAction("unsubscribe")
        a = AutomationConfig(when={"domain": "x"}, then=[y])
        assert isinstance(a.then[0], YoloAction)

    def test_single_yolo_normalizes_to_list(self):
        y = YoloAction("unsubscribe")
        a = AutomationConfig(when={"domain": "x"}, then=y)
        assert a.then == [y]


# ---------------------------------------------------------------------------
# BasePlatformConfig
# ---------------------------------------------------------------------------


class TestBasePlatformConfig:
    def test_string_classification_shorthand(self):
        cfg = BasePlatformConfig(
            classifications={"urgency": "how urgent is this?"},
        )
        assert cfg.classifications["urgency"].prompt == "how urgent is this?"
        assert cfg.classifications["urgency"].type == "confidence"

    def test_dict_classification_preserved(self):
        cfg = BasePlatformConfig(
            classifications={
                "prio": {"prompt": "priority?", "type": "enum", "values": ["low", "high"]},
            },
        )
        assert cfg.classifications["prio"].type == "enum"
        assert cfg.classifications["prio"].values == ["low", "high"]

    def test_defaults_empty(self):
        cfg = BasePlatformConfig()
        assert cfg.classifications == {}
        assert cfg.automations == []


# ---------------------------------------------------------------------------
# BaseIntegrationConfig
# ---------------------------------------------------------------------------


class TestBaseIntegrationConfig:
    def test_composite_id(self):
        cfg = BaseIntegrationConfig(type="email", name="personal")
        assert cfg.id == "email.personal"

    def test_default_llm(self):
        cfg = BaseIntegrationConfig(type="github", name="work")
        assert cfg.llm == "default"

    def test_schedule_none_by_default(self):
        cfg = BaseIntegrationConfig(type="email", name="test")
        assert cfg.schedule is None


# ---------------------------------------------------------------------------
# ScheduleConfig
# ---------------------------------------------------------------------------


class TestScheduleConfig:
    def test_every(self):
        s = ScheduleConfig(every="30m")
        assert s.every == "30m"
        assert s.cron is None

    def test_cron(self):
        s = ScheduleConfig(cron="0 8 * * *")
        assert s.cron == "0 8 * * *"
        assert s.every is None


# ---------------------------------------------------------------------------
# ScriptConfig
# ---------------------------------------------------------------------------


class TestScriptConfig:
    def test_defaults(self):
        s = ScriptConfig(shell="echo hello")
        assert s.timeout == 120
        assert s.reversible is False
        assert s.on_output == "human_log"
        assert s.inputs == []

    def test_reversible_override(self):
        s = ScriptConfig(shell="echo hello", reversible=True)
        assert s.reversible is True
