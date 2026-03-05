"""Tests for the YAML round-trip read/write layer.

All tests use tmp_path with fixture config files. No test touches the real config.yaml.
"""

from __future__ import annotations

import pytest

from app.ui.yaml_rw import (
    ConfigValidationError,
    delete_llm_profile,
    delete_script,
    is_dirty,
    read_config,
    save_raw_yaml,
    update_directories,
    update_llm_profile,
    update_script,
    write_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = """\
# Top-level comment
llms:
  default:
    base_url: http://localhost:11434  # inline comment
    model: test-model
    parameters:
      temperature: 0.7
  fast:
    base_url: http://gaming:11434
    model: fast-model
directories:
  task_queue: data/queue
  logs: logs
scripts:
  greet:
    description: "Say hello"
    shell: "echo hello"
    timeout: 60
"""

_SECRET_CONFIG = """\
llms:
  default:
    base_url: http://localhost:11434
    model: test-model
    token: !secret my_token
directories:
  task_queue: data/queue
  logs: logs
"""

_YOLO_CONFIG = """\
llms:
  default:
    base_url: http://localhost:11434
    model: test-model
integrations:
  - type: email
    name: personal
    imap_server: imap.example.com
    imap_port: 993
    username: test@example.com
    password: !secret email_pass
    platforms:
      inbox:
        limit: 50
        automations:
          - when:
              classification.robot: "> 0.95"
            then:
              - !yolo unsubscribe
directories:
  task_queue: data/queue
  logs: logs
"""


@pytest.fixture
def config_file(tmp_path):
    """Create a temp config.yaml + secrets.yaml, return the config path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MINIMAL_CONFIG)
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("my_token: secret123\nemail_pass: pass456\n")
    return cfg


@pytest.fixture
def secret_config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_SECRET_CONFIG)
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("my_token: secret123\n")
    return cfg


@pytest.fixture
def yolo_config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_YOLO_CONFIG)
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("email_pass: pass456\n")
    return cfg


@pytest.fixture(autouse=True)
def _reset_dirty():
    """Reset the dirty flag before each test."""
    import app.ui.yaml_rw as mod

    mod._config_dirty = False
    yield
    mod._config_dirty = False


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_comments_preserved(self, config_file):
        data = read_config(config_file)
        write_config(data, config_file)
        content = config_file.read_text()
        assert "# Top-level comment" in content
        assert "# inline comment" in content

    def test_key_order_preserved(self, config_file):
        data = read_config(config_file)
        write_config(data, config_file)
        content = config_file.read_text()
        llms_pos = content.index("llms:")
        dirs_pos = content.index("directories:")
        scripts_pos = content.index("scripts:")
        assert llms_pos < dirs_pos < scripts_pos

    def test_block_style_preserved(self, config_file):
        data = read_config(config_file)
        write_config(data, config_file)
        content = config_file.read_text()
        assert "base_url: http://localhost:11434" in content


# ---------------------------------------------------------------------------
# Secret preservation
# ---------------------------------------------------------------------------


class TestSecretPreservation:
    def test_secret_tag_survives_roundtrip(self, secret_config_file):
        data = read_config(secret_config_file)
        # Make an unrelated edit
        data["llms"]["default"]["model"] = "new-model"
        write_config(data, secret_config_file)
        content = secret_config_file.read_text()
        assert "!secret my_token" in content

    def test_secret_not_resolved_during_roundtrip(self, secret_config_file):
        data = read_config(secret_config_file)
        write_config(data, secret_config_file)
        content = secret_config_file.read_text()
        assert "secret123" not in content
        assert "!secret my_token" in content


# ---------------------------------------------------------------------------
# Yolo preservation
# ---------------------------------------------------------------------------


class TestYoloPreservation:
    def test_yolo_scalar_survives_roundtrip(self, yolo_config_file):
        data = read_config(yolo_config_file)
        write_config(data, yolo_config_file)
        content = yolo_config_file.read_text()
        assert "!yolo" in content
        assert "unsubscribe" in content


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_config_not_written(self, config_file):
        original = config_file.read_text()
        with pytest.raises(ConfigValidationError):
            # llms must be a dict of LLMConfig — empty string is invalid
            save_raw_yaml("llms: invalid", config_file)
        assert config_file.read_text() == original

    def test_valid_config_accepted(self, config_file):
        yaml_str = (
            "llms:\n"
            "  default:\n"
            "    model: new-model\n"
            "directories:\n"
            "  task_queue: data/queue\n"
            "  logs: logs\n"
        )
        save_raw_yaml(yaml_str, config_file)
        data = read_config(config_file)
        assert data["llms"]["default"]["model"] == "new-model"

    def test_non_yaml_rejected(self, config_file):
        original = config_file.read_text()
        with pytest.raises(ConfigValidationError):
            save_raw_yaml("not: [valid: yaml: {{", config_file)
        assert config_file.read_text() == original

    def test_non_mapping_rejected(self, config_file):
        original = config_file.read_text()
        with pytest.raises(ConfigValidationError):
            save_raw_yaml("[1, 2, 3]", config_file)
        assert config_file.read_text() == original


# ---------------------------------------------------------------------------
# Section updates
# ---------------------------------------------------------------------------


class TestSectionUpdates:
    def test_update_llm_profile(self, config_file):
        update_llm_profile("default", {"model": "updated-model"}, config_file)
        data = read_config(config_file)
        assert data["llms"]["default"]["model"] == "updated-model"

    def test_create_llm_profile(self, config_file):
        update_llm_profile("new_profile", {"model": "new-model"}, config_file)
        data = read_config(config_file)
        assert "new_profile" in data["llms"]
        assert data["llms"]["new_profile"]["model"] == "new-model"

    def test_delete_llm_profile(self, config_file):
        delete_llm_profile("fast", config_file)
        data = read_config(config_file)
        assert "fast" not in data["llms"]
        assert "default" in data["llms"]

    def test_delete_last_llm_profile_refused(self, config_file):
        delete_llm_profile("fast", config_file)
        with pytest.raises(ConfigValidationError, match="last LLM profile"):
            delete_llm_profile("default", config_file)

    def test_delete_missing_llm_profile(self, config_file):
        with pytest.raises(ConfigValidationError, match="not found"):
            delete_llm_profile("nonexistent", config_file)

    def test_update_directories(self, config_file):
        update_directories({"logs": "/new/logs"}, config_file)
        data = read_config(config_file)
        assert data["directories"]["logs"] == "/new/logs"

    def test_update_directories_remove_optional(self, config_file):
        update_directories({"notes": "/some/path"}, config_file)
        data = read_config(config_file)
        assert data["directories"]["notes"] == "/some/path"
        update_directories({"notes": ""}, config_file)
        data = read_config(config_file)
        assert "notes" not in data["directories"]

    def test_update_script(self, config_file):
        update_script("greet", {"description": "Updated greeting"}, config_file)
        data = read_config(config_file)
        assert data["scripts"]["greet"]["description"] == "Updated greeting"

    def test_create_script(self, config_file):
        update_script(
            "new_script",
            {"shell": "echo new", "description": "A new script"},
            config_file,
        )
        data = read_config(config_file)
        assert "new_script" in data["scripts"]
        assert data["scripts"]["new_script"]["shell"] == "echo new"

    def test_delete_script(self, config_file):
        delete_script("greet", config_file)
        data = read_config(config_file)
        assert "greet" not in data["scripts"]

    def test_delete_missing_script(self, config_file):
        with pytest.raises(ConfigValidationError, match="not found"):
            delete_script("nonexistent", config_file)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


class TestBackup:
    def test_backup_created(self, config_file):
        original = config_file.read_text()
        update_llm_profile("default", {"model": "new"}, config_file)
        backup = config_file.with_suffix(".yaml.bak")
        assert backup.exists()
        assert backup.read_text() == original

    def test_backup_updated_on_second_write(self, config_file):
        update_llm_profile("default", {"model": "first"}, config_file)
        after_first = config_file.read_text()
        update_llm_profile("default", {"model": "second"}, config_file)
        backup = config_file.with_suffix(".yaml.bak")
        assert backup.read_text() == after_first


# ---------------------------------------------------------------------------
# Dirty flag
# ---------------------------------------------------------------------------


class TestDirtyFlag:
    def test_starts_clean(self):
        assert not is_dirty()

    def test_dirty_after_write(self, config_file):
        update_llm_profile("default", {"model": "new"}, config_file)
        assert is_dirty()

    def test_read_does_not_dirty(self, config_file):
        read_config(config_file)
        assert not is_dirty()
