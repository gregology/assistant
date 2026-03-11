"""YAML round-trip read/write layer.

All config mutations go through this module. Uses ruamel.yaml to preserve
comments, key ordering, block style, and custom tags (!secret, !yolo).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import fcntl
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML, CommentedMap  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_LOCK_PATH = _DEFAULT_CONFIG_PATH.with_suffix(".lock")


# ---------------------------------------------------------------------------
# Singleton YAML instance (round-trip mode)
# ---------------------------------------------------------------------------

_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True


# ---------------------------------------------------------------------------
# Dirty flag — tracks whether config has been written since last restart
# ---------------------------------------------------------------------------

_config_dirty = False


def mark_dirty() -> None:
    global _config_dirty
    _config_dirty = True


def is_dirty() -> bool:
    return _config_dirty


# ---------------------------------------------------------------------------
# Locking helper
# ---------------------------------------------------------------------------


@contextmanager
def _lock_config():
    """Exclusive lock on a separate lockfile to protect the config.yaml RMW cycle."""
    _LOCK_PATH.touch(exist_ok=True)
    with _LOCK_PATH.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigValidationError(Exception):
    """Wraps validation errors with user-friendly messages."""


# ---------------------------------------------------------------------------
# Core read/write
# ---------------------------------------------------------------------------


def read_config(config_path: Path = _DEFAULT_CONFIG_PATH) -> CommentedMap:
    with config_path.open() as f:
        return _yaml.load(f)


def write_config(data: CommentedMap, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if config_path.exists():
        shutil.copy2(config_path, backup_path)
    with config_path.open("w") as f:
        _yaml.dump(data, f)
    mark_dirty()


def validate_proposed(data: CommentedMap, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    """Validate config by dumping to a temp file and loading via Pydantic.

    Raises ConfigValidationError on failure. Cleans up temp file in finally.
    """
    from app.config import load_config

    tmp_dir = config_path.parent
    _fd, tmp_path_str = tempfile.mkstemp(suffix=".yaml", dir=tmp_dir)
    tmp_path = Path(tmp_path_str)
    try:
        with tmp_path.open("w") as f:
            _yaml.dump(data, f)
        load_config(tmp_path)
    except Exception as exc:
        raise ConfigValidationError(str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Secret helpers
# ---------------------------------------------------------------------------


def is_secret_ref(node: Any) -> bool:
    """Check if a ruamel node has a !secret tag."""
    tag = getattr(node, "tag", None)
    return bool(tag and tag.value == "!secret")


def get_secret_key(node: Any) -> str | None:
    """Get the key name from a !secret tagged scalar."""
    if is_secret_ref(node):
        return str(node)
    return None


# ---------------------------------------------------------------------------
# Section update functions
# ---------------------------------------------------------------------------


def update_llm_profile(
    name: str, updates: dict[str, Any], config_path: Path = _DEFAULT_CONFIG_PATH
) -> None:
    with _lock_config():
        data = read_config(config_path)
        if "llms" not in data:
            data["llms"] = CommentedMap()

        if name not in data["llms"]:
            data["llms"][name] = CommentedMap()

        profile = data["llms"][name]
        for key, value in updates.items():
            profile[key] = value

        validate_proposed(data, config_path)
        write_config(data, config_path)


def delete_llm_profile(name: str, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    with _lock_config():
        data = read_config(config_path)
        llms = data.get("llms", {})
        if name not in llms:
            raise ConfigValidationError(f"LLM profile '{name}' not found")
        if len(llms) <= 1:
            raise ConfigValidationError("Cannot delete the last LLM profile")
        del data["llms"][name]
        validate_proposed(data, config_path)
        write_config(data, config_path)


def update_directories(
    updates: dict[str, Any], config_path: Path = _DEFAULT_CONFIG_PATH
) -> None:
    with _lock_config():
        data = read_config(config_path)
        if "directories" not in data:
            data["directories"] = CommentedMap()

        dirs = data["directories"]
        for key, value in updates.items():
            if value == "" or value is None:
                if key in dirs:
                    del dirs[key]
            else:
                dirs[key] = value

        validate_proposed(data, config_path)
        write_config(data, config_path)


def update_integration_settings(
    integration_index: int,
    updates: dict[str, Any],
    config_path: Path = _DEFAULT_CONFIG_PATH,
) -> None:
    with _lock_config():
        data = read_config(config_path)
        integrations = data.get("integrations", [])
        if integration_index < 0 or integration_index >= len(integrations):
            raise ConfigValidationError(
                f"Integration index {integration_index} out of range"
            )

        integration = integrations[integration_index]
        if "schedule" in updates:
            integration["schedule"] = updates["schedule"]
        if "llm" in updates:
            integration["llm"] = updates["llm"]

        validate_proposed(data, config_path)
        write_config(data, config_path)


def update_script(
    name: str, updates: dict[str, Any], config_path: Path = _DEFAULT_CONFIG_PATH
) -> None:
    with _lock_config():
        data = read_config(config_path)
        if "scripts" not in data:
            data["scripts"] = CommentedMap()

        if name not in data["scripts"]:
            data["scripts"][name] = CommentedMap()

        script = data["scripts"][name]
        for key, value in updates.items():
            script[key] = value

        validate_proposed(data, config_path)
        write_config(data, config_path)


def delete_script(name: str, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    with _lock_config():
        data = read_config(config_path)
        scripts = data.get("scripts", {})
        if name not in scripts:
            raise ConfigValidationError(f"Script '{name}' not found")
        del data["scripts"][name]
        validate_proposed(data, config_path)
        write_config(data, config_path)


def save_raw_yaml(yaml_string: str, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    """Save raw YAML string from the escape-hatch editor."""
    with _lock_config():
        stream = StringIO(yaml_string)
        try:
            data = _yaml.load(stream)
        except Exception as exc:
            raise ConfigValidationError(f"Invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigValidationError("Config must be a YAML mapping")
        validate_proposed(data, config_path)
        write_config(data, config_path)


def read_raw_yaml(config_path: Path = _DEFAULT_CONFIG_PATH) -> str:
    """Read config.yaml as plain text for the raw editor."""
    return config_path.read_text()
