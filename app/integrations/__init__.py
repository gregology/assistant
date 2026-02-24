"""Integration handler registry.

Handlers and entry tasks are populated by register_all(), which must be
called after app.loader.load_all_modules() has loaded integration modules.
"""

from app.loader import get_manifests, get_modules

HANDLERS: dict[str, callable] = {}
ENTRY_TASKS: dict[str, str] = {}


def register_all() -> None:
    """Register handlers and entry tasks from all loaded integration modules."""
    manifests = get_manifests()
    modules = get_modules()

    for domain, module in modules.items():
        handlers = getattr(module, "HANDLERS", {})
        for suffix, handler in handlers.items():
            HANDLERS[f"{domain}.{suffix}"] = handler

        manifest = manifests[domain]
        for platform_name, platform_manifest in manifest.platforms.items():
            entry_key = f"{domain}.{platform_name}"
            entry_task = f"{domain}.{platform_name}.{platform_manifest.entry_task}"
            ENTRY_TASKS[entry_key] = entry_task
