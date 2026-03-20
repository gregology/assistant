"""Result routing for service task output.

When a service handler returns data, the worker calls route_results()
to dispatch the result to one or more destinations based on the task's
on_result configuration.

Route types:
    note — Save to NoteStore as markdown with frontmatter + human log breadcrumb.
    (Future: chat_reply, webhook, etc.)
"""

from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import app.human_log  # noqa: F401 — registers HumanMarkdownHandler
from assistant_sdk import runtime
from assistant_sdk.logging import get_logger
from assistant_sdk.store import NoteStore
from assistant_sdk.task import TaskRecord

log = get_logger(__name__)


def route_results(result: dict[str, Any], task: TaskRecord) -> None:
    """Dispatch a handler's return value to configured result routes.

    Reads ``on_result`` from the task payload. Falls back to a ``note``
    route for service tasks that lack explicit routing.
    """
    routes = task["payload"].get("on_result")
    if routes is None:
        task_type = task["payload"].get("type", "")
        if task_type.startswith("service."):
            routes = [{"type": "note"}]
        else:
            return

    for route in routes:
        route_type = route.get("type")
        try:
            if route_type == "note":
                _route_note(result, task, route)
            elif route_type == "chat_reply":
                _route_chat_reply(result, task, route)
            else:
                log.warning("Unknown result route type: %s", route_type)
        except Exception:
            log.exception(
                "Failed to route result for task %s via %s",
                task.get("id"),
                route,
            )


def _route_chat_reply(
    result: dict[str, Any], task: TaskRecord, route_config: dict[str, Any],
) -> None:
    """Handle chat reply result routing.

    For the web UI channel, delivery is pull-based (client polls).
    This handler logs the interaction for the audit trail.
    """
    conversation_id = route_config.get("conversation_id", "unknown")
    content = result.get("content", "")
    log.human("Chat reply [%s]: %s", conversation_id[:8], content[:100])


def _route_note(result: dict[str, Any], task: TaskRecord, route_config: dict[str, Any]) -> Path:
    """Save result as a markdown note with frontmatter.

    Directory is derived from the task type unless overridden by
    ``path`` in route_config. Writes a human log breadcrumb pointing
    to the saved file.
    """
    payload = task["payload"]
    task_type = payload.get("type", "")
    notes_dir = Path(runtime.get_notes_dir())

    # Determine target directory
    custom_path = route_config.get("path")
    if custom_path:
        target_dir = notes_dir / custom_path
    else:
        # service.gemini.web_research -> services/gemini/web_research/
        parts = task_type.split(".", 2)
        if len(parts) == 3:
            target_dir = notes_dir / "services" / parts[1] / parts[2]
        else:
            target_dir = notes_dir / "services"

    store = NoteStore(target_dir)

    # Build filename: timestamp + short task ID
    now = datetime.now(UTC)
    timestamp = now.strftime("%Y_%m_%d__%H_%M_%S")
    task_id = task.get("id", "unknown")
    prefix = task_id.split("--")[0] if "--" in task_id else task_id
    short_id = prefix.rsplit("_", 1)[-1] if "_" in prefix else prefix[:8]
    filename = f"{timestamp}__{short_id}.md"

    # Separate text body from metadata fields
    text = result.get("text", "")
    fields = {}
    for key, value in result.items():
        if key != "text":
            fields[key] = value
    # Audit metadata set after result merge so service data cannot overwrite
    fields["service"] = task_type
    fields["integration"] = payload.get("integration", "")
    fields["inputs"] = payload.get("inputs", {})
    fields["completed_at"] = now.isoformat()

    filepath = store.save(filename, content=text, **fields)

    # Human log breadcrumb
    try:
        rel_path = filepath.relative_to(notes_dir)
    except ValueError:
        rel_path = filepath
    human_log_msg = payload.get("human_log")
    if human_log_msg:
        log.human("%s → %s", human_log_msg, rel_path)
    else:
        text_len = len(text)
        log.human(
            "%s: result saved (%s chars) → %s", task_type, f"{text_len:,}", rel_path,
        )

    return filepath
