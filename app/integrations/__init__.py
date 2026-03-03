"""Integration handler registry.

Handlers and entry tasks are populated by register_all(), which must be
called after app.loader.load_all_modules() has loaded integration modules.
"""

import importlib
import logging

from app.loader import get_manifests, get_modules
from gaas_sdk import runtime

log = logging.getLogger(__name__)

HANDLERS: dict[str, callable] = {}
ENTRY_TASKS: dict[str, str] = {}


def _load_handler(module_name: str, handler_path: str):
    """Load a handler function from a module path string.

    If handler_path starts with '.', it is relative to module_name.
    Expected format: ".platforms.issues.check.handle" or "module.func"
    """
    if handler_path.startswith("."):
        parts = handler_path[1:].split(".")
    else:
        parts = handler_path.split(".")

    func_name = parts.pop()
    sub_module = ".".join(parts)
    full_module_path = f"{module_name}.{sub_module}" if sub_module else module_name

    try:
        mod = importlib.import_module(full_module_path)
        return getattr(mod, func_name)
    except (ImportError, AttributeError):
        log.exception("Failed to load handler: %s from %s", handler_path, module_name)
        return None


def register_all() -> None:
    """Register handlers and entry tasks from all loaded integration modules."""
    manifests = get_manifests()
    modules = get_modules()

    for domain, manifest in manifests.items():
        if domain not in modules:
            continue

        module = modules[domain]
        module_name = module.__name__

        # 1. Integration-level handlers
        for suffix, handler_path in manifest.handlers.items():
            handler = _load_handler(module_name, handler_path)
            if handler:
                HANDLERS[f"{domain}.{suffix}"] = handler

        # 2. Platform-level handlers
        for platform_name, platform_manifest in manifest.platforms.items():
            for suffix, handler_path in platform_manifest.handlers.items():
                handler = _load_handler(module_name, handler_path)
                if handler:
                    HANDLERS[f"{domain}.{platform_name}.{suffix}"] = handler

            # Entry task registration
            entry_key = f"{domain}.{platform_name}"
            entry_task = f"{domain}.{platform_name}.{platform_manifest.entry_task}"
            ENTRY_TASKS[entry_key] = entry_task

        # 3. Service handlers
        for service_name, service_manifest in manifest.services.items():
            handler = _load_handler(module_name, service_manifest.handler)
            if handler:
                HANDLERS[f"service.{domain}.{service_name}"] = handler
                if service_manifest.human_log:
                    runtime.set_service_log_template(
                        f"service.{domain}.{service_name}",
                        service_manifest.human_log,
                    )
