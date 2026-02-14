from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"

with _CONFIG_PATH.open() as f:
    _data: dict = yaml.safe_load(f)


def cfg(dotpath: str, default=None):
    """Retrieve a config value using dot-notation, e.g. cfg('email.imap_server')."""
    keys = dotpath.split(".")
    value = _data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value
