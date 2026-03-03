import yaml

from app.config import ScriptConfig, YoloAction, _Loader
from app.evaluate import unwrap_actions


class TestYoloTag:
    def test_yolo_produces_yolo_action(self):
        raw = yaml.load("action: !yolo unsubscribe", Loader=_Loader)
        assert isinstance(raw["action"], YoloAction)
        assert raw["action"].value == "unsubscribe"

    def test_yolo_in_list(self):
        raw = yaml.load(
            "actions:\n  - !yolo unsubscribe\n  - archive",
            Loader=_Loader,
        )
        assert isinstance(raw["actions"][0], YoloAction)
        assert raw["actions"][0].value == "unsubscribe"
        assert raw["actions"][1] == "archive"

    def test_yolo_equality(self):
        a = YoloAction("unsubscribe")
        b = YoloAction("unsubscribe")
        c = YoloAction("other")
        assert a == b
        assert a != c

    def test_yolo_hash(self):
        a = YoloAction("unsubscribe")
        b = YoloAction("unsubscribe")
        assert hash(a) == hash(b)
        assert {a, b} == {a}

    def test_yolo_on_mapping(self):
        raw = yaml.load(
            "action: !yolo\n  script:\n    name: test\n    inputs:\n      domain: '{{ domain }}'",
            Loader=_Loader,
        )
        assert isinstance(raw["action"], YoloAction)
        assert isinstance(raw["action"].value, dict)
        assert raw["action"].value == {"script": {"name": "test", "inputs": {"domain": "{{ domain }}"}}}

    def test_yolo_on_mapping_unwrap(self):
        action = YoloAction({"script": {"name": "test"}})
        result = unwrap_actions([action])
        assert result == [{"script": {"name": "test"}}]

    def test_yolo_mapping_equality(self):
        a = YoloAction({"script": {"name": "test"}})
        b = YoloAction({"script": {"name": "test"}})
        assert a == b

    def test_yolo_mapping_hash(self):
        a = YoloAction({"script": {"name": "test"}})
        b = YoloAction({"script": {"name": "test"}})
        assert hash(a) == hash(b)


class TestScriptConfig:
    def test_script_config_parsing(self):
        script = ScriptConfig(
            description="Test script",
            inputs=["domain"],
            timeout=300,
            shell="echo hello",
            output="OUTPUT",
            on_output="human_log",
            reversible=False,
        )
        assert script.description == "Test script"
        assert script.inputs == ["domain"]
        assert script.timeout == 300
        assert script.shell == "echo hello"
        assert script.output == "OUTPUT"
        assert script.on_output == "human_log"
        assert script.reversible is False

    def test_script_config_defaults(self):
        script = ScriptConfig(shell="echo hello")
        assert script.description == ""
        assert script.inputs == []
        assert script.timeout == 120
        assert script.output is None
        assert script.on_output == "human_log"
        assert script.reversible is False
