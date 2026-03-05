"""Runtime registration for SDK modules.

Integrations call functions like ``enqueue()`` and ``get_integration()``
without importing from ``app.*``. The app registers implementations at
startup via ``register()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from collections.abc import Callable

from gaas_sdk.models import BaseIntegrationConfig, BasePlatformConfig


class RuntimeNotRegistered(RuntimeError):
    """Raised when SDK runtime functions are called before register()."""

    def __init__(self, fn_name: str):
        super().__init__(
            f"gaas_sdk.runtime.{fn_name}() called before register(). "
            f"Ensure app.runtime_init.register_runtime() runs at startup."
        )


_enqueue: Callable[..., str | None] | None = None
_get_integration: Callable[[str], BaseIntegrationConfig] | None = None
_get_platform: Callable[[str, str], BasePlatformConfig] | None = None
_create_llm_conversation: Callable[..., Any] | None = None
_get_llm_config: Callable[[str], Any] | None = None
_get_notes_dir: Callable[[], Path] | None = None


def register(
    *,
    enqueue: Callable[..., str | None],
    get_integration: Callable[[str], BaseIntegrationConfig],
    get_platform: Callable[[str, str], BasePlatformConfig],
    create_llm_conversation: Callable[..., Any],
    get_llm_config: Callable[[str], Any],
    get_notes_dir: Callable[[], Path],
) -> None:
    """Register app-level implementations for SDK runtime functions.

    Called once at app startup, before any integration code runs.
    """
    global _enqueue, _get_integration, _get_platform
    global _create_llm_conversation, _get_llm_config, _get_notes_dir
    _enqueue = enqueue
    _get_integration = get_integration
    _get_platform = get_platform
    _create_llm_conversation = create_llm_conversation
    _get_llm_config = get_llm_config
    _get_notes_dir = get_notes_dir


def enqueue(
    payload: dict[str, Any], priority: int = 5, provenance: str | None = None,
) -> str | None:
    if _enqueue is None:
        raise RuntimeNotRegistered("enqueue")
    return _enqueue(payload, priority=priority, provenance=provenance)


def get_integration(integration_id: str) -> BaseIntegrationConfig:
    if _get_integration is None:
        raise RuntimeNotRegistered("get_integration")
    return _get_integration(integration_id)


def get_platform(integration_id: str, platform_name: str) -> BasePlatformConfig:
    if _get_platform is None:
        raise RuntimeNotRegistered("get_platform")
    return _get_platform(integration_id, platform_name)


def create_llm_conversation(model: str = "default", system: str | None = None) -> Any:
    if _create_llm_conversation is None:
        raise RuntimeNotRegistered("create_llm_conversation")
    return _create_llm_conversation(model, system)


def get_llm_config(profile: str = "default") -> Any:
    if _get_llm_config is None:
        raise RuntimeNotRegistered("get_llm_config")
    return _get_llm_config(profile)


def get_notes_dir() -> Path:
    if _get_notes_dir is None:
        raise RuntimeNotRegistered("get_notes_dir")
    return _get_notes_dir()


# ---------------------------------------------------------------------------
# Service log templates (simple key-value storage, no callback registration)
# ---------------------------------------------------------------------------

_service_log_templates: dict[str, str] = {}


def set_service_log_template(task_type: str, template: str) -> None:
    _service_log_templates[task_type] = template


def get_service_log_template(task_type: str) -> str | None:
    return _service_log_templates.get(task_type)
