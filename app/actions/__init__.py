"""Shared action layer for cross-cutting action types.

Platform-specific actions (archive, draft_reply) are handled by each
platform's act.py. Shared actions (scripts) are partitioned out at
evaluate time and enqueued as independent queue tasks.
"""

from __future__ import annotations

import logging
from typing import Any

from app import queue

log = logging.getLogger(__name__)


def is_script_action(action: Any) -> bool:
    """Check if an action is a script action (dict with 'script' key)."""
    return isinstance(action, dict) and "script" in action


def resolve_script_inputs(
    raw_inputs: dict[str, str],
    resolve_value,
    classification: dict,
) -> dict[str, str]:
    """Resolve $field references in script inputs against the automation context.

    Literal values (no $ prefix) pass through as-is.
    Missing fields resolve to empty string with a warning.
    """
    resolved = {}
    for key, value in raw_inputs.items():
        if isinstance(value, str) and value.startswith("$"):
            field = value[1:]
            result = resolve_value(field, classification)
            from app.evaluate import MISSING
            if result is MISSING:
                log.warning("Script input '$%s' could not be resolved, using empty string", field)
                resolved[key] = ""
            else:
                resolved[key] = str(result)
        else:
            resolved[key] = str(value) if value is not None else ""
    return resolved


def enqueue_actions(
    actions: list,
    platform_payload: dict,
    resolve_value,
    classification: dict,
    provenance: str,
    priority: int = 7,
) -> None:
    """Enqueue an automation.run task with the full list of actions and context."""
    # Build the initial context for template resolution
    context = {
        **classification,  # Include all classification results directly
        "classification": classification, # And under the classification prefix
    }
    
    # Resolve all top-level fields that the resolver knows about
    # This is a bit of a hack to get snapshot data into the context
    # In a more robust system, we'd have a standard list of context keys per platform
    fields = [
        "domain", "from_address", "subject", "is_reply", "is_forward",
        "has_attachments", "is_read", "is_starred"
    ]
    for field in fields:
        val = resolve_value(field, classification)
        from app.evaluate import MISSING
        if val is not MISSING:
            context[field] = val

    queue.enqueue({
        "type": "automation.run",
        "actions": actions,
        "context": context,
        "platform_payload": platform_payload,
    }, priority=priority, provenance=provenance)
    log.info("Enqueued automation.run with %d actions", len(actions))
