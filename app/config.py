from __future__ import annotations

import importlib
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_SECRETS_PATH = _PROJECT_ROOT / "secrets.yaml"

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


class YoloAction:
    """Marker for actions tagged with !yolo in config.

    Signals that the user has explicitly acknowledged the risk of
    running this irreversible action with non-deterministic provenance.
    """

    def __init__(self, value: str):
        self.value = value

    def __repr__(self) -> str:
        return f"YoloAction({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, YoloAction):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("yolo", self.value))


def _yolo_constructor(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> YoloAction:
    value = loader.construct_scalar(node)
    return YoloAction(value)


_Loader.add_constructor("!yolo", _yolo_constructor)


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def resolve_provenance(when: dict[str, Any], deterministic_sources: frozenset[str]) -> str:
    """Derive provenance from the namespaces used in automation conditions.

    Returns "rule" if all conditions reference deterministic sources,
    "llm" if all reference classification, or "hybrid" if mixed.

    deterministic_sources must be supplied by the caller from the
    integration's own const.py — config.py holds no integration-specific
    knowledge about what is or isn't deterministic.
    """
    has_deterministic = False
    has_nondeterministic = False
    for key in when:
        namespace = key.split(".")[0]
        if namespace in deterministic_sources:
            has_deterministic = True
        else:
            has_nondeterministic = True
    if has_nondeterministic and has_deterministic:
        return "hybrid"
    if has_nondeterministic:
        return "llm"
    return "rule"


def _load_integration_const(integration_type: str):
    """Dynamically load an integration's const module, or None if absent."""
    try:
        return importlib.import_module(f"app.integrations.{integration_type}.const")
    except ImportError:
        return None


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str
    token: str | None = None
    parameters: dict[str, Any] = {}


class ScheduleConfig(BaseModel):
    every: str | None = None
    cron: str | None = None


class ClassificationConfig(BaseModel):
    prompt: str
    type: Literal["confidence", "boolean", "enum"] = "confidence"
    values: list[str] | None = None

    @model_validator(mode="after")
    def _check_values(self) -> ClassificationConfig:
        if self.type == "enum" and not self.values:
            raise ValueError("'values' is required when type is 'enum'")
        if self.type != "enum" and self.values is not None:
            raise ValueError("'values' is only valid when type is 'enum'")
        return self


class AutomationConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    when: dict[str, Any]
    then: list[str | dict[str, str] | YoloAction]

    @model_validator(mode="before")
    @classmethod
    def _normalize_then(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        then = data.get("then")
        if isinstance(then, (str, YoloAction)):
            data["then"] = [then]
        return data


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
    classifications: dict[str, ClassificationConfig] = {}
    automations: list[AutomationConfig] = []

    @model_validator(mode="before")
    @classmethod
    def _normalize_classifications(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("classifications")
        if not raw or not isinstance(raw, dict):
            return data
        normalized = {}
        for key, value in raw.items():
            if isinstance(value, str):
                normalized[key] = {"prompt": value}
            else:
                normalized[key] = value
        data["classifications"] = normalized
        return data


class GitHubIntegration(BaseModel):
    type: Literal["github"] = "github"
    name: str
    schedule: ScheduleConfig | None = None
    llm: str = "default"
    include_mentions: bool = False
    orgs: list[str] | None = None
    repos: list[str] | None = None
    classifications: dict[str, ClassificationConfig] = {}
    automations: list[AutomationConfig] = []

    @model_validator(mode="before")
    @classmethod
    def _normalize_classifications(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("classifications")
        if not raw or not isinstance(raw, dict):
            return data
        normalized = {}
        for key, value in raw.items():
            if isinstance(value, str):
                normalized[key] = {"prompt": value}
            else:
                normalized[key] = value
        data["classifications"] = normalized
        return data


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

    @model_validator(mode="after")
    def _check_unique_names(self) -> AppConfig:
        seen: set[tuple[str, str]] = set()
        for i in self.integrations:
            key = (i.type, i.name)
            if key in seen:
                raise ValueError(
                    f"Duplicate integration: type={i.type!r} name={i.name!r}. "
                    f"Names must be unique within each integration type."
                )
            seen.add(key)
        return self

    def get_integration(self, name: str, integration_type: str) -> Integration:
        for entry in self.integrations:
            if entry.name == name and entry.type == integration_type:
                return entry
        available = [(i.type, i.name) for i in self.integrations]
        raise ValueError(
            f"Unknown integration type={integration_type!r} name={name!r}. Available: {available}"
        )

    def get_integrations_by_type(self, integration_type: str) -> list[Integration]:
        return [i for i in self.integrations if i.type == integration_type]


def _validate_automation_safety(integrations: list) -> list[str]:
    """Validate that no automation triggers irreversible actions from
    non-deterministic provenance without a !yolo override.

    Unsafe automations are removed from the integration's list.
    Returns warning messages for each automation that was disabled.

    Integration-specific constants (DETERMINISTIC_SOURCES, IRREVERSIBLE_ACTIONS)
    are loaded dynamically from each integration's const.py by integration type.
    Integrations without a const.py skip safety validation for those constants.
    """
    warnings = []
    for integration in integrations:
        if not hasattr(integration, "automations"):
            continue
        const = _load_integration_const(integration.type)
        deterministic_sources: frozenset[str] = getattr(const, "DETERMINISTIC_SOURCES", frozenset())
        irreversible_actions: frozenset[str] = getattr(const, "IRREVERSIBLE_ACTIONS", frozenset())
        safe = []
        for automation in integration.automations:
            provenance = resolve_provenance(automation.when, deterministic_sources)
            if provenance in ("llm", "hybrid"):
                unsafe_actions = []
                for action in automation.then:
                    if isinstance(action, YoloAction):
                        continue
                    name = action if isinstance(action, str) else next(iter(action), "")
                    if name in irreversible_actions:
                        unsafe_actions.append(name)
                if unsafe_actions:
                    when_keys = ", ".join(automation.when.keys())
                    msg = (
                        f"Automation disabled in '{integration.name}': "
                        f"irreversible actions [{', '.join(unsafe_actions)}] "
                        f"with {provenance} provenance "
                        f"(conditions: {when_keys}). "
                        f"Use !yolo tag on the action to override."
                    )
                    warnings.append(msg)
                    continue
            safe.append(automation)
        integration.automations = safe
    return warnings


with _CONFIG_PATH.open() as _f:
    _raw: dict = yaml.load(_f, Loader=_Loader)

config: AppConfig = AppConfig(**_raw)
safety_warnings: list[str] = _validate_automation_safety(config.integrations)

if safety_warnings:
    _log = logging.getLogger(__name__)
    for _w in safety_warnings:
        _log.warning(_w)
