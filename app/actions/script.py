"""Script executor for user-defined shell scripts.

Scripts run as subprocess with a bash preamble that provides logging
helper functions. Output is captured via environment variable.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import app.human_log  # noqa: F401 — registers HumanMarkdownHandler
from app.config import config
from gaas_sdk.logging import get_logger
from gaas_sdk.task import TaskRecord

log = get_logger(__name__)

# Bash preamble injected before every script. Provides log_human, log_info,
# log_warn helper functions that write LEVEL\tMESSAGE\x1e records to $GAAS_LOG.
# Uses printf so \t and \x1e are interpreted by bash, not Python.
_PREAMBLE = r"""
log_human() { printf 'HUMAN\t%s\x1e' "$*" >> "$GAAS_LOG"; }
log_info()  { printf 'INFO\t%s\x1e' "$*" >> "$GAAS_LOG"; }
log_warn()  { printf 'WARN\t%s\x1e' "$*" >> "$GAAS_LOG"; }
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


def execute(script_def, inputs: dict[str, str]) -> str | None:
    """Execute a script definition with the given inputs.

    Returns the captured output string, or None if no output was captured.
    Raises subprocess.TimeoutExpired if the script exceeds its timeout.
    """
    log_file = None
    script_file = None
    output_file = None
    try:
        # Create temp files for logging and output capture
        log_fd, log_path = tempfile.mkstemp(prefix="gaas_log_", suffix=".txt")
        os.close(log_fd)
        log_file = Path(log_path)

        output_fd, output_path = tempfile.mkstemp(prefix="gaas_out_", suffix=".txt")
        os.close(output_fd)
        output_file = Path(output_path)

        # Build environment
        env = os.environ.copy()
        env["GAAS_LOG"] = str(log_file)
        env["GAAS_OUTPUT_FILE"] = str(output_file)
        for key, value in inputs.items():
            env[f"GAAS_INPUT_{key.upper()}"] = value

        # Build script body with output capture
        body = _PREAMBLE + "\n" + script_def.shell
        if script_def.output:
            body += f'\nprintf "%s" "${{{script_def.output}}}" > "$GAAS_OUTPUT_FILE"'

        # Write script to temp file
        script_fd, script_path = tempfile.mkstemp(prefix="gaas_script_", suffix=".sh")
        os.close(script_fd)
        script_file = Path(script_path)
        script_file.write_text(body)

        # Execute
        result = subprocess.run(
            ["bash", str(script_file)],
            env=env,
            timeout=script_def.timeout,
            capture_output=True,
            text=True,
        )

        # Process logs regardless of exit code
        _process_log_file(log_file, script_def.description or "unnamed")

        if result.returncode != 0:
            log.warning(
                "Script exited with code %d: stderr=%s",
                result.returncode, result.stderr.strip(),
            )

        # Capture output
        output = None
        if script_def.output and output_file.exists():
            content = output_file.read_text()
            if content:
                output = content

        return output

    except subprocess.TimeoutExpired:
        # Process any partial logs before re-raising
        if log_file:
            _process_log_file(log_file, script_def.description or "unnamed")
        raise
    finally:
        # Clean up temp files
        for f in (log_file, script_file, output_file):
            if f is not None and f.exists():
                f.unlink()


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
