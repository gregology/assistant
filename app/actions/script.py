"""Script executor for user-defined shell scripts.

Scripts run as subprocess with a bash preamble that provides logging
helper functions. Output is captured via environment variable.
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any

import app.human_log  # noqa: F401 — registers HumanMarkdownHandler
from app.config import config
from assistant_sdk.logging import get_logger
from assistant_sdk.task import TaskRecord

log = get_logger(__name__)

# Bash preamble injected before every script. Provides log_human, log_info,
# log_warn helper functions that write LEVEL\tMESSAGE\x1e records to $ASSISTANT_LOG.
# Uses printf so \t and \x1e are interpreted by bash, not Python.
_PREAMBLE = r"""
log_human() { printf 'HUMAN\t%s\x1e' "$*" >> "$ASSISTANT_LOG"; }
log_info()  { printf 'INFO\t%s\x1e' "$*" >> "$ASSISTANT_LOG"; }
log_warn()  { printf 'WARN\t%s\x1e' "$*" >> "$ASSISTANT_LOG"; }
"""

_LOG_ROUTES: dict[str, int] = {
    "HUMAN": 25,  # human_log.HUMAN level
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
}


def _process_log_file(log_path: Path, script_name: str) -> None:
    """Read \x1e-delimited log records and route to Python logging."""
    if not log_path.exists():
        return
    content = log_path.read_text()
    if not content:
        return
    records = content.split("\x1e")
    script_log = logging.getLogger(f"script.{script_name}")
    for record in records:
        record = record.strip()
        if not record:
            continue
        parts = record.split("\t", 1)
        if len(parts) != 2:
            log.warning("Malformed script log record: %r", record)
            continue
        level_str, message = parts
        level = _LOG_ROUTES.get(level_str)
        if level is None:
            log.warning("Unknown script log level %r in script %s", level_str, script_name)
            continue
        script_log.log(level, "%s", message)


def _make_temp_file(prefix: str, suffix: str = ".txt") -> Path:
    """Create a temporary file and return its Path."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(path)


def _build_env(inputs: dict[str, str], log_file: Path, output_file: Path) -> dict[str, str]:
    """Build the subprocess environment with ASSISTANT variables and inputs."""
    env = os.environ.copy()
    env["ASSISTANT_LOG"] = str(log_file)
    env["ASSISTANT_OUTPUT_FILE"] = str(output_file)
    for key, value in inputs.items():
        env[f"ASSISTANT_INPUT_{key.upper()}"] = value
    return env


def _build_script_body(script_def: Any) -> str:
    """Build the bash script body with preamble and optional output capture."""
    body: str = _PREAMBLE + "\n" + str(script_def.shell)
    if script_def.output:
        body += f'\nprintf "%s" "${{{script_def.output}}}" > "$ASSISTANT_OUTPUT_FILE"'
    return body


def _read_captured_output(script_def: Any, output_file: Path) -> str | None:
    """Read the captured output file, returning None if empty or not applicable."""
    if not script_def.output or not output_file.exists():
        return None
    content = output_file.read_text()
    return content or None


def _cleanup_temp_files(*files: Path | None) -> None:
    """Remove temporary files, ignoring missing ones."""
    for f in files:
        if f is not None and f.exists():
            f.unlink()


def _script_label(script_def: Any) -> str:
    return script_def.description or "unnamed"


def execute(script_def: Any, inputs: dict[str, str]) -> str | None:
    """Execute a script definition with the given inputs.

    Returns the captured output string, or None if no output was captured.
    Raises subprocess.TimeoutExpired if the script exceeds its timeout.
    """
    log_file: Path | None = None
    output_file: Path | None = None
    script_file: Path | None = None
    try:
        log_file = _make_temp_file("assistant_log_")
        output_file = _make_temp_file("assistant_out_")
        script_file = _make_temp_file("assistant_script_", suffix=".sh")
        env = _build_env(inputs, log_file, output_file)
        script_file.write_text(_build_script_body(script_def))

        result = subprocess.run(  # nosec
            ["bash", str(script_file)],
            env=env,
            timeout=script_def.timeout,
            capture_output=True,
            text=True,
        )

        _process_log_file(log_file, _script_label(script_def))

        if result.returncode != 0:
            log.warning(
                "Script exited with code %d: stderr=%s",
                result.returncode,
                result.stderr.strip(),
            )

        return _read_captured_output(script_def, output_file)

    except subprocess.TimeoutExpired:
        if log_file is not None:
            _process_log_file(log_file, _script_label(script_def))
        raise
    finally:
        _cleanup_temp_files(log_file, script_file, output_file)


def handle(task: TaskRecord) -> None:
    """Worker handler for script.run tasks."""
    payload = task["payload"]
    script_name = payload.get("script_name", "")
    inputs = payload.get("inputs", {})

    scripts = getattr(config, "scripts", {})
    script_def = scripts.get(script_name)
    if script_def is None:
        log.warning("Unknown script: %s", script_name)
        return

    log.info("Executing script: %s", script_name)
    output = execute(script_def, inputs)

    if output is not None:
        if script_def.on_output == "human_log":
            script_log = get_logger(f"script.{script_name}")
            script_log.human(output)
        else:
            log.info("Script %s output: %s", script_name, output)
