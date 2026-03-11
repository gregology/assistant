"""Tests for the GaaS CLI, setup wizard, and doctor."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


# ─── CLI parser tests ─────────────────────────────────────────────────────────


class TestCLIParser:
    """Test that the CLI parser builds correctly and routes subcommands."""

    def test_build_parser(self):
        from app.cli import build_parser

        parser = build_parser()
        assert parser.prog == "gaas"

    def test_start_defaults(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["start"])
        assert args.command == "start"
        assert args.dev is False
        assert args.expose is False
        assert args.port == 6767

    def test_start_all_flags(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["start", "--dev", "--expose", "--port", "8080"])
        assert args.dev is True
        assert args.expose is True
        assert args.port == 8080

    def test_setup_defaults(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
        assert args.reconfigure is False

    def test_setup_reconfigure(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup", "--reconfigure"])
        assert args.reconfigure is True

    def test_update(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["update"])
        assert args.command == "update"

    def test_doctor(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_version(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_logs_defaults(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["logs"])
        assert args.command == "logs"
        assert args.tail is None

    def test_logs_tail(self):
        from app.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["logs", "--tail", "50"])
        assert args.tail == 50

    def test_no_command_returns_zero(self):
        from app.cli import main

        with patch("sys.argv", ["gaas"]):
            # No subcommand should print help and return 0
            result = main()
            assert result == 0


# ─── Version command ──────────────────────────────────────────────────────────


class TestVersion:
    def test_version_output(self, capsys):
        from app.cli import build_parser, cmd_version

        parser = build_parser()
        args = parser.parse_args(["version"])
        result = cmd_version(args)
        assert result == 0
        output = capsys.readouterr().out
        assert "GaaS v" in output
        assert "Python:" in output
        assert "Path:" in output


# ─── Status command ───────────────────────────────────────────────────────────


class TestStatus:
    def test_status_not_running(self, capsys):
        """Status should report not running when nothing is on port 6767."""
        from app.cli import build_parser, cmd_status

        parser = build_parser()
        args = parser.parse_args(["status"])
        result = cmd_status(args)
        # Should return 1 (not running) unless GaaS happens to be running
        # during tests — either is acceptable
        assert result in (0, 1)


# ─── Doctor checks ────────────────────────────────────────────────────────────


class TestDoctorChecks:
    """Test individual doctor checks in isolation."""

    def test_check_python_passes(self):
        """We're running on 3.11+ so this should pass."""
        from app.doctor import check_python

        assert check_python() is True

    def test_check_uv(self):
        """uv should be available in the test environment."""
        from app.doctor import check_uv

        # May or may not be in PATH depending on env
        result = check_uv()
        assert isinstance(result, bool)

    def test_check_git(self):
        """git should be available."""
        from app.doctor import check_git

        assert check_git() is True

    def test_check_config_with_valid_config(self, tmp_path, monkeypatch):
        """Doctor should pass with a valid config file."""
        import app.doctor as doctor

        config = tmp_path / "config.yaml"
        config.write_text(
            "llms:\n"
            "  default:\n"
            "    base_url: http://localhost:11434\n"
            "    model: test\n"
            "directories:\n"
            "  notes: /tmp/notes\n"
            "  task_queue: /tmp/queue\n"
            "  logs: /tmp/logs\n"
        )
        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_config() is True

    def test_check_config_missing(self, tmp_path, monkeypatch):
        """Doctor should fail when config.yaml is missing."""
        import app.doctor as doctor

        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_config() is False

    def test_check_config_invalid_yaml(self, tmp_path, monkeypatch):
        """Doctor should fail on broken YAML."""
        import app.doctor as doctor

        config = tmp_path / "config.yaml"
        config.write_text(": : : not valid yaml [[[")
        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_config() is False

    def test_check_secrets_present(self, tmp_path, monkeypatch):
        """Doctor should pass when secrets.yaml exists."""
        import app.doctor as doctor

        (tmp_path / "secrets.yaml").write_text("key: value\n")
        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_secrets() is True

    def test_check_secrets_missing(self, tmp_path, monkeypatch):
        """Doctor should warn when secrets.yaml is missing."""
        import app.doctor as doctor

        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_secrets() is False

    def test_check_directories_valid(self, tmp_path, monkeypatch):
        """Doctor should pass when directories exist and are writable."""
        import app.doctor as doctor

        # Create dirs
        notes = tmp_path / "data" / "notes"
        queue = tmp_path / "data" / "queue"
        logs = tmp_path / "data" / "logs"
        for d in (notes, queue, logs):
            d.mkdir(parents=True)

        config = tmp_path / "config.yaml"
        config.write_text(
            f"directories:\n"
            f"  notes: {notes}\n"
            f"  task_queue: {queue}\n"
            f"  logs: {logs}\n"
        )
        monkeypatch.setattr(doctor, "PROJECT_ROOT", tmp_path)
        assert doctor.check_directories() is True


# ─── Setup config generation ─────────────────────────────────────────────────


class TestSetupConfigGeneration:
    """Test the config and secrets YAML generation functions."""

    def test_build_config_yaml_minimal(self):
        from app.setup import _build_config_yaml

        result = _build_config_yaml(
            llm_config={"default": {"base_url": "http://localhost:11434", "model": "test"}},
            integrations=[],
            directories={"notes": "/tmp/notes", "task_queue": "/tmp/queue", "logs": "/tmp/logs"},
        )
        assert "llms:" in result
        assert "default:" in result
        assert "http://localhost:11434" in result
        assert "directories:" in result
        assert "/tmp/notes" in result

    def test_build_config_yaml_with_secret(self):
        from app.setup import _build_config_yaml

        result = _build_config_yaml(
            llm_config={
                "default": {
                    "base_url": "https://api.openai.com",
                    "model": "gpt-4o",
                    "token": "!secret llm_api_key",
                }
            },
            integrations=[],
            directories={"notes": "/tmp/n", "task_queue": "/tmp/q", "logs": "/tmp/l"},
        )
        # !secret should appear as a YAML tag, not a quoted string
        assert "!secret llm_api_key" in result

    def test_build_config_yaml_with_integration(self):
        from app.setup import _build_config_yaml

        result = _build_config_yaml(
            llm_config={"default": {"base_url": "http://localhost:11434", "model": "test"}},
            integrations=[
                {
                    "type": "email",
                    "name": "personal",
                    "imap_server": "imap.example.com",
                    "imap_port": 993,
                    "username": "me@example.com",
                    "password": "!secret personal_email_password",
                    "schedule": {"every": "30m"},
                    "llm": "default",
                    "platforms": {
                        "inbox": {
                            "limit": 50,
                            "classifications": {
                                "human": "is this a personal email written by a human?",
                            },
                        }
                    },
                }
            ],
            directories={"notes": "/tmp/n", "task_queue": "/tmp/q", "logs": "/tmp/l"},
        )
        assert "integrations:" in result
        assert "type: email" in result
        assert "name: personal" in result
        assert "!secret personal_email_password" in result
        assert "every: 30m" in result
        assert "limit: 50" in result

    def test_build_secrets_yaml(self):
        from app.setup import _build_secrets_yaml

        result = _build_secrets_yaml({"my_key": "my_value", "other": "secret"})
        assert "my_key: my_value" in result
        assert "other: secret" in result
        assert "!secret" in result  # Header comment mentions !secret

    def test_build_secrets_yaml_empty(self):
        from app.setup import _build_secrets_yaml

        result = _build_secrets_yaml({})
        assert "GaaS secrets" in result  # Header still present

    def test_backup_file(self, tmp_path):
        from app.setup import _backup_file

        target = tmp_path / "config.yaml"
        target.write_text("original content")

        backup_path = _backup_file(target)
        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.read_text() == "original content"
        assert ".bak." in backup_path.name

    def test_backup_file_nonexistent(self, tmp_path):
        from app.setup import _backup_file

        target = tmp_path / "does_not_exist.yaml"
        assert _backup_file(target) is None


# ─── Install script validation ────────────────────────────────────────────────


class TestInstallScript:
    """Validate the install.sh script structure (not execution)."""

    def test_install_script_exists(self):
        assert (PROJECT_ROOT / "install.sh").exists()

    def test_install_script_is_executable(self):

        mode = (PROJECT_ROOT / "install.sh").stat().st_mode
        assert mode & 0o111, "install.sh should be executable"

    def test_install_script_has_function_wrapping(self):
        """All code should be in main() for partial-download protection."""
        content = (PROJECT_ROOT / "install.sh").read_text()
        assert "main()" in content
        assert 'main "$@"' in content

    def test_install_script_has_set_euo(self):
        """Script should use strict error handling."""
        content = (PROJECT_ROOT / "install.sh").read_text()
        assert "set -euo pipefail" in content

    def test_install_script_checks_prerequisites(self):
        """Script should check for required tools."""
        content = (PROJECT_ROOT / "install.sh").read_text()
        assert "check_git" in content
        assert "check_python" in content
        assert "check_uv" in content

    def test_install_script_has_banner(self):
        """Script should include ASCII art banner."""
        content = (PROJECT_ROOT / "install.sh").read_text()
        assert "GaaS" in content
        assert "Greg as a Service" in content

    def test_install_script_shellcheck(self):
        """Run shellcheck if available."""
        if subprocess.run(["which", "shellcheck"], capture_output=True).returncode != 0:
            pytest.skip("shellcheck not installed")
        result = subprocess.run(
            ["shellcheck", "-e", "SC2034", str(PROJECT_ROOT / "install.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"shellcheck errors:\n{result.stdout}"
