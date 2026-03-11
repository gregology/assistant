"""Process supervisor that manages the FastAPI server and worker as children.

Usage:
    uv run python -m app.supervisor                # Production
    uv run python -m app.supervisor --dev          # Dev mode (uvicorn --reload)
    uv run python -m app.supervisor --expose       # Allow external connections
    uv run python -m app.supervisor --port 8080    # Custom port (default: 6767)

Creates a .gaas-restart sentinel file mechanism: the UI writes the file,
the supervisor detects it and restarts all children.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SENTINEL = PROJECT_ROOT / ".gaas-restart"
POLL_INTERVAL = 0.5  # seconds


class ManagedProcess:
    """Wraps a subprocess with start/stop/restart lifecycle."""

    def __init__(self, name: str, cmd: list[str]):
        self.name = name
        self.cmd = cmd
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        env = {**os.environ, "GAAS_SUPERVISOR": "1"}
        self._proc = subprocess.Popen(self.cmd, env=env)
        log.info("Started %s (pid %d)", self.name, self._proc.pid)

    def stop(self, timeout: int = 30) -> None:
        if not self.is_running:
            return
        assert self._proc is not None
        pid = self._proc.pid
        log.info("Stopping %s (pid %d)…", self.name, pid)
        self._proc.terminate()
        try:
            self._proc.wait(timeout=timeout)
            log.info("%s stopped", self.name)
        except subprocess.TimeoutExpired:
            log.warning("%s did not stop in %ds, killing", self.name, timeout)
            self._proc.kill()
            self._proc.wait()

    def restart(self) -> None:
        self.stop()
        self.start()


_shutting_down = False


def _shutdown_handler(signum: int, _frame: object) -> None:
    global _shutting_down
    _shutting_down = True


def main() -> None:
    parser = argparse.ArgumentParser(description="GaaS process supervisor")
    parser.add_argument("--dev", action="store_true", help="Enable uvicorn --reload")
    parser.add_argument("--expose", action="store_true",
                        help="Allow external connections (bind 0.0.0.0 instead of 127.0.0.1)")
    parser.add_argument("--port", type=int, default=6767, help="Port number (default: 6767)")
    args = parser.parse_args()

    host = "0.0.0.0" if args.expose else "127.0.0.1"
    python = sys.executable

    server_cmd = [python, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(args.port)]
    if args.dev:
        server_cmd.append("--reload")

    worker_cmd = [python, "-m", "app.worker"]

    server = ManagedProcess("server", server_cmd)
    worker = ManagedProcess("worker", worker_cmd)
    children = [server, worker]

    # Clean up stale sentinel from a previous run
    SENTINEL.unlink(missing_ok=True)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    for child in children:
        child.start()

    log.info(
        "Supervisor running (dev=%s, host=%s, port=%d). Press Ctrl+C to stop.",
        args.dev, host, args.port,
    )

    try:
        while not _shutting_down:
            # Check for restart sentinel
            if SENTINEL.exists():
                SENTINEL.unlink(missing_ok=True)
                log.info("Restart sentinel detected, restarting all children…")
                for child in children:
                    child.restart()

            # Watchdog: restart any child that exited unexpectedly
            for child in children:
                if not child.is_running and not _shutting_down:
                    log.warning("%s exited unexpectedly, restarting…", child.name)
                    child.start()

            time.sleep(POLL_INTERVAL)
    finally:
        log.info("Supervisor shutting down…")
        for child in children:
            child.stop()
        log.info("All children stopped")


if __name__ == "__main__":
    main()
