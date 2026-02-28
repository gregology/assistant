import logging
from typing import Any
from jinja2 import Template

from app import queue
from app.integrations import HANDLERS

log = logging.getLogger(__name__)

def resolve_templates(data: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve Jinja2 templates in a data structure."""
    if isinstance(data, str):
        if "{{" in data or "{%" in data:
            return Template(data).render(**context)
        return data
    if isinstance(data, dict):
        return {k: resolve_templates(v, context) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve_templates(v, context) for v in data]
    return data

def run_action(action: Any, context: dict[str, Any], platform_payload: dict[str, Any]) -> Any:
    """Execute a single action and return its result.
    
    Actions can be:
    - A string (e.g., "archive"): Passed to the platform's act handler.
    - A dict with 'action': Global action (e.g., {"action": "gemini.research", "inputs": {...}})
    - A dict with 'script': Script action (backwards compatibility).
    """
    if isinstance(action, str):
        # Platform-specific shorthand
        # We need to send this back to the platform's act handler
        # Since we want to support chaining, we might need to wait for it or just enqueue it.
        # For simplicity in this first pass, if it's a string, we treat it as a platform action.
        payload = {**platform_payload, "actions": [action]}
        queue.enqueue(payload, priority=7) # TODO: get priority/provenance
        return None

    if isinstance(action, dict):
        if "action" in action:
            action_key = action["action"]
            handler = HANDLERS.get(action_key)
            if not handler:
                log.warning("Action handler not found: %s", action_key)
                return None
            
            inputs = action.get("inputs", {})
            resolved_inputs = resolve_templates(inputs, context)
            
            # Create a task-like object for the handler
            task = {
                "payload": {
                    "type": action_key,
                    "inputs": resolved_inputs,
                    **platform_payload
                }
            }
            log.info("Executing action: %s with inputs: %s", action_key, resolved_inputs)
            return handler(task)

        if "script" in action:
            # Script action (backwards compatibility)
            script_ref = action["script"]
            script_name = script_ref.get("name", "") if isinstance(script_ref, dict) else script_ref
            raw_inputs = script_ref.get("inputs", {}) if isinstance(script_ref, dict) else {}
            resolved_inputs = resolve_templates(raw_inputs, context)
            
            queue.enqueue({
                "type": "script.run",
                "script_name": script_name,
                "inputs": resolved_inputs,
            }, priority=7) # TODO
            return None

    log.warning("Unknown action format: %s", action)
    return None

def handle_automation(task: dict[str, Any]) -> None:
    """Worker handler for automation.run tasks."""
    payload = task["payload"]
    actions = payload.get("actions", [])
    context = payload.get("context", {})
    platform_payload = payload.get("platform_payload", {})
    
    log.info("Starting automation chain with %d actions", len(actions))
    
    for action in actions:
        # Check for 'register' keyword
        register_key = None
        if isinstance(action, dict) and "register" in action:
            # Create a copy to avoid modifying the config object
            action = action.copy()
            register_key = action.pop("register")
        
        result = run_action(action, context, platform_payload)
        
        if register_key and result is not None:
            context[register_key] = result
            log.info("Registered action result to context key: %s", register_key)

    log.info("Automation chain completed")
