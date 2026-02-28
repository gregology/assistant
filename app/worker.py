import logging
import signal
import time

import app.human_log  # noqa: F401 — registers log.human()
from app import queue
from app.actions.script import handle as script_run_handle
from app.automations import handle_automation
from app.loader import load_all_modules
from app.integrations import HANDLERS, register_all

log = logging.getLogger(__name__)

POLL_INTERVAL = 1  # seconds

_shutting_down = False


def _shutdown_handler(signum, frame):
    global _shutting_down
    _shutting_down = True
    log.info("Received signal %s, shutting down gracefully…", signum)


def handle(task: dict):
    task_type = task["payload"].get("type")
    handler = HANDLERS.get(task_type)
    if handler is None:
        log.warning("Unknown task type: %s", task_type)
        return
    handler(task)


def main():
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # Load integration modules and register handlers
    load_all_modules()
    register_all()
    HANDLERS["script.run"] = script_run_handle
    HANDLERS["automation.run"] = handle_automation

    queue.init()
    log.info("Worker started, polling every %ss", POLL_INTERVAL)

    while not _shutting_down:
        task = queue.dequeue()
        if task is None:
            time.sleep(POLL_INTERVAL)
            continue

        log.info("Dequeued task %s", task["id"])
        try:
            handle(task)
            queue.complete(task["id"])
            log.info("Completed task %s", task["id"])
        except Exception as exc:
            log.exception("Task %s failed", task["id"])
            queue.fail(task["id"], str(exc))

    log.info("Worker shut down gracefully")


if __name__ == "__main__":
    main()
