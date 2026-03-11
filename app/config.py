from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml  # type: ignore[import-untyped]

from pydantic import BaseModel, Field, create_model, model_validator

# Re-export models from gaas_sdk so existing imports work unchanged
from gaas_sdk.models import (  # noqa: F401
    YoloAction,
    ScheduleConfig,
    ScriptConfig,
    ClassificationConfig,
    AutomationConfig,
    BasePlatformConfig,
    BaseIntegrationConfig,
    SimpleAction,
    ScriptAction,
    ServiceAction,
    DictAction,
)
from gaas_sdk.provenance import resolve_provenance

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
SECRETS_PATH = _PROJECT_ROOT / "secrets.yaml"

_secrets_cache: dict[str, Any] | None = None


def _load_secrets() -> dict[str, Any]:
    global _secrets_cache
    if _secrets_cache is None:
        if SECRETS_PATH.exists():
            _secrets_cache = yaml.safe_load(SECRETS_PATH.read_text()) or {}
        else:
            _secrets_cache = {}
    return _secrets_cache


def _secret_constructor(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    key = loader.construct_scalar(node)
    secrets = _load_secrets()
    if key not in secrets:
        raise ValueError(
            f"Secret '{key}' not found in {SECRETS_PATH}. "
            f"Available secrets: {list(secrets.keys())}"
        )
    return str(secrets[key])


_Loader: type = type("_Loader", (yaml.SafeLoader,), {})
_Loader.add_constructor("!secret", _secret_constructor)  # type: ignore[attr-defined]


def _yolo_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> YoloAction:
    if isinstance(node, yaml.ScalarNode):
        return YoloAction(loader.construct_scalar(node))
    if isinstance(node, yaml.MappingNode):
        return YoloAction(loader.construct_mapping(node, deep=True))
    raise yaml.constructor.ConstructorError(
        None, None,
        f"expected a scalar or mapping node, but found {node.id}",
        node.start_mark,
    )


_Loader.add_constructor("!yolo", _yolo_constructor)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def load_platform_const(integration_type: str, platform_name: str) -> object | None:
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
# Config models (app-specific, not in SDK)
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str
    token: str | None = None
    parameters: dict[str, Any] = {}


class DirectoriesConfig(BaseModel):
    notes: Path | None = None
    task_queue: Path = Path("data/queue")
    logs: Path = Path("logs")
    custom_integrations: Path | None = None


class RateLimitConfig(BaseModel):
    max: int
    per: str  # "30m", "1h", "1d"


class TaskPolicyConfig(BaseModel):
    deduplicate_pending: bool = True
    rate_limit: RateLimitConfig | None = None


class QueuePolicyConfig(BaseModel):
    defaults: TaskPolicyConfig = TaskPolicyConfig()
    overrides: dict[str, TaskPolicyConfig] = {}


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
    prop_name: str, prop_def: dict[str, Any], required_fields: set[str]
) -> tuple[Any, ...]:
    """Convert a JSON Schema property definition to a (type, default) tuple
    for pydantic.create_model().
    """
    json_type = prop_def.get("type", "string")

    python_type: Any
    if json_type == "array":
        item_type_str = prop_def.get("items", {}).get("type", "string")
        item_type = _JSON_TYPE_MAP.get(item_type_str, str)
        python_type = list[item_type]  # type: ignore[valid-type]
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
    platform_manifest: Any,
) -> type[BaseModel]:
    """Create a Pydantic model for a single platform's config."""
    schema = platform_manifest.config_schema
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields = {}
    for prop_name, prop_def in properties.items():
        fields[prop_name] = _json_schema_to_field(prop_name, prop_def, required_fields)

    domain_part = domain.title().replace('_', '')
    platform_part = platform_name.title().replace('_', '')
    model_name = f"{domain_part}{platform_part}PlatformConfig"

    return create_model(  # type: ignore[call-overload, no-any-return]
        model_name,
        __base__=BasePlatformConfig,
        **fields,
    )


def build_integration_model(manifest: Any) -> type[BaseModel]:
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
        PlatformsContainer = create_model(container_name, **platform_fields)  # type: ignore[call-overload]
        fields["platforms"] = (PlatformsContainer | None, None)

    model_name = f"{manifest.domain.title().replace('_', '')}Integration"

    return create_model(  # type: ignore[call-overload, no-any-return]
        model_name,
        __base__=BaseIntegrationConfig,
        **fields,
    )


def build_integration_union(manifests: dict[str, Any]) -> Any:
    """Build a discriminated union type from all discovered integration manifests."""
    if not manifests:
        return BaseIntegrationConfig

    models = [build_integration_model(m) for m in manifests.values()]

    if len(models) == 1:
        return Annotated[models[0], Field(discriminator="type")]

    union_type = Union[tuple(models)]  # type: ignore[valid-type]  # noqa: UP007
    return Annotated[union_type, Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Safety validation
# ---------------------------------------------------------------------------


class _UniversalSet:
    """Sentinel set whose ``__contains__`` always returns True.

    Used as a fail-safe fallback for IRREVERSIBLE_ACTIONS when a platform's
    const.py is missing — every action name is treated as irreversible until
    proven otherwise.
    """

    def __contains__(self, item: object) -> bool:
        return True


def _find_unsafe_actions(
    automation: AutomationConfig,
    irreversible_actions: frozenset[str] | _UniversalSet,
    scripts: dict[str, ScriptConfig] | None = None,
) -> list[str]:
    """Return irreversible action names that lack a !yolo override."""
    unsafe = []
    for action in automation.then:
        if isinstance(action, YoloAction):
            continue
        if isinstance(action, SimpleAction):
            if action.action in irreversible_actions:
                unsafe.append(action.action)
        elif isinstance(action, ScriptAction):
            script_ref = action.script
            script_name = script_ref.get("name", "") if isinstance(script_ref, dict) else script_ref
            if scripts is None:
                unsafe.append(f"script:{script_name}")
            else:
                script_def = scripts.get(script_name)
                if script_def is None or not script_def.reversible:
                    unsafe.append(f"script:{script_name}")
        elif isinstance(action, ServiceAction):
            service_ref = action.service
            call = service_ref.get("call", "")
            parts = call.rsplit(".", 2)
            if len(parts) == 3:
                svc_type, _svc_name, service_name = parts
                # Look up service reversibility from manifest registry
                from app.loader import get_manifests
                manifests = get_manifests()
                manifest = manifests.get(svc_type)
                if manifest and service_name in manifest.services:
                    if not manifest.services[service_name].reversible:
                        unsafe.append(f"service:{call}")
                else:
                    unsafe.append(f"service:{call}")
            else:
                unsafe.append(f"service:{call}")
        elif isinstance(action, DictAction):
            name = next(iter(action.data), "")
            if name in irreversible_actions:
                unsafe.append(name)
        else:
            log.warning(
                "Unrecognized action type %s treated as irreversible",
                type(action).__name__,
            )
            unsafe.append(f"unknown:{type(action).__name__}")
    return unsafe


def _filter_platform_automations(
    platform: Any,
    integration_name: str,
    platform_name: str,
    deterministic_sources: frozenset[str],
    irreversible_actions: frozenset[str] | _UniversalSet,
    scripts: dict[str, ScriptConfig] | None = None,
) -> list[str]:
    """Remove unsafe automations from a platform, returning warning messages."""
    warnings = []
    safe = []
    for automation in platform.automations:
        provenance = resolve_provenance(automation.when, deterministic_sources)
        if provenance in ("llm", "hybrid"):
            unsafe_actions = _find_unsafe_actions(automation, irreversible_actions, scripts=scripts)
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


def _validate_automation_safety(
    integrations: list[Any],
    scripts: dict[str, ScriptConfig] | None = None,
) -> list[str]:
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

            const = load_platform_const(integration.type, platform_name)
            if const is None:
                log.warning(
                    "Safety constants unavailable for %s.%s, "
                    "treating all actions as irreversible",
                    integration.type,
                    platform_name,
                )
            deterministic_sources: frozenset[str] = getattr(
                const, "DETERMINISTIC_SOURCES", frozenset(),
            )
            irreversible_actions = getattr(
                const, "IRREVERSIBLE_ACTIONS", _UniversalSet()
            )
            warnings.extend(_filter_platform_automations(
                platform, integration.name, platform_name,
                deterministic_sources, irreversible_actions,
                scripts=scripts,
            ))
    return warnings


def _validate_script_references(
    integrations: list[Any],
    scripts: dict[str, ScriptConfig],
) -> list[str]:
    """Warn about automation rules that reference undefined scripts.

    The automations are NOT disabled -- the handler gracefully skips
    unknown scripts at runtime, matching the act.py pattern.
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
            for automation in platform.automations:
                for action in automation.then:
                    raw = action
                    if isinstance(raw, YoloAction):
                        from gaas_sdk.models import _normalize_action
                        raw = _normalize_action(raw.value)
                    if isinstance(raw, ScriptAction):
                        script_ref = raw.script
                        if isinstance(script_ref, dict):
                            name = script_ref.get("name", "")
                        else:
                            name = script_ref
                        if name not in scripts:
                            warnings.append(
                                f"Automation in '{integration.name}.{platform_name}' "
                                f"references undefined script '{name}'"
                            )
    return warnings


def _validate_service_references(
    integrations: list[Any],
) -> list[str]:
    """Warn about automation rules that reference unconfigured services.

    The automations are NOT disabled -- the handler gracefully skips
    unknown services at runtime.
    """
    from app.loader import get_manifests

    manifests = get_manifests()
    warnings = []
    for integration in integrations:
        platforms = getattr(integration, "platforms", None)
        if platforms is None:
            continue
        for platform_name in type(platforms).model_fields:
            platform = getattr(platforms, platform_name)
            if platform is None or not hasattr(platform, "automations"):
                continue
            for automation in platform.automations:
                for action in automation.then:
                    raw = action
                    if isinstance(raw, YoloAction):
                        from gaas_sdk.models import _normalize_action
                        raw = _normalize_action(raw.value)
                    if isinstance(raw, ServiceAction):
                        service_ref = raw.service
                        call = service_ref.get("call", "")
                        parts = call.rsplit(".", 2)
                        if len(parts) != 3:
                            warnings.append(
                                f"Automation in '{integration.name}.{platform_name}' "
                                f"has malformed service call '{call}' "
                                f"(expected 'type.instance.service')"
                            )
                        else:
                            svc_type, _svc_name, service_name = parts
                            manifest = manifests.get(svc_type)
                            if not manifest or service_name not in manifest.services:
                                warnings.append(
                                    f"Automation in '{integration.name}.{platform_name}' "
                                    f"references unknown service '{call}'"
                                )
    return warnings


# ---------------------------------------------------------------------------
# Two-phase config loading
# ---------------------------------------------------------------------------


def load_config(config_path: Path = _CONFIG_PATH) -> tuple[Any, list[str]]:
    """Load and validate config with dynamic integration discovery.

    Phase 1: Parse raw YAML, extract custom_integrations directory path.
    Phase 2: Discover integration manifests from built-in and custom dirs.
    Phase 3: Build dynamic Pydantic models from manifest config schemas.
    Phase 4: Validate full config and run safety checks.
    """
    from app.loader import discover_integrations

    # Phase 1: Raw YAML parse
    with config_path.open() as f:
        raw: dict[str, Any] = yaml.load(f, Loader=_Loader)

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
        integrations: list[Integration] = []  # type: ignore[valid-type]
        directories: DirectoriesConfig = DirectoriesConfig()
        scripts: dict[str, ScriptConfig] = {}
        queue_policies: QueuePolicyConfig = QueuePolicyConfig()

        @model_validator(mode="after")
        def _check_unique_names(self) -> AppConfig:
            seen: set[str] = set()
            for i in self.integrations:
                if i.id in seen:  # type: ignore[attr-defined]
                    raise ValueError(
                        f"Duplicate integration: {i.id!r}. "  # type: ignore[attr-defined]
                        f"Each {i.type}.name must be unique."  # type: ignore[attr-defined]
                    )
                seen.add(i.id)  # type: ignore[attr-defined]
            return self

        def get_integration(self, integration_id: str) -> Any:
            for entry in self.integrations:
                if entry.id == integration_id:  # type: ignore[attr-defined]
                    return entry
            available = [i.id for i in self.integrations]  # type: ignore[attr-defined]
            raise ValueError(
                f"Unknown integration {integration_id!r}. "
                f"Available: {available}"
            )

        def get_integrations_by_type(self, integration_type: str) -> list[Any]:
            return [i for i in self.integrations if i.type == integration_type]  # type: ignore[attr-defined]

        def get_platform(self, integration_id: str, platform_name: str) -> Any:
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
    warnings = _validate_automation_safety(cfg.integrations, scripts=cfg.scripts)
    warnings.extend(_validate_script_references(cfg.integrations, cfg.scripts))
    warnings.extend(_validate_service_references(cfg.integrations))
    return cfg, warnings


# ---------------------------------------------------------------------------
# Module-level singleton -- loaded eagerly at import time
# ---------------------------------------------------------------------------

config, safety_warnings = load_config()

if safety_warnings:
    _log = logging.getLogger(__name__)
    for _w in safety_warnings:
        _log.warning(_w)


def reload_config(config_path: Path = _CONFIG_PATH) -> None:
    """Reload config from disk into the module-level singleton.

    Used by the UI after writing config changes so the page renders
    updated values. Does NOT affect the running scheduler/worker -- a
    full process restart is still required for those.
    """
    global config, safety_warnings, _secrets_cache
    _secrets_cache = None  # bust the secrets cache
    config, safety_warnings = load_config(config_path)
