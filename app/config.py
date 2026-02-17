from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_SECRETS_PATH = _PROJECT_ROOT / "secrets.yaml"

# ---------------------------------------------------------------------------
# Custom YAML loader with !secret tag support
# ---------------------------------------------------------------------------

_secrets_cache: dict[str, Any] | None = None


def _load_secrets() -> dict[str, Any]:
    global _secrets_cache
    if _secrets_cache is None:
        if _SECRETS_PATH.exists():
            _secrets_cache = yaml.safe_load(_SECRETS_PATH.read_text()) or {}
        else:
            _secrets_cache = {}
    return _secrets_cache


def _secret_constructor(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    key = loader.construct_scalar(node)
    secrets = _load_secrets()
    if key not in secrets:
        raise ValueError(
            f"Secret '{key}' not found in {_SECRETS_PATH}. "
            f"Available secrets: {list(secrets.keys())}"
        )
    return secrets[key]


_Loader = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor("!secret", _secret_constructor)

# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str
    token: str | None = None
    parameters: dict[str, Any] = {}


class EmailConfig(BaseModel):
    imap_server: str
    imap_port: int = 993
    username: str
    password: str


class DirectoriesConfig(BaseModel):
    notes: Path | None = None
    task_queue: Path = Path("data/queue")
    logs: Path = Path("logs")


class ScheduleEntry(BaseModel):
    task: str
    every: str | None = None
    cron: str | None = None
    options: dict[str, Any] = {}


class AppConfig(BaseModel):
    llms: dict[str, LLMConfig]
    email: EmailConfig | None = None
    directories: DirectoriesConfig = DirectoriesConfig()
    schedules: list[ScheduleEntry] = []


# ---------------------------------------------------------------------------
# Load configuration at import time
# ---------------------------------------------------------------------------

with _CONFIG_PATH.open() as _f:
    _raw: dict = yaml.load(_f, Loader=_Loader)

config: AppConfig = AppConfig(**_raw)
