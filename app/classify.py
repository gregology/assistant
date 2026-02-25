"""Shared classification utilities.

Provides schema building and Jinja2 environment setup used by all
platform classify handlers. Platform-specific prompt rendering and
handle() logic stays in each platform's classify.py.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import ClassificationConfig

_TYPE_TO_SCHEMA = {
    "confidence": lambda _cls: {"type": "number"},
    "boolean": lambda _cls: {"type": "boolean"},
    "enum": lambda cls: {"type": "string", "enum": cls.values},
}


def build_schema(classifications: dict[str, ClassificationConfig]) -> dict:
    """Convert classification configs to a JSON schema for the LLM."""
    properties = {}
    for name, cls in classifications.items():
        properties[name] = _TYPE_TO_SCHEMA[cls.type](cls)
    return {
        "properties": properties,
        "required": list(classifications.keys()),
    }


def make_jinja_env(templates_dir: Path) -> Environment:
    """Create a Jinja2 environment with the scrub filter for prompt injection defense."""
    env = Environment(loader=FileSystemLoader(templates_dir))
    env.filters["scrub"] = lambda s: str(s).replace("END UNTRUSTED", "")
    return env
