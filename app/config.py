from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"

with _CONFIG_PATH.open() as f:
    _data: dict = yaml.safe_load(f)

_overrides: dict[str, Any] = {}


def set_override(dotpath: str, value: Any) -> None:
    """Set a runtime override that takes precedence over config.yml."""
    _overrides[dotpath] = value


def cfg(dotpath: str, default=None):
    """Retrieve a config value using dot-notation, e.g. cfg('email.imap_server')."""
    if dotpath in _overrides:
        return _overrides[dotpath]
    keys = dotpath.split(".")
    value = _data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value
