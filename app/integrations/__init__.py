"""Integration handler registry.

Handlers and entry tasks are populated by register_all(), which must be
called after app.loader.load_all_modules() has loaded integration modules.
"""

import importlib
import logging
from collections.abc import Mapping

from app.loader import get_manifests, get_modules
from assistant_sdk import runtime
from assistant_sdk.protocols import TaskHandler

log = logging.getLogger(__name__)

HANDLERS: dict[str, TaskHandler] = {}
ENTRY_TASKS: dict[str, str] = {}


def _load_handler(module_name: str, handler_path: str) -> TaskHandler | None:
    """Load a handler function from a module path string.

    If handler_path starts with '.', it is relative to module_name.
    Expected format: ".platforms.issues.check.handle" or "module.func"
    """
    parts = handler_path[1:].split(".") if handler_path.startswith(".") else handler_path.split(".")

    func_name = parts.pop()
    sub_module = ".".join(parts)
    full_module_path = f"{module_name}.{sub_module}" if sub_module else module_name

    try:
        mod = importlib.import_module(full_module_path)
        return getattr(mod, func_name)  # type: ignore[no-any-return]
    except (ImportError, AttributeError):
        log.exception("Failed to load handler: %s from %s", handler_path, module_name)
        return None


def _register_integration_handlers(
    domain: str, module_name: str, handlers: dict[str, str]
) -> None:
    """Register integration-level handlers."""
    for suffix, handler_path in handlers.items():
        handler = _load_handler(module_name, handler_path)
        if handler:
            HANDLERS[f"{domain}.{suffix}"] = handler


def _register_platform_handlers(
    domain: str, module_name: str, platforms: Mapping[str, object]
) -> None:
    """Register platform-level handlers and entry tasks."""
    for platform_name, platform_manifest in platforms.items():
        _register_single_platform(domain, module_name, platform_name, platform_manifest)


def _register_single_platform(
    domain: str, module_name: str, platform_name: str, platform_manifest: object
) -> None:
    """Register handlers and entry task for a single platform."""
    for suffix, handler_path in platform_manifest.handlers.items():  # type: ignore[attr-defined]
        handler = _load_handler(module_name, handler_path)
        if handler:
            HANDLERS[f"{domain}.{platform_name}.{suffix}"] = handler

    ENTRY_TASKS[f"{domain}.{platform_name}"] = (
        f"{domain}.{platform_name}.{platform_manifest.entry_task}"  # type: ignore[attr-defined]
    )


def _register_service_handlers(
    domain: str, module_name: str, services: Mapping[str, object]
) -> None:
    """Register service handlers and their log templates."""
    for service_name, service_manifest in services.items():
        _register_single_service(domain, module_name, service_name, service_manifest)


def _register_single_service(
    domain: str, module_name: str, service_name: str, service_manifest: object
) -> None:
    """Register a single service handler and its log template if present."""
    handler = _load_handler(module_name, service_manifest.handler)  # type: ignore[attr-defined]
    if not handler:
        return
    key = f"service.{domain}.{service_name}"
    HANDLERS[key] = handler
    if service_manifest.human_log:  # type: ignore[attr-defined]
        runtime.set_service_log_template(key, service_manifest.human_log)  # type: ignore[attr-defined]


def register_all() -> None:
    """Register handlers and entry tasks from all loaded integration modules."""
    manifests = get_manifests()
    modules = get_modules()

    for domain, manifest in manifests.items():
        if domain not in modules:
            continue

        module_name = modules[domain].__name__  # type: ignore[attr-defined]

        _register_integration_handlers(domain, module_name, manifest.handlers)
        _register_platform_handlers(domain, module_name, manifest.platforms)
        _register_service_handlers(domain, module_name, manifest.services)
