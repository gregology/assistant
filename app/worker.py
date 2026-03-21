import signal
import time
from types import FrameType
from typing import Any

import app.human_log  # noqa: F401 — registers HumanMarkdownHandler
from app import queue
from app.actions.script import handle as script_run_handle
from app.result_routes import route_results
from app.runtime_init import register_runtime
from app.loader import load_all_modules
from app.integrations import HANDLERS, register_all
from assistant_sdk.logging import get_logger
from assistant_sdk.task import TaskRecord

log = get_logger(__name__)

POLL_INTERVAL = 1  # seconds

_shutting_down = False


def _shutdown_handler(signum: int, _frame: FrameType | None) -> None:
    global _shutting_down
    _shutting_down = True
    log.info("Received signal %s, shutting down gracefully…", signum)


def handle(task: TaskRecord) -> dict[str, Any] | None:
    task_type = task["payload"].get("type")
    handler = HANDLERS.get(str(task_type))
    if handler is None:
        raise ValueError(f"Unknown task type: {task_type}")
    return handler(task)


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # Register SDK runtime before loading integration modules
    register_runtime()

    # Load integration modules and register handlers
    load_all_modules()
    register_all()
    HANDLERS["script.run"] = script_run_handle

    from app.chat import chat_message_handler

    HANDLERS["chat.message"] = chat_message_handler

    queue.init()
    queue.recover_stale_active()
    log.info("Worker started, polling every %ss", POLL_INTERVAL)

    while not _shutting_down:
        task = queue.dequeue()
        if task is None:
            time.sleep(POLL_INTERVAL)
            continue

        log.info("Dequeued task %s", task["id"])
        try:
            result = handle(task)
            queue.complete(task["id"], result=result)
            log.info("Completed task %s", task["id"])
        except Exception as exc:
            log.exception("Task %s failed", task["id"])
            try:
                queue.fail(task["id"], str(exc))
            except Exception:
                log.exception("Failed to record failure for task %s", task["id"])
            continue

        if result is not None:
            try:
                route_results(result, task)
            except Exception:
                log.exception(
                    "Task %s completed but result routing failed; result preserved in done/",
                    task["id"],
                )

    log.info("Worker shut down gracefully")


if __name__ == "__main__":
    main()
