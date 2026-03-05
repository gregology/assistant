"""Core Pydantic models for GaaS integrations.

These models define the configuration structures shared by all integrations.
No app.* imports allowed — only pydantic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class YoloAction:
    """Marker for actions tagged with !yolo in config.

    Signals that the user has explicitly acknowledged the risk of
    running this irreversible action with non-deterministic provenance.
    """

    def __init__(self, value: str | dict[str, Any]):
        self.value = value

    def __repr__(self) -> str:
        return f"YoloAction({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, YoloAction):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("yolo", repr(self.value)))


class ScheduleConfig(BaseModel):
    every: str | None = None
    cron: str | None = None


class ScriptConfig(BaseModel):
    description: str = ""
    inputs: list[str] = []
    timeout: int = 120
    shell: str
    output: str | None = None
    on_output: str = "human_log"
    reversible: bool = False


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


class SimpleAction(BaseModel):
    """A plain string action like 'archive', 'spam', 'trash'."""

    action: str

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SimpleAction):
            return self.action == other.action
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("simple", self.action))


class ScriptAction(BaseModel):
    """A script action: ``{script: {name: ..., inputs: ...}}``."""

    script: str | dict[str, Any]


class ServiceAction(BaseModel):
    """A service action: ``{service: {call: ..., inputs: ...}}``."""

    service: dict[str, Any]


class DictAction(BaseModel):
    """A platform-specific dict action like ``{draft_reply: ...}``."""

    model_config = ConfigDict(extra="allow")

    data: dict[str, Any]


ActionType = SimpleAction | ScriptAction | ServiceAction | DictAction


def _normalize_action(action: Any) -> Any:
    """Convert raw action values to action model instances.

    - Bare strings → SimpleAction
    - Dicts with 'script' → ScriptAction
    - Dicts with 'service' → ServiceAction
    - Other dicts → DictAction (platform-specific like draft_reply, move_to)
    - YoloAction wrappers are preserved
    """
    if isinstance(action, (SimpleAction, ScriptAction, ServiceAction, DictAction)):
        return action
    if isinstance(action, YoloAction):
        return action
    if isinstance(action, str):
        return SimpleAction(action=action)
    if isinstance(action, dict):
        if "script" in action:
            return ScriptAction(script=action["script"])
        if "service" in action:
            return ServiceAction(service=action["service"])
        return DictAction(data=action)
    return action


class AutomationConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    when: dict[str, Any]
    then: list[ActionType | YoloAction]

    @model_validator(mode="before")
    @classmethod
    def _normalize_then(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        then = data.get("then")
        if isinstance(then, (str, dict, YoloAction)):
            data["then"] = [then]
        if isinstance(data.get("then"), list):
            data["then"] = [_normalize_action(a) for a in data["then"]]
        return data


class BasePlatformConfig(BaseModel):
    """Common fields shared by all platform configs.

    Classifications and automations are per-platform, not per-integration.
    Dynamically created platform models inherit from this class.
    """

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


class BaseIntegrationConfig(BaseModel):
    """Common fields shared by all integration configs.

    After the platforms refactor, classifications and automations
    live in BasePlatformConfig, not here.
    """

    type: str
    name: str
    schedule: ScheduleConfig | None = None
    llm: str = "default"

    @property
    def id(self) -> str:
        """Composite identity following HA's entity_id pattern: ``{type}.{name}``."""
        return f"{self.type}.{self.name}"
