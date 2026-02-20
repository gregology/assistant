from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import config

_log_dir: Path = Path(config.directories.logs)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

HUMAN = 25  # between INFO (20) and WARNING (30)
logging.addLevelName(HUMAN, "HUMAN")


class HumanMarkdownHandler(logging.Handler):
    """Logging handler that appends to a daily markdown file.

    Uses O_APPEND mode so concurrent writers (multiple worker processes)
    produce intact, non-interleaved lines — guaranteed by POSIX for
    writes up to PIPE_BUF (4096 bytes), well above our line lengths.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            now = datetime.now().astimezone()
            timestamp = now.strftime("%H:%M")
            line = f" - {timestamp} {message}\n"

            path = _log_dir / now.strftime("%Y-%m-%d %A.md")
            _log_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(line)
        except Exception:
            self.handleError(record)


def _human(self: logging.Logger, msg: str, *args: object, **kwargs) -> None:
    if self.isEnabledFor(HUMAN):
        self._log(HUMAN, msg, args, **kwargs)


logging.Logger.human = _human  # type: ignore[attr-defined]

_handler = HumanMarkdownHandler()
_handler.setLevel(HUMAN)
_handler.addFilter(lambda record: record.levelno == HUMAN)
logging.getLogger().addHandler(_handler)
