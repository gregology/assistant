from app.config import AutomationConfig, ClassificationConfig, DictAction, SimpleAction, YoloAction
from app.ui.presenters import (
    QueueCounts,
    _format_action,
    _present_automation,
    _present_classification,
    mask_value,
)


class TestMaskValue:
    def test_known_secret_masked(self):
        secrets = frozenset({"hunter2", "s3cret"})
        assert mask_value("any_field", "hunter2", secrets) == "********"

    def test_sensitive_field_name_masked(self):
        assert mask_value("password", "cleartext", frozenset()) == "********"
        assert mask_value("api_key", "abc123", frozenset()) == "********"
        assert mask_value("token", "tok_xyz", frozenset()) == "********"
        assert mask_value("my_secret", "val", frozenset()) == "********"

    def test_normal_value_passes_through(self):
        assert mask_value("base_url", "http://localhost", frozenset()) == "http://localhost"

    def test_non_string_converted(self):
        assert mask_value("port", 993, frozenset()) == "993"


class TestPresentClassification:
    def test_confidence_type(self):
        cfg = ClassificationConfig(prompt="is this human?")
        view = _present_classification("human", cfg)
        assert view.name == "human"
        assert view.type == "confidence"
        assert view.values is None
        assert "human" in view.prompt

    def test_boolean_type(self):
        cfg = ClassificationConfig(prompt="requires response?", type="boolean")
        view = _present_classification("requires_response", cfg)
        assert view.type == "boolean"

    def test_enum_type(self):
        cfg = ClassificationConfig(prompt="priority?", type="enum", values=["low", "high"])
        view = _present_classification("priority", cfg)
        assert view.type == "enum"
        assert view.values == ["low", "high"]


class TestPresentAutomation:
    def test_rule_provenance(self):
        auto = AutomationConfig(when={"domain": "github.com"}, then=["archive"])
        det = frozenset({"domain"})
        view = _present_automation(auto, "email", "inbox", det)
        assert view.provenance == "rule"
        assert not view.yolo

    def test_llm_provenance(self):
        auto = AutomationConfig(when={"classification.human": 0.8}, then=["archive"])
        det = frozenset({"domain"})
        view = _present_automation(auto, "email", "inbox", det)
        assert view.provenance == "llm"

    def test_hybrid_provenance(self):
        auto = AutomationConfig(
            when={"domain": "x.com", "classification.robot": 0.9},
            then=["archive"],
        )
        det = frozenset({"domain"})
        view = _present_automation(auto, "email", "inbox", det)
        assert view.provenance == "hybrid"

    def test_yolo_detected(self):
        auto = AutomationConfig(
            when={"classification.robot": 0.9},
            then=[YoloAction("unsubscribe")],
        )
        det = frozenset({"domain"})
        view = _present_automation(auto, "email", "inbox", det)
        assert view.yolo
        assert "!yolo" in view.then[0]


class TestFormatAction:
    def test_string_action(self):
        assert _format_action(SimpleAction(action="archive")) == "archive"

    def test_dict_action(self):
        result = _format_action(DictAction(data={"draft_reply": "hi"}))
        assert "draft_reply" in result

    def test_yolo_string(self):
        result = _format_action(YoloAction("unsubscribe"))
        assert "!yolo" in result
        assert "unsubscribe" in result

    def test_yolo_dict(self):
        result = _format_action(YoloAction({"script": {"name": "foo"}}))
        assert "!yolo" in result


class TestQueueCounts:
    def test_total_computed(self):
        q = QueueCounts(pending=2, active=1, done=5, failed=1)
        assert q.total == 9

    def test_counts_from_fixture(self, queue_dir):
        from app.ui.presenters import _get_queue_counts

        counts = _get_queue_counts()
        assert counts.pending == 0
        assert counts.total == 0

    def test_counts_with_tasks(self, queue_dir):
        import yaml as _yaml

        from app.ui.presenters import _get_queue_counts

        task = {"id": "test", "status": "pending", "payload": {"type": "test"}}
        (queue_dir / "pending" / "test.yaml").write_text(_yaml.dump(task, default_flow_style=False))
        counts = _get_queue_counts()
        assert counts.pending == 1
        assert counts.total == 1
