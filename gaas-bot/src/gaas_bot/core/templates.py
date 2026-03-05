"""Jinja2 template rendering for gaas-bot commands."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Undefined

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def render(name: str, ctx: dict, *, extra_dirs: list[Path] | None = None) -> str:
    """Render a Jinja2 template by name with the given context.

    Templates are loaded from the package's templates/ directory.
    Extra directories can be added for command-specific template locations.
    """
    search_path = [str(TEMPLATES_DIR)]
    if extra_dirs:
        search_path.extend(str(d) for d in extra_dirs)

    env = Environment(
        loader=FileSystemLoader(search_path),
        keep_trailing_newline=True,
        undefined=Undefined,
    )
    return env.get_template(name).render(**ctx)
