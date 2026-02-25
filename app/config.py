from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

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


def _load_platform_const(integration_type: str, platform_name: str):
    """Dynamically load a platform's const module, or None if absent.

    Uses the loader's manifest registry to find the integration, then loads
    the platform-specific const.py from platforms/{platform_name}/const.py.
    """
    from app.loader import get_manifests, load_platform_const_module

    manifests = get_manifests()
    manifest = manifests.get(integration_type)
    if manifest is None:
        return None
    return load_platform_const_module(manifest, platform_name)


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


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


class DirectoriesConfig(BaseModel):
    notes: Path | None = None
    task_queue: Path = Path("data/queue")
    logs: Path = Path("logs")
    custom_integrations: Path | None = None


# ---------------------------------------------------------------------------
# Dynamic model construction from manifests
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _json_schema_to_field(
    prop_name: str, prop_def: dict, required_fields: set[str]
) -> tuple:
    """Convert a JSON Schema property definition to a (type, default) tuple
    for pydantic.create_model().
    """
    json_type = prop_def.get("type", "string")

    if json_type == "array":
        item_type_str = prop_def.get("items", {}).get("type", "string")
        item_type = _JSON_TYPE_MAP.get(item_type_str, str)
        python_type = list[item_type]
    else:
        python_type = _JSON_TYPE_MAP.get(json_type, str)

    is_required = prop_name in required_fields
    has_default = "default" in prop_def

    if has_default:
        return (python_type, prop_def["default"])
    elif is_required:
        return (python_type, ...)
    else:
        return (python_type | None, None)


def _build_platform_model(
    domain: str,
    platform_name: str,
    platform_manifest,
) -> type[BaseModel]:
    """Create a Pydantic model for a single platform's config."""
    schema = platform_manifest.config_schema
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields = {}
    for prop_name, prop_def in properties.items():
        fields[prop_name] = _json_schema_to_field(prop_name, prop_def, required_fields)

    model_name = f"{domain.title().replace('_', '')}{platform_name.title().replace('_', '')}PlatformConfig"

    return create_model(
        model_name,
        __base__=BasePlatformConfig,
        **fields,
    )


def build_integration_model(manifest) -> type[BaseModel]:
    """Create a Pydantic model from a manifest's config_schema and platforms.

    The model inherits from BaseIntegrationConfig and adds
    integration-specific fields. The ``type`` field is constrained
    to a Literal matching the manifest's domain. Platform models
    are built from each platform's config_schema.
    """
    schema = manifest.config_schema
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields = {}
    for prop_name, prop_def in properties.items():
        fields[prop_name] = _json_schema_to_field(prop_name, prop_def, required_fields)

    # Override 'type' with a Literal for discriminated union support
    fields["type"] = (Literal[manifest.domain], manifest.domain)

    # Build platform models and container
    if manifest.platforms:
        platform_fields = {}
        for plat_name, plat_manifest in manifest.platforms.items():
            plat_model = _build_platform_model(manifest.domain, plat_name, plat_manifest)
            platform_fields[plat_name] = (plat_model | None, None)

        container_name = f"{manifest.domain.title().replace('_', '')}PlatformsContainer"
        PlatformsContainer = create_model(container_name, **platform_fields)
        fields["platforms"] = (PlatformsContainer | None, None)

    model_name = f"{manifest.domain.title().replace('_', '')}Integration"

    return create_model(
        model_name,
        __base__=BaseIntegrationConfig,
        **fields,
    )


def build_integration_union(manifests: dict) -> type:
    """Build a discriminated union type from all discovered integration manifests."""
    if not manifests:
        return BaseIntegrationConfig

    models = [build_integration_model(m) for m in manifests.values()]

    if len(models) == 1:
        return Annotated[models[0], Field(discriminator="type")]

    union_type = Union[tuple(models)]
    return Annotated[union_type, Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Safety validation
# ---------------------------------------------------------------------------


def _find_unsafe_actions(
    automation: AutomationConfig,
    irreversible_actions: frozenset[str],
) -> list[str]:
    """Return irreversible action names that lack a !yolo override."""
    unsafe = []
    for action in automation.then:
        if isinstance(action, YoloAction):
            continue
        name = action if isinstance(action, str) else next(iter(action), "")
        if name in irreversible_actions:
            unsafe.append(name)
    return unsafe


def _filter_platform_automations(
    platform,
    integration_name: str,
    platform_name: str,
    deterministic_sources: frozenset[str],
    irreversible_actions: frozenset[str],
) -> list[str]:
    """Remove unsafe automations from a platform, returning warning messages."""
    warnings = []
    safe = []
    for automation in platform.automations:
        provenance = resolve_provenance(automation.when, deterministic_sources)
        if provenance in ("llm", "hybrid"):
            unsafe_actions = _find_unsafe_actions(automation, irreversible_actions)
            if unsafe_actions:
                when_keys = ", ".join(automation.when.keys())
                warnings.append(
                    f"Automation disabled in '{integration_name}.{platform_name}': "
                    f"irreversible actions [{', '.join(unsafe_actions)}] "
                    f"with {provenance} provenance "
                    f"(conditions: {when_keys}). "
                    f"Use !yolo tag on the action to override."
                )
                continue
        safe.append(automation)
    platform.automations = safe
    return warnings


def _validate_automation_safety(integrations: list) -> list[str]:
    """Validate that no automation triggers irreversible actions from
    non-deterministic provenance without a !yolo override.

    Unsafe automations are removed from the platform's list.
    Returns warning messages for each automation that was disabled.

    Platform-specific constants (DETERMINISTIC_SOURCES, IRREVERSIBLE_ACTIONS)
    are loaded dynamically from each platform's const.py.
    """
    warnings = []
    for integration in integrations:
        platforms = getattr(integration, "platforms", None)
        if platforms is None:
            continue
        for platform_name in type(platforms).model_fields:
            platform = getattr(platforms, platform_name)
            if platform is None or not hasattr(platform, "automations"):
                continue

            const = _load_platform_const(integration.type, platform_name)
            deterministic_sources = getattr(const, "DETERMINISTIC_SOURCES", frozenset())
            irreversible_actions = getattr(const, "IRREVERSIBLE_ACTIONS", frozenset())
            warnings.extend(_filter_platform_automations(
                platform, integration.name, platform_name,
                deterministic_sources, irreversible_actions,
            ))
    return warnings


# ---------------------------------------------------------------------------
# Two-phase config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path = _CONFIG_PATH) -> tuple:
    """Load and validate config with dynamic integration discovery.

    Phase 1: Parse raw YAML, extract custom_integrations directory path.
    Phase 2: Discover integration manifests from built-in and custom dirs.
    Phase 3: Build dynamic Pydantic models from manifest config schemas.
    Phase 4: Validate full config and run safety checks.
    """
    from app.loader import discover_integrations

    # Phase 1: Raw YAML parse
    with config_path.open() as f:
        raw: dict = yaml.load(f, Loader=_Loader)

    custom_dir_raw = raw.get("directories", {}).get("custom_integrations")
    custom_dir = Path(custom_dir_raw) if custom_dir_raw else None

    # Phase 2: Discover integration manifests
    builtin_dir = Path(__file__).parent / "integrations"
    manifests = discover_integrations(builtin_dir, custom_dir)

    # Phase 3: Build dynamic union type
    Integration = build_integration_union(manifests)

    # Phase 4: Define AppConfig with the dynamic Integration type and validate
    class AppConfig(BaseModel):
        llms: dict[str, LLMConfig]
        integrations: list[Integration] = []
        directories: DirectoriesConfig = DirectoriesConfig()

        @model_validator(mode="after")
        def _check_unique_names(self):
            seen: set[str] = set()
            for i in self.integrations:
                if i.id in seen:
                    raise ValueError(
                        f"Duplicate integration: {i.id!r}. "
                        f"Each {i.type}.name must be unique."
                    )
                seen.add(i.id)
            return self

        def get_integration(self, integration_id: str):
            for entry in self.integrations:
                if entry.id == integration_id:
                    return entry
            available = [i.id for i in self.integrations]
            raise ValueError(
                f"Unknown integration {integration_id!r}. "
                f"Available: {available}"
            )

        def get_integrations_by_type(self, integration_type: str) -> list:
            return [i for i in self.integrations if i.type == integration_type]

        def get_platform(self, integration_id: str, platform_name: str):
            integration = self.get_integration(integration_id)
            platforms = getattr(integration, "platforms", None)
            if platforms is None:
                raise ValueError(f"Integration {integration_id!r} has no platforms")
            platform = getattr(platforms, platform_name, None)
            if platform is None:
                raise ValueError(
                    f"Platform {platform_name!r} not configured in {integration_id!r}"
                )
            return platform

    cfg = AppConfig(**raw)
    warnings = _validate_automation_safety(cfg.integrations)
    return cfg, warnings


# ---------------------------------------------------------------------------
# Module-level singleton — loaded eagerly at import time
# ---------------------------------------------------------------------------

config, safety_warnings = load_config()

if safety_warnings:
    _log = logging.getLogger(__name__)
    for _w in safety_warnings:
        _log.warning(_w)
