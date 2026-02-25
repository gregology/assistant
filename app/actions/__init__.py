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
    """Partition actions into platform-specific and shared, enqueuing each appropriately.

    Script actions become individual script.run queue tasks.
    Remaining platform actions are bundled into a single platform act task.
    """
    platform_actions = []
    for action in actions:
        if is_script_action(action):
            script_ref = action["script"]
            script_name = script_ref.get("name", "") if isinstance(script_ref, dict) else script_ref
            raw_inputs = script_ref.get("inputs", {}) if isinstance(script_ref, dict) else {}
            resolved_inputs = resolve_script_inputs(raw_inputs, resolve_value, classification)
            queue.enqueue({
                "type": "script.run",
                "script_name": script_name,
                "inputs": resolved_inputs,
            }, priority=priority, provenance=provenance)
            log.info("Enqueued script.run for script=%s inputs=%s", script_name, resolved_inputs)
        else:
            platform_actions.append(action)

    if platform_actions:
        platform_payload["actions"] = platform_actions
        queue.enqueue(platform_payload, priority=priority, provenance=provenance)
