"""Transform app state into template-ready view models.

All secret masking and provenance computation happens here, never in templates.

Config-editing contexts (config_context, llm_profiles_context, etc.) read
from disk via load_config() so the UI always reflects the file as written.
Runtime contexts (dashboard, queue, logs) use the in-memory singleton since
they display the state of the running system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from assistant_sdk.models import (
    BaseIntegrationConfig,
    BasePlatformConfig,
    DictAction,
    ScriptAction,
    ServiceAction,
    SimpleAction,
    YoloAction,
)
from assistant_sdk.provenance import resolve_provenance

from app.config import (
    SECRETS_PATH,
    config,
    load_config,
    load_platform_const,
    safety_warnings,
)
from app import queue as _queue

log = logging.getLogger(__name__)

_SENSITIVE_NAMES = frozenset({"password", "token", "api_key", "secret"})


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------


@dataclass
class LLMProfileView:
    name: str
    base_url: str
    model: str
    token: str
    parameters: dict[str, Any]


@dataclass
class ClassificationView:
    name: str
    prompt: str
    type: str
    values: list[str] | None


@dataclass
class AutomationView:
    when: dict[str, Any]
    then: list[str]
    provenance: str
    yolo: bool


@dataclass
class PlatformView:
    name: str
    fields: dict[str, str]
    classifications: list[ClassificationView]
    automations: list[AutomationView]


@dataclass
class IntegrationView:
    id: str
    type: str
    name: str
    schedule: str | None
    schedule_type: str
    schedule_value: str
    llm_profile: str
    fields: dict[str, str]
    platforms: list[PlatformView]


@dataclass
class ScriptView:
    name: str
    description: str
    inputs: list[str]
    timeout: int
    reversible: bool
    shell: str
    output: str | None
    on_output: str


@dataclass
class QueueCounts:
    pending: int = 0
    active: int = 0
    done: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.pending + self.active + self.done + self.failed


@dataclass
class TaskView:
    id: str
    status: str
    task_type: str
    created_at: str
    payload: dict[str, Any]


@dataclass
class LogDateView:
    date: str
    filename: str


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def mask_value(field_name: str, value: str, secret_values: frozenset[str]) -> str:
    if not isinstance(value, str):
        return str(value)
    if value in secret_values:
        return "********"
    if any(s in field_name.lower() for s in _SENSITIVE_NAMES):
        return "********"
    return value


def _load_secret_values() -> frozenset[str]:
    if not SECRETS_PATH.exists():
        return frozenset()
    try:
        raw = yaml.safe_load(SECRETS_PATH.read_text()) or {}
        return frozenset(str(v) for v in raw.values() if v is not None)
    except Exception:
        log.warning("Could not read secrets.yaml for masking")
        return frozenset()


# ---------------------------------------------------------------------------
# Display config — read from disk so the UI always shows the file as written
# ---------------------------------------------------------------------------


def _load_display_config() -> Any:
    """Load config fresh from disk for UI display.

    This is intentionally separate from the runtime singleton. The UI
    shows what's on disk; the running system uses what was loaded at
    startup. The 'restart required' banner communicates the gap.
    """
    cfg, _ = load_config()
    return cfg


# ---------------------------------------------------------------------------
# LLM profiles
# ---------------------------------------------------------------------------


def _present_llm_profiles(cfg: Any, secret_values: frozenset[str]) -> list[LLMProfileView]:
    profiles = []
    for name, llm_cfg in cfg.llms.items():
        profiles.append(
            LLMProfileView(
                name=name,
                base_url=llm_cfg.base_url,
                model=llm_cfg.model,
                token=mask_value("token", llm_cfg.token, secret_values) if llm_cfg.token else "",
                parameters=llm_cfg.parameters,
            )
        )
    return profiles


# ---------------------------------------------------------------------------
# Classifications & automations
# ---------------------------------------------------------------------------


def _present_classification(name: str, cls_cfg: Any) -> ClassificationView:
    return ClassificationView(
        name=name,
        prompt=cls_cfg.prompt,
        type=cls_cfg.type,
        values=cls_cfg.values,
    )


def _format_action(action: Any) -> str:
    if isinstance(action, YoloAction):
        return f"!yolo {action.value}"
    if isinstance(action, SimpleAction):
        return action.action
    if isinstance(action, ScriptAction):
        return str({"script": action.script})
    if isinstance(action, ServiceAction):
        return str({"service": action.service})
    if isinstance(action, DictAction):
        return str(action.data)
    return str(action)


def _get_deterministic_sources(integration_type: str, platform_name: str) -> frozenset[str]:
    const = load_platform_const(integration_type, platform_name)
    return getattr(const, "DETERMINISTIC_SOURCES", frozenset())


def _present_automation(
    automation: Any,
    integration_type: str,
    platform_name: str,
    deterministic_sources: frozenset[str],
) -> AutomationView:
    provenance = resolve_provenance(automation.when, deterministic_sources)
    has_yolo = any(isinstance(a, YoloAction) for a in automation.then)
    return AutomationView(
        when=dict(automation.when),
        then=[_format_action(a) for a in automation.then],
        provenance=provenance,
        yolo=has_yolo,
    )


# ---------------------------------------------------------------------------
# Platforms & integrations
# ---------------------------------------------------------------------------

_BASE_INTEGRATION_FIELDS = frozenset(BaseIntegrationConfig.model_fields.keys() | {"platforms"})
_BASE_PLATFORM_FIELDS = frozenset(BasePlatformConfig.model_fields.keys())


def _present_platform(
    platform_name: str,
    platform: Any,
    integration_type: str,
    secret_values: frozenset[str],
) -> PlatformView:
    deterministic_sources = _get_deterministic_sources(integration_type, platform_name)

    fields = {}
    for fname in type(platform).model_fields:
        if fname in _BASE_PLATFORM_FIELDS:
            continue
        val = getattr(platform, fname)
        fields[fname] = mask_value(fname, str(val) if val is not None else "", secret_values)

    classifications = [
        _present_classification(name, cls_cfg) for name, cls_cfg in platform.classifications.items()
    ]

    automations = [
        _present_automation(auto, integration_type, platform_name, deterministic_sources)
        for auto in platform.automations
    ]

    return PlatformView(
        name=platform_name,
        fields=fields,
        classifications=classifications,
        automations=automations,
    )


def _present_integration(integration: Any, secret_values: frozenset[str]) -> IntegrationView:
    schedule = None
    schedule_type = "none"
    schedule_value = ""
    if integration.schedule:
        if integration.schedule.every:
            schedule = f"every {integration.schedule.every}"
            schedule_type = "every"
            schedule_value = integration.schedule.every
        elif integration.schedule.cron:
            schedule = f"cron: {integration.schedule.cron}"
            schedule_type = "cron"
            schedule_value = integration.schedule.cron

    fields = {}
    for fname in type(integration).model_fields:
        if fname in _BASE_INTEGRATION_FIELDS:
            continue
        val = getattr(integration, fname)
        fields[fname] = mask_value(fname, str(val) if val is not None else "", secret_values)

    platforms = []
    platforms_obj = getattr(integration, "platforms", None)
    if platforms_obj is not None:
        for plat_name in type(platforms_obj).model_fields:
            plat = getattr(platforms_obj, plat_name)
            if plat is None:
                continue
            platforms.append(_present_platform(plat_name, plat, integration.type, secret_values))

    return IntegrationView(
        id=integration.id,
        type=integration.type,
        name=integration.name,
        schedule=schedule,
        schedule_type=schedule_type,
        schedule_value=schedule_value,
        llm_profile=integration.llm,
        fields=fields,
        platforms=platforms,
    )


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


def _present_scripts(cfg: Any) -> list[ScriptView]:
    scripts = []
    for name, script_cfg in cfg.scripts.items():
        scripts.append(
            ScriptView(
                name=name,
                description=script_cfg.description,
                inputs=script_cfg.inputs,
                timeout=script_cfg.timeout,
                reversible=script_cfg.reversible,
                shell=script_cfg.shell,  # nosec B604
                output=script_cfg.output,
                on_output=script_cfg.on_output,
            )
        )
    return scripts


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def _get_queue_counts() -> QueueCounts:
    counts = {}
    for d in _queue.DIRS:
        dir_path = _queue.BASE_DIR / d
        if dir_path.is_dir():
            counts[d] = len([f for f in dir_path.iterdir() if f.suffix == ".yaml"])
        else:
            counts[d] = 0
    return QueueCounts(**counts)


def _get_recent_tasks(directory: str, limit: int = 10) -> list[TaskView]:
    dir_path = _queue.BASE_DIR / directory
    if not dir_path.is_dir():
        return []
    files = sorted(
        (f for f in dir_path.iterdir() if f.suffix == ".yaml"),
        key=lambda f: f.name,
        reverse=True,
    )
    tasks = []
    for f in files[:limit]:
        try:
            data = yaml.safe_load(f.read_text())
            payload = data.get("payload", {})
            tasks.append(
                TaskView(
                    id=data.get("id", f.stem),
                    status=data.get("status", directory),
                    task_type=payload.get("type", "unknown"),
                    created_at=data.get("created_at", ""),
                    payload=payload,
                )
            )
        except Exception:
            log.warning("Could not parse task file: %s", f)
    return tasks


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


def _get_log_dates() -> list[LogDateView]:
    log_dir = Path(config.directories.logs)
    if not log_dir.is_dir():
        return []
    files = sorted(
        (f for f in log_dir.iterdir() if f.suffix == ".md"),
        key=lambda f: f.name,
        reverse=True,
    )
    return [LogDateView(date=f.stem, filename=f.name) for f in files]


def _read_log_file(date: str) -> str | None:
    log_dir = Path(config.directories.logs)
    if not log_dir.is_dir():
        return None
    for f in log_dir.iterdir():
        if f.suffix == ".md" and f.stem == date:
            return f.read_text()
    return None


# ---------------------------------------------------------------------------
# Public context builders
# ---------------------------------------------------------------------------


def dashboard_context() -> dict[str, Any]:
    """Dashboard shows the running system's state — uses runtime singleton."""
    secret_values = _load_secret_values()
    return {
        "integrations": [_present_integration(i, secret_values) for i in config.integrations],
        "queue": _get_queue_counts(),
        "recent_logs": _get_log_dates()[:5],
        "safety_warnings": list(safety_warnings),
    }


def config_context() -> dict[str, Any]:
    """Config page shows what's on disk — reads fresh from the file."""
    from app.ui.yaml_rw import is_dirty, read_raw_yaml

    cfg = _load_display_config()
    secret_values = _load_secret_values()
    return {
        "llm_profiles": _present_llm_profiles(cfg, secret_values),
        "integrations": [_present_integration(i, secret_values) for i in cfg.integrations],
        "scripts": _present_scripts(cfg),
        "directories": {
            "notes": str(cfg.directories.notes or ""),
            "task_queue": str(cfg.directories.task_queue),
            "logs": str(cfg.directories.logs),
            "custom_integrations": str(cfg.directories.custom_integrations or ""),
        },
        "llm_names": list(cfg.llms.keys()),
        "raw_yaml": read_raw_yaml(),
        "config_dirty": is_dirty(),
    }


def llm_profiles_context() -> dict[str, Any]:
    from app.ui.yaml_rw import is_dirty

    cfg = _load_display_config()
    secret_values = _load_secret_values()
    return {
        "llm_profiles": _present_llm_profiles(cfg, secret_values),
        "llm_names": list(cfg.llms.keys()),
        "config_dirty": is_dirty(),
    }


def scripts_list_context() -> dict[str, Any]:
    from app.ui.yaml_rw import is_dirty

    cfg = _load_display_config()
    return {
        "scripts": _present_scripts(cfg),
        "config_dirty": is_dirty(),
    }


def directories_context() -> dict[str, Any]:
    from app.ui.yaml_rw import is_dirty

    cfg = _load_display_config()
    return {
        "directories": {
            "notes": str(cfg.directories.notes or ""),
            "task_queue": str(cfg.directories.task_queue),
            "logs": str(cfg.directories.logs),
            "custom_integrations": str(cfg.directories.custom_integrations or ""),
        },
        "config_dirty": is_dirty(),
    }


def integration_header_context(index: int) -> dict[str, Any]:
    cfg = _load_display_config()
    secret_values = _load_secret_values()
    integration = _present_integration(cfg.integrations[index], secret_values)
    return {
        "integration": integration,
        "llm_names": list(cfg.llms.keys()),
        "index": index,
    }


def raw_yaml_context() -> dict[str, Any]:
    from app.ui.yaml_rw import is_dirty, read_raw_yaml

    return {
        "raw_yaml": read_raw_yaml(),
        "config_dirty": is_dirty(),
    }


def queue_context() -> dict[str, Any]:
    return {
        "counts": _get_queue_counts(),
        "tasks": {d: _get_recent_tasks(d) for d in _queue.DIRS},
    }


def log_list_context() -> dict[str, Any]:
    return {
        "dates": _get_log_dates(),
    }


def log_detail_context(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "content": _read_log_file(date),
        "dates": _get_log_dates(),
    }
