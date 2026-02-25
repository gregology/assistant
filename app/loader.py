"""Integration discovery, manifest parsing, and module loading.

Scans built-in (app/integrations/) and custom integration directories for
packages with manifest.yaml files. Handles dynamic Pydantic model construction
for config validation and safe module loading for custom integrations.

This module has NO imports from app.* to avoid circular dependencies with
config.py, which imports from here during load_config().
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class PlatformManifest:
    """Parsed platform definition from a manifest.yaml."""

    name: str
    entry_task: str
    config_schema: dict


@dataclass
class IntegrationManifest:
    """Parsed manifest.yaml for an integration package."""

    domain: str
    name: str
    version: str
    entry_task: str
    dependencies: list[str]
    config_schema: dict
    platforms: dict[str, PlatformManifest]
    path: Path
    builtin: bool


# ---------------------------------------------------------------------------
# Module-level registries (populated by discover/load functions)
# ---------------------------------------------------------------------------

_manifests: dict[str, IntegrationManifest] = {}
_modules: dict[str, object] = {}


def get_manifests() -> dict[str, IntegrationManifest]:
    return _manifests


def get_modules() -> dict[str, object]:
    return _modules


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _scan_directory(
    directory: Path, builtin: bool
) -> dict[str, IntegrationManifest]:
    """Scan a single directory for integration packages with manifest.yaml."""
    manifests: dict[str, IntegrationManifest] = {}
    if not directory.is_dir():
        return manifests
    for integration_dir in sorted(directory.iterdir()):
        if not integration_dir.is_dir():
            continue
        if not (integration_dir / "manifest.yaml").exists():
            continue
        manifest = _load_manifest(integration_dir, builtin=builtin)
        if manifest:
            manifests[manifest.domain] = manifest
    return manifests


def discover_integrations(
    builtin_dir: Path,
    custom_dir: Path | None = None,
) -> dict[str, IntegrationManifest]:
    """Scan directories for integration packages with manifest.yaml.

    Custom integrations with the same domain as a built-in shadow it
    (with a logged warning). Populates the module-level manifest registry.
    """
    global _manifests
    manifests = _scan_directory(builtin_dir, builtin=True)

    if custom_dir:
        for domain, manifest in _scan_directory(custom_dir, builtin=False).items():
            if domain in manifests:
                log.warning(
                    "Custom integration '%s' shadows built-in integration",
                    domain,
                )
            manifests[domain] = manifest

    _manifests = manifests
    return manifests


def _load_manifest(
    integration_dir: Path, builtin: bool
) -> IntegrationManifest | None:
    """Parse a manifest.yaml file into an IntegrationManifest."""
    manifest_path = integration_dir / "manifest.yaml"
    try:
        raw = yaml.safe_load(manifest_path.read_text())
    except Exception:
        log.exception("Failed to parse manifest: %s", manifest_path)
        return None

    if not isinstance(raw, dict):
        log.warning("Invalid manifest (not a dict): %s", manifest_path)
        return None

    domain = raw.get("domain")
    if not domain:
        log.warning("Manifest missing 'domain': %s", manifest_path)
        return None

    if domain != integration_dir.name:
        log.warning(
            "Manifest domain '%s' does not match directory name '%s' in %s",
            domain,
            integration_dir.name,
            manifest_path,
        )
        return None

    raw_platforms = raw.get("platforms", {})
    platforms: dict[str, PlatformManifest] = {}
    for plat_name, plat_def in raw_platforms.items():
        platforms[plat_name] = PlatformManifest(
            name=plat_def.get("name", plat_name),
            entry_task=plat_def.get("entry_task", "check"),
            config_schema=plat_def.get("config_schema", {}),
        )

    return IntegrationManifest(
        domain=domain,
        name=raw.get("name", domain),
        version=raw.get("version", "0.0.0"),
        entry_task=raw.get("entry_task", "check"),
        dependencies=raw.get("dependencies", []),
        config_schema=raw.get("config_schema", {}),
        platforms=platforms,
        path=integration_dir,
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------


def check_dependencies(manifest: IntegrationManifest) -> list[str]:
    """Return list of missing pip dependencies declared in the manifest."""
    missing = []
    for dep in manifest.dependencies:
        pkg_name = (
            dep.split(">=")[0]
            .split("<=")[0]
            .split("==")[0]
            .split(">")[0]
            .split("<")[0]
            .split("!=")[0]
            .strip()
        )
        import_name = pkg_name.replace("-", "_")
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(dep)
    return missing


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------


def load_all_modules() -> dict[str, object]:
    """Load all discovered integration modules.

    Must be called after discover_integrations(). Skips integrations
    with missing dependencies.
    """
    global _modules
    for domain, manifest in _manifests.items():
        missing = check_dependencies(manifest)
        if missing:
            log.warning(
                "Integration '%s' has missing dependencies: %s — skipping.",
                domain,
                ", ".join(missing),
            )
            continue

        try:
            module = _load_module(manifest)
            _modules[domain] = module
        except Exception:
            log.exception("Failed to load integration module: %s", domain)

    return _modules


def _load_module(manifest: IntegrationManifest):
    """Import an integration's Python module."""
    if manifest.builtin:
        return importlib.import_module(f"app.integrations.{manifest.domain}")
    else:
        return _load_custom_module(manifest)


def _load_custom_module(manifest: IntegrationManifest):
    """Load a custom integration via spec_from_file_location.

    Uses a ``gaas_ext.{domain}`` namespace to avoid stdlib shadowing
    and cross-integration leakage. Relative imports within the
    integration package work normally.
    """
    module_name = f"gaas_ext.{manifest.domain}"
    init_path = manifest.path / "__init__.py"

    if not init_path.exists():
        raise ImportError(f"No __init__.py found in {manifest.path}")

    # Ensure the gaas_ext namespace package exists
    if "gaas_ext" not in sys.modules:
        import types

        ns_pkg = types.ModuleType("gaas_ext")
        ns_pkg.__path__ = []
        ns_pkg.__package__ = "gaas_ext"
        sys.modules["gaas_ext"] = ns_pkg

    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(manifest.path)],
    )
    if spec is None:
        raise ImportError(f"Could not create module spec for {manifest.path}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_const_module(manifest: IntegrationManifest):
    """Load an integration's const.py for safety validation.

    const.py must only import from the app framework (app.*), not from
    sibling modules within the integration package.
    Returns None if const.py does not exist.
    """
    const_path = manifest.path / "const.py"
    if not const_path.exists():
        return None

    if manifest.builtin:
        try:
            return importlib.import_module(
                f"app.integrations.{manifest.domain}.const"
            )
        except ImportError:
            return None
    else:
        module_name = f"gaas_ext.{manifest.domain}.const"
        spec = importlib.util.spec_from_file_location(module_name, const_path)
        if spec is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            log.exception("Failed to load const module: %s", const_path)
            del sys.modules[module_name]
            return None
        return module


def load_platform_const_module(manifest: IntegrationManifest, platform_name: str):
    """Load a platform's const.py for safety validation.

    Looks in {manifest.path}/platforms/{platform_name}/const.py.
    Returns None if const.py does not exist.

    Uses spec_from_file_location for both builtin and custom modules
    to avoid triggering the package __init__.py, which may have
    circular import issues when called during config loading.
    """
    const_path = manifest.path / "platforms" / platform_name / "const.py"
    if not const_path.exists():
        return None

    if manifest.builtin:
        module_name = f"app.integrations.{manifest.domain}.platforms.{platform_name}.const"
    else:
        module_name = f"gaas_ext.{manifest.domain}.platforms.{platform_name}.const"

    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, const_path)
    if spec is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        log.exception("Failed to load platform const module: %s", const_path)
        del sys.modules[module_name]
        return None
    return module
