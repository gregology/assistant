"""Tests for assistant_sdk.manifest — manifest dataclasses."""

from pathlib import Path

from assistant_sdk.manifest import IntegrationManifest, PlatformManifest, ServiceManifest


class TestServiceManifest:
    def test_defaults(self):
        s = ServiceManifest(name="search", description="Web search", handler=".services.search")
        assert s.reversible is False
        assert s.input_schema == {}

    def test_reversible(self):
        s = ServiceManifest(
            name="search", description="Web search",
            handler=".services.search", reversible=True,
        )
        assert s.reversible is True


class TestPlatformManifest:
    def test_construction(self):
        p = PlatformManifest(
            name="Pull Requests",
            entry_task="check",
            config_schema={"properties": {}},
        )
        assert p.name == "Pull Requests"
        assert p.entry_task == "check"
        assert p.handlers == {}


class TestIntegrationManifest:
    def test_construction(self):
        m = IntegrationManifest(
            domain="github",
            name="GitHub",
            version="1.0.0",
            entry_task="check",
            dependencies=[],
            config_schema={},
            platforms={},
            path=Path("/test"),
            builtin=True,
        )
        assert m.domain == "github"
        assert m.builtin is True
        assert m.services == {}
        assert m.handlers == {}
        assert m.entry_point_module is None

    def test_with_services(self):
        svc = ServiceManifest(name="s1", description="d", handler=".h")
        m = IntegrationManifest(
            domain="gemini", name="Gemini", version="1.0.0",
            entry_task="check", dependencies=[], config_schema={},
            platforms={}, path=Path("/test"), builtin=False,
            services={"s1": svc},
        )
        assert "s1" in m.services
        assert m.services["s1"].name == "s1"
