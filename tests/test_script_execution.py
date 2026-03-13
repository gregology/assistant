"""Tests for script executor."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.actions.script import _process_log_file, execute, handle
from app.config import ScriptConfig


def _make_script(**kwargs) -> ScriptConfig:
    defaults = {
        "shell": "echo hello",
        "timeout": 5,
    }
    defaults.update(kwargs)
    return ScriptConfig(**defaults)


class TestExecute:
    def test_successful_execution(self):
        _make_script(shell="echo hello", output="OUTPUT")
        # The script itself must set OUTPUT and it must be captured via env
        script_with_output = _make_script(
            shell='OUTPUT="result_value"',
            output="OUTPUT",
        )
        result = execute(script_with_output, {})
        assert result == "result_value"

    def test_no_output_returns_none(self):
        script = _make_script(shell="echo hello")
        result = execute(script, {})
        assert result is None

    def test_empty_output_returns_none(self):
        script = _make_script(shell='OUTPUT=""', output="OUTPUT")
        result = execute(script, {})
        assert result is None

    def test_timeout_raises(self):
        script = _make_script(shell="sleep 10", timeout=1)
        with pytest.raises(subprocess.TimeoutExpired):
            execute(script, {})

    def test_nonzero_exit_logs_warning(self):
        script = _make_script(shell="exit 1", output="OUTPUT")
        # Should not raise, just log warning
        result = execute(script, {})
        assert result is None

    def test_nonzero_exit_no_output_captured(self):
        """exit 1 terminates before the output capture line runs."""
        script = _make_script(
            shell='OUTPUT="partial_result"\nexit 1',
            output="OUTPUT",
        )
        result = execute(script, {})
        # Output capture runs after the script body, so exit 1 prevents it
        assert result is None

    def test_nonzero_returncode_without_exit_captures_output(self):
        """A failing command doesn't prevent output capture if the script continues."""
        script = _make_script(
            shell='false\nOUTPUT="still_captured"',
            output="OUTPUT",
        )
        result = execute(script, {})
        assert result == "still_captured"

    def test_input_env_vars_injected(self):
        script = _make_script(
            shell='OUTPUT="$ASSISTANT_INPUT_DOMAIN"',
            output="OUTPUT",
        )
        result = execute(script, {"domain": "example.com"})
        assert result == "example.com"

    def test_input_vars_uppercased(self):
        script = _make_script(
            shell='OUTPUT="$ASSISTANT_INPUT_MY_KEY"',
            output="OUTPUT",
        )
        result = execute(script, {"my_key": "test_value"})
        assert result == "test_value"

    def test_preamble_log_human(self, tmp_path):
        script = _make_script(shell='log_human "test message"')
        # Just verify it doesn't crash — log routing is tested separately
        result = execute(script, {})
        assert result is None

    def test_preamble_log_info(self):
        script = _make_script(shell='log_info "info message"')
        result = execute(script, {})
        assert result is None

    def test_preamble_log_warn(self):
        script = _make_script(shell='log_warn "warning message"')
        result = execute(script, {})
        assert result is None


class TestProcessLogFile:
    def test_routes_human_log(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("HUMAN\ttest message\x1e")
        with patch("app.actions.script.logging") as mock_logging:
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            _process_log_file(log_file, "test_script")
            mock_logger.log.assert_called_once_with(25, "%s", "test message")

    def test_routes_info_log(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("INFO\tinfo message\x1e")
        with patch("app.actions.script.logging") as mock_logging:
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            mock_logging.INFO = 20
            _process_log_file(log_file, "test_script")
            mock_logger.log.assert_called_once_with(20, "%s", "info message")

    def test_routes_warn_log(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("WARN\twarn message\x1e")
        with patch("app.actions.script.logging") as mock_logging:
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            mock_logging.WARNING = 30
            _process_log_file(log_file, "test_script")
            mock_logger.log.assert_called_once_with(30, "%s", "warn message")

    def test_multiple_records(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("INFO\tmsg1\x1eHUMAN\tmsg2\x1e")
        with patch("app.actions.script.logging") as mock_logging:
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            mock_logging.INFO = 20
            _process_log_file(log_file, "test_script")
            assert mock_logger.log.call_count == 2

    def test_multiline_message_preserved(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("HUMAN\tline1\nline2\nline3\x1e")
        with patch("app.actions.script.logging") as mock_logging:
            mock_logger = MagicMock()
            mock_logging.getLogger.return_value = mock_logger
            _process_log_file(log_file, "test_script")
            mock_logger.log.assert_called_once_with(25, "%s", "line1\nline2\nline3")

    def test_empty_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        # Should not crash
        _process_log_file(log_file, "test_script")

    def test_missing_file(self, tmp_path):
        log_file = tmp_path / "nonexistent.log"
        # Should not crash
        _process_log_file(log_file, "test_script")


class TestHandle:
    def test_unknown_script_warns(self):
        task = {"payload": {"type": "script.run", "script_name": "nonexistent", "inputs": {}}}
        with patch("app.actions.script.config") as mock_config:
            mock_config.scripts = {}
            # Should not crash
            handle(task)

    def test_successful_handle(self):
        script_def = _make_script(
            shell='OUTPUT="done"',
            output="OUTPUT",
            on_output="human_log",
            description="test script",
        )
        task = {"payload": {"type": "script.run", "script_name": "test", "inputs": {}}}
        with patch("app.actions.script.config") as mock_config:
            mock_config.scripts = {"test": script_def}
            handle(task)

    def test_handle_with_inputs(self):
        script_def = _make_script(
            shell='OUTPUT="$ASSISTANT_INPUT_DOMAIN"',
            output="OUTPUT",
            description="test script",
        )
        task = {
            "payload": {
                "type": "script.run",
                "script_name": "test",
                "inputs": {"domain": "example.com"},
            }
        }
        with patch("app.actions.script.config") as mock_config:
            mock_config.scripts = {"test": script_def}
            handle(task)
