"""Manifest dataclasses for integration and platform discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServiceManifest:
    """Parsed service definition from a manifest.yaml."""

    name: str
    description: str
    handler: str
    reversible: bool = False
    input_schema: dict = field(default_factory=dict)
    human_log: str | None = None


@dataclass
class PlatformManifest:
    """Parsed platform definition from a manifest.yaml."""

    name: str
    entry_task: str
    config_schema: dict
    handlers: dict[str, str] = field(default_factory=dict)


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
    handlers: dict[str, str] = field(default_factory=dict)
    services: dict[str, ServiceManifest] = field(default_factory=dict)
    entry_point_module: str | None = None
