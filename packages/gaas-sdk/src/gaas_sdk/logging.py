"""Structured audit logging for GaaS.

Provides ``AuditLogger``, a typed wrapper around ``logging.Logger`` that
adds a ``.human()`` method for the HUMAN log level (25). This replaces
the previous monkey-patch on ``logging.Logger`` and eliminates the need
for ``# type: ignore[attr-defined]`` at every call site.

The ``HumanMarkdownHandler`` (which writes daily audit files) stays in
``app/human_log.py`` because it depends on ``app.config``. This module
only registers the custom level name so that ``.human()`` works even
before the handler is attached.

Usage::

    from gaas_sdk.logging import get_logger

    log = get_logger(__name__)
    log.human("Archived email from **%s**", sender)
    log.info("Operational detail")
"""

from __future__ import annotations

import logging
from typing import Any

HUMAN = 25  # between INFO (20) and WARNING (30)
logging.addLevelName(HUMAN, "HUMAN")


class AuditLogger:
    """Typed wrapper around ``logging.Logger`` with a ``.human()`` method."""

    __slots__ = ("_logger",)

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def human(self, msg: str, *args: object, **kwargs: Any) -> None:
        if self._logger.isEnabledFor(HUMAN):
            self._logger._log(HUMAN, msg, args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)


def get_logger(name: str) -> AuditLogger:
    """Create an ``AuditLogger`` for the given module name."""
    return AuditLogger(name)
