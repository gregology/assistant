"""Tests for the integration loader: discovery, manifest parsing,
dynamic model construction, and module loading."""

from pathlib import Path
from typing import get_origin

import pytest
import yaml

from app.loader import (
    _load_manifest,
    check_dependencies,
    discover_integrations,
    load_const_module,
    load_platform_const_module,
)
from app.config import (
    BaseIntegrationConfig,
    ClassificationConfig,
    build_integration_model,
    build_integration_union,
    load_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def custom_dir(tmp_path):
    """Create a custom integrations directory with a mock integration."""
    integration_dir = tmp_path / "mock_thing"
    integration_dir.mkdir()

    manifest = {
        "domain": "mock_thing",
        "name": "Mock Thing",
        "version": "1.0.0",
        "entry_task": "check",
        "dependencies": [],
        "config_schema": {
            "properties": {
                "api_url": {"type": "string"},
                "api_key": {"type": "string"},
                "polling_limit": {"type": "integer", "default": 50},
                "enabled": {"type": "boolean", "default": True},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["api_url", "api_key"],
        },
    }
    (integration_dir / "manifest.yaml").write_text(yaml.dump(manifest))

    (integration_dir / "__init__.py").write_text(
        "HANDLERS = {'check': lambda task: None}\n"
    )

    return tmp_path


@pytest.fixture
def builtin_dir():
    """The real built-in integrations directory."""
    return Path(__file__).parent.parent / "app" / "integrations"


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_valid_manifest(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        assert manifest is not None
        assert manifest.domain == "mock_thing"
        assert manifest.name == "Mock Thing"
        assert manifest.version == "1.0.0"
        assert manifest.entry_task == "check"
        assert manifest.dependencies == []
        assert manifest.builtin is False

    def test_missing_domain_returns_none(self, tmp_path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "manifest.yaml").write_text("name: No Domain\n")
        assert _load_manifest(d, builtin=False) is None

    def test_domain_mismatch_returns_none(self, tmp_path):
        d = tmp_path / "wrong_name"
        d.mkdir()
        (d / "manifest.yaml").write_text("domain: different_name\nname: X\n")
        assert _load_manifest(d, builtin=False) is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        d = tmp_path / "bad_yaml"
        d.mkdir()
        (d / "manifest.yaml").write_text(":::invalid:::\n  - [")
        assert _load_manifest(d, builtin=False) is None

    def test_defaults_for_optional_fields(self, tmp_path):
        d = tmp_path / "minimal"
        d.mkdir()
        (d / "manifest.yaml").write_text("domain: minimal\n")
        manifest = _load_manifest(d, builtin=False)
        assert manifest is not None
        assert manifest.name == "minimal"
        assert manifest.version == "0.0.0"
        assert manifest.entry_task == "check"
        assert manifest.dependencies == []
        assert manifest.config_schema == {}

    def test_builtin_flag(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=True)
        assert manifest is not None
        assert manifest.builtin is True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscoverIntegrations:
    def test_discovers_installed_integrations(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        assert "email" in manifests
        assert "github" in manifests
        # Email and github are installed as entry-point packages
        assert manifests["email"].entry_point_module is not None
        assert manifests["github"].entry_point_module is not None

    def test_discovers_custom_integrations(self, builtin_dir, custom_dir):
        manifests = discover_integrations(builtin_dir, custom_dir)
        assert "mock_thing" in manifests
        assert manifests["mock_thing"].builtin is False
        # Built-ins still present
        assert "email" in manifests

    def test_custom_shadows_builtin(self, builtin_dir, tmp_path):
        """A custom integration with the same domain shadows the built-in."""
        shadow_dir = tmp_path / "email"
        shadow_dir.mkdir()
        (shadow_dir / "manifest.yaml").write_text(
            "domain: email\nname: Custom Email\nversion: 2.0.0\n"
        )
        (shadow_dir / "__init__.py").write_text("HANDLERS = {}\n")

        manifests = discover_integrations(builtin_dir, tmp_path)
        assert manifests["email"].name == "Custom Email"
        assert manifests["email"].builtin is False

    def test_skips_dirs_without_manifest(self, builtin_dir, tmp_path):
        (tmp_path / "no_manifest").mkdir()
        manifests = discover_integrations(builtin_dir, tmp_path)
        assert "no_manifest" not in manifests

    def test_nonexistent_custom_dir(self, builtin_dir):
        manifests = discover_integrations(
            builtin_dir, Path("/nonexistent/path")
        )
        assert "email" in manifests

    def test_no_builtin_dir(self, tmp_path, custom_dir):
        manifests = discover_integrations(
            tmp_path / "nonexistent", custom_dir
        )
        assert "mock_thing" in manifests
        # Entry-point integrations are still discovered even without builtin dir
        assert "email" in manifests


# ---------------------------------------------------------------------------
# Dynamic model construction
# ---------------------------------------------------------------------------


class TestBuildIntegrationModel:
    def test_creates_model_with_required_fields(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        Model = build_integration_model(manifest)

        assert issubclass(Model, BaseIntegrationConfig)

        # Required fields — should fail without them
        with pytest.raises((ValueError, TypeError)):
            Model(type="mock_thing", name="test")

        # Should succeed with required fields
        instance = Model(
            type="mock_thing",
            name="test",
            api_url="https://example.com",
            api_key="secret",
        )
        assert instance.api_url == "https://example.com"
        assert instance.api_key == "secret"

    def test_optional_fields_have_defaults(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        Model = build_integration_model(manifest)

        instance = Model(
            type="mock_thing",
            name="test",
            api_url="https://example.com",
            api_key="secret",
        )
        assert instance.polling_limit == 50
        assert instance.enabled is True
        assert instance.tags is None

    def test_type_literal_is_set(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        Model = build_integration_model(manifest)

        instance = Model(
            type="mock_thing",
            name="test",
            api_url="https://example.com",
            api_key="secret",
        )
        assert instance.type == "mock_thing"

    def test_common_fields_present(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        Model = build_integration_model(manifest)

        instance = Model(
            type="mock_thing",
            name="test",
            api_url="https://example.com",
            api_key="secret",
            llm="fast",
        )
        assert instance.llm == "fast"
        assert instance.schedule is None


class TestBuildIntegrationUnion:
    def test_union_with_multiple_manifests(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        Union = build_integration_union(manifests)
        # Should be an Annotated type with discriminator
        assert get_origin(Union) is not None or Union is not BaseIntegrationConfig

    def test_union_with_no_manifests(self):
        Union = build_integration_union({})
        assert Union is BaseIntegrationConfig

    def test_union_validates_email_config(self, builtin_dir):
        """The dynamic email model should accept the same config as the old static one."""
        manifests = discover_integrations(builtin_dir)
        Model = build_integration_model(manifests["email"])

        instance = Model(
            type="email",
            name="personal",
            imap_server="imap.example.com",
            username="user@example.com",
            password="secret",
        )
        assert instance.imap_server == "imap.example.com"
        assert instance.imap_port == 993  # default

    def test_union_validates_email_platform_config(self, builtin_dir):
        """The inbox platform config should accept limit."""
        manifests = discover_integrations(builtin_dir)
        Model = build_integration_model(manifests["email"])

        instance = Model(
            type="email",
            name="personal",
            imap_server="imap.example.com",
            username="user@example.com",
            password="secret",
            platforms={"inbox": {"limit": 100}},
        )
        assert instance.platforms.inbox.limit == 100

    def test_union_validates_github_config(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        Model = build_integration_model(manifests["github"])

        instance = Model(
            type="github",
            name="my_repos",
            orgs=["myorg"],
        )
        assert instance.orgs == ["myorg"]

    def test_union_validates_github_platform_config(self, builtin_dir):
        """The pull_requests platform config should accept include_mentions."""
        manifests = discover_integrations(builtin_dir)
        Model = build_integration_model(manifests["github"])

        instance = Model(
            type="github",
            name="my_repos",
            platforms={"pull_requests": {"include_mentions": True}},
        )
        assert instance.platforms.pull_requests.include_mentions is True


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------


class TestCheckDependencies:
    def test_no_dependencies(self, custom_dir):
        manifest = _load_manifest(custom_dir / "mock_thing", builtin=False)
        assert check_dependencies(manifest) == []

    def test_installed_dependency(self, tmp_path):
        d = tmp_path / "has_dep"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "domain: has_dep\ndependencies:\n  - yaml\n"
        )
        manifest = _load_manifest(d, builtin=False)
        # yaml (pyyaml) is installed
        assert check_dependencies(manifest) == []

    def test_missing_dependency(self, tmp_path):
        d = tmp_path / "missing_dep"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "domain: missing_dep\ndependencies:\n  - nonexistent-package>=99.0\n"
        )
        manifest = _load_manifest(d, builtin=False)
        assert "nonexistent-package>=99.0" in check_dependencies(manifest)


# ---------------------------------------------------------------------------
# Const module loading
# ---------------------------------------------------------------------------


class TestLoadConstModule:
    def test_loads_email_inbox_const(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        manifest = manifests["email"]
        const = load_platform_const_module(manifest, "inbox")
        assert const is not None
        assert hasattr(const, "DETERMINISTIC_SOURCES")
        assert hasattr(const, "IRREVERSIBLE_ACTIONS")
        assert hasattr(const, "SIMPLE_ACTIONS")

    def test_loads_github_pr_const(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        manifest = manifests["github"]
        const = load_platform_const_module(manifest, "pull_requests")
        assert const is not None
        assert hasattr(const, "DETERMINISTIC_SOURCES")

    def test_returns_none_without_const(self, tmp_path):
        d = tmp_path / "no_const"
        d.mkdir()
        (d / "manifest.yaml").write_text("domain: no_const\n")
        (d / "__init__.py").write_text("HANDLERS = {}\n")
        manifest = _load_manifest(d, builtin=False)
        assert load_const_module(manifest) is None

    def test_returns_none_for_nonexistent_platform(self, builtin_dir):
        manifests = discover_integrations(builtin_dir)
        manifest = manifests["email"]
        assert load_platform_const_module(manifest, "nonexistent") is None


# ---------------------------------------------------------------------------
# Full config loading with dynamic models
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_loads_with_builtin_integrations(self, tmp_path):
        """A minimal config with an email integration loads correctly."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            "integrations:\n"
            "  - type: email\n"
            "    name: test\n"
            "    imap_server: imap.test.com\n"
            "    username: user@test.com\n"
            "    password: secret\n"
        )
        cfg, _warnings = load_config(config_path)
        assert len(cfg.integrations) == 1
        assert cfg.integrations[0].type == "email"
        assert cfg.integrations[0].imap_server == "imap.test.com"

    def test_unknown_integration_type_rejected(self, tmp_path):
        """Config with an unknown integration type raises a validation error."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            "integrations:\n"
            "  - type: nonexistent\n"
            "    name: test\n"
        )
        with pytest.raises((ValueError, TypeError)):
            load_config(config_path)

    def test_missing_required_field_rejected(self, tmp_path):
        """Config missing a required field raises a validation error."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            "integrations:\n"
            "  - type: email\n"
            "    name: test\n"
            "    username: user@test.com\n"
            "    password: secret\n"
            # Missing imap_server
        )
        with pytest.raises((ValueError, TypeError)):
            load_config(config_path)

    def test_custom_integrations_directory(self, tmp_path):
        """Config with custom_integrations directory discovers external integrations."""
        # Create a custom integration
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        ext_dir = custom_dir / "test_ext"
        ext_dir.mkdir()
        manifest = {
            "domain": "test_ext",
            "name": "Test External",
            "version": "1.0.0",
            "entry_task": "check",
            "dependencies": [],
            "config_schema": {
                "properties": {
                    "api_url": {"type": "string"},
                },
                "required": ["api_url"],
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (ext_dir / "__init__.py").write_text(
            "HANDLERS = {'check': lambda task: None}\n"
        )

        # Create config referencing the custom directory
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            f"directories:\n"
            f"  custom_integrations: {custom_dir}\n"
            "integrations:\n"
            "  - type: test_ext\n"
            "    name: my_ext\n"
            "    api_url: https://example.com\n"
        )
        cfg, _warnings = load_config(config_path)
        assert len(cfg.integrations) == 1
        assert cfg.integrations[0].type == "test_ext"
        assert cfg.integrations[0].api_url == "https://example.com"

    def test_classification_shorthand_works(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            "integrations:\n"
            "  - type: email\n"
            "    name: test\n"
            "    imap_server: imap.test.com\n"
            "    username: user@test.com\n"
            "    password: secret\n"
            "    platforms:\n"
            "      inbox:\n"
            "        classifications:\n"
            "          human: is this from a human?\n"
        )
        cfg, _ = load_config(config_path)
        cls = cfg.integrations[0].platforms.inbox.classifications["human"]
        assert isinstance(cls, ClassificationConfig)
        assert cls.prompt == "is this from a human?"

    def test_duplicate_names_rejected(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "llms:\n"
            "  default:\n"
            "    model: test-model\n"
            "integrations:\n"
            "  - type: email\n"
            "    name: same\n"
            "    imap_server: a.com\n"
            "    username: a@a.com\n"
            "    password: x\n"
            "  - type: email\n"
            "    name: same\n"
            "    imap_server: b.com\n"
            "    username: b@b.com\n"
            "    password: y\n"
        )
        with pytest.raises((ValueError, TypeError)):
            load_config(config_path)
