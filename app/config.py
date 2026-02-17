from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field

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


class ScheduleConfig(BaseModel):
    every: str | None = None
    cron: str | None = None


class EmailIntegration(BaseModel):
    type: Literal["email"] = "email"
    name: str
    imap_server: str
    imap_port: int = 993
    username: str
    password: str
    schedule: ScheduleConfig | None = None
    llm: str = "default"
    limit: int = 50


class GitHubIntegration(BaseModel):
    type: Literal["github"] = "github"
    name: str
    schedule: ScheduleConfig | None = None
    llm: str = "default"


Integration = Annotated[
    EmailIntegration | GitHubIntegration,
    Field(discriminator="type"),
]


class DirectoriesConfig(BaseModel):
    notes: Path | None = None
    task_queue: Path = Path("data/queue")
    logs: Path = Path("logs")


class AppConfig(BaseModel):
    llms: dict[str, LLMConfig]
    integrations: list[Integration] = []
    directories: DirectoriesConfig = DirectoriesConfig()

    def get_integration(self, name: str) -> Integration:
        for entry in self.integrations:
            if entry.name == name:
                return entry
        available = [i.name for i in self.integrations]
        raise ValueError(
            f"Unknown integration '{name}'. Available: {available}"
        )

    def get_integrations_by_type(self, integration_type: str) -> list[Integration]:
        return [i for i in self.integrations if i.type == integration_type]


# ---------------------------------------------------------------------------
# Load configuration at import time
# ---------------------------------------------------------------------------

with _CONFIG_PATH.open() as _f:
    _raw: dict = yaml.load(_f, Loader=_Loader)

config: AppConfig = AppConfig(**_raw)
