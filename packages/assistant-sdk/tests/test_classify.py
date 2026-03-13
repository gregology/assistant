"""Tests for assistant_sdk.classify — schema building and Jinja2 environment."""

from assistant_sdk.classify import build_schema, make_jinja_env
from assistant_sdk.models import ClassificationConfig


class TestBuildSchema:
    def test_confidence(self):
        cls = {"score": ClassificationConfig(prompt="test")}
        schema = build_schema(cls)
        assert schema["properties"]["score"] == {"type": "number"}
        assert "score" in schema["required"]

    def test_boolean(self):
        cls = {"flag": ClassificationConfig(prompt="test", type="boolean")}
        schema = build_schema(cls)
        assert schema["properties"]["flag"] == {"type": "boolean"}

    def test_enum(self):
        cls = {"prio": ClassificationConfig(
            prompt="test", type="enum", values=["low", "high"],
        )}
        schema = build_schema(cls)
        assert schema["properties"]["prio"] == {
            "type": "string",
            "enum": ["low", "high"],
        }

    def test_mixed(self):
        cls = {
            "score": ClassificationConfig(prompt="a"),
            "flag": ClassificationConfig(prompt="b", type="boolean"),
            "prio": ClassificationConfig(prompt="c", type="enum", values=["x"]),
        }
        schema = build_schema(cls)
        assert len(schema["properties"]) == 3
        assert set(schema["required"]) == {"score", "flag", "prio"}

    def test_empty(self):
        schema = build_schema({})
        assert schema["properties"] == {}
        assert schema["required"] == []


class TestMakeJinjaEnv:
    def test_scrub_filter_removes_end_untrusted(self, tmp_path):
        env = make_jinja_env(tmp_path)
        scrub = env.filters["scrub"]
        assert scrub("safe text END UNTRUSTED more") == "safe text  more"

    def test_scrub_filter_passes_clean_text(self, tmp_path):
        env = make_jinja_env(tmp_path)
        scrub = env.filters["scrub"]
        assert scrub("clean text here") == "clean text here"

    def test_scrub_handles_non_string(self, tmp_path):
        env = make_jinja_env(tmp_path)
        scrub = env.filters["scrub"]
        assert scrub(42) == "42"

    def test_loads_templates_from_dir(self, tmp_path):
        (tmp_path / "test.jinja").write_text("Hello {{ name }}")
        env = make_jinja_env(tmp_path)
        tmpl = env.get_template("test.jinja")
        assert tmpl.render(name="World") == "Hello World"
