"""Shared action layer for cross-cutting action types.

Platform-specific actions (archive, draft_reply) are handled by each
platform's act.py. Shared actions (scripts, services) are partitioned
out at evaluate time and enqueued as independent queue tasks.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict, TypeGuard

from jinja2 import ChainableUndefined, meta
from jinja2.sandbox import SandboxedEnvironment

from assistant_sdk import runtime
from assistant_sdk.evaluate import MISSING
from assistant_sdk.models import (
    ActionType,
    DictAction,
    ScriptAction,
    ServiceAction,
    SimpleAction,
    YoloAction,
)
from assistant_sdk.protocols import ResolveValue


class ScriptActionDict(TypedDict):
    script: str | dict[str, Any]


class ServiceActionDict(TypedDict):
    service: dict[str, Any]

log = logging.getLogger(__name__)

_jinja_env = SandboxedEnvironment(undefined=ChainableUndefined)

_JINJA_MARKERS = ("{{", "{%", "{#")


def _build_context(
    template_source: str,
    resolve_value: ResolveValue,
    classification: dict[str, Any],
) -> dict[str, Any]:
    """Build a template context dict by resolving referenced variables.

    Parses the template to discover undeclared variables, then resolves
    each via the platform resolver.  ``classification`` is always
    available for dot-access (``{{ classification.human }}``).
    """
    ast = _jinja_env.parse(template_source)
    variables = meta.find_undeclared_variables(ast)
    ctx: dict[str, Any] = {"classification": classification}
    for var in variables:
        if var == "classification":
            continue
        result = resolve_value(var, classification)
        if result is not MISSING:
            ctx[var] = result
    return ctx


def is_script_action(action: str | dict[str, Any]) -> TypeGuard[ScriptActionDict]:
    """Check if an action is a script action (dict with 'script' key)."""
    return isinstance(action, dict) and "script" in action


def is_service_action(action: str | dict[str, Any]) -> TypeGuard[ServiceActionDict]:
    """Check if an action is a service action (dict with 'service' key)."""
    return isinstance(action, dict) and "service" in action


def _render_template(
    template_str: str,
    resolve_value: ResolveValue,
    classification: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> str:
    """Render a single Jinja2 template string against the automation context.

    ``extra`` is merged into the context after resolver variables, allowing
    already-resolved inputs to be referenced (e.g. ``{{ prompt }}``).
    """
    if not any(m in template_str for m in _JINJA_MARKERS):
        return template_str
    ctx = _build_context(template_str, resolve_value, classification)
    if extra:
        ctx.update(extra)
    return _jinja_env.from_string(template_str).render(ctx)


def resolve_inputs(
    raw_inputs: dict[str, str],
    resolve_value: ResolveValue,
    classification: dict[str, Any],
) -> dict[str, str]:
    """Resolve ``{{ field }}`` Jinja2 templates in script/service inputs.

    Supports Jinja2 expressions (``{{ domain }}``), filters
    (``{{ domain | upper }}``), and conditionals (``{% if ... %}``).
    Values without Jinja2 markers pass through unchanged.  Missing
    variables render as empty string via ``ChainableUndefined``.

    Uses ``SandboxedEnvironment`` to block attribute access on
    internal Python objects.
    """
    resolved = {}
    for key, value in raw_inputs.items():
        if not isinstance(value, str) or not any(m in value for m in _JINJA_MARKERS):
            resolved[key] = str(value) if value is not None else ""
            continue

        ctx = _build_context(value, resolve_value, classification)
        template = _jinja_env.from_string(value)
        resolved[key] = template.render(ctx)
    return resolved


def _action_to_dict(action: ActionType) -> str | dict[str, Any]:
    """Convert an action model back to its raw dict/string form for payloads."""
    if isinstance(action, SimpleAction):
        return action.action
    if isinstance(action, ScriptAction):
        return {"script": action.script}
    if isinstance(action, ServiceAction):
        return {"service": action.service}
    if isinstance(action, DictAction):
        return action.data
    return action


def _unwrap_yolo(action: ActionType | YoloAction) -> tuple[ActionType, bool]:
    """Unwrap a potential YoloAction, returning (inner_action, is_yolo)."""
    if isinstance(action, YoloAction):
        from assistant_sdk.models import _normalize_action
        return _normalize_action(action.value), True
    return action, False


def _action_to_payload(action: ActionType, yolo: bool) -> str | dict[str, Any]:
    """Convert an action model to its payload form, preserving !yolo marker."""
    raw = _action_to_dict(action)
    if yolo:
        return {"!yolo": raw}
    return raw


def _enqueue_script(
    inner: ScriptAction,
    resolve_value: ResolveValue,
    classification: dict[str, Any],
    provenance: str,
    priority: int,
) -> None:
    """Enqueue a single script.run task."""
    script_ref = inner.script
    script_name = script_ref.get("name", "") if isinstance(script_ref, dict) else script_ref
    raw_inputs = script_ref.get("inputs", {}) if isinstance(script_ref, dict) else {}
    resolved_inputs = resolve_inputs(raw_inputs, resolve_value, classification)
    runtime.enqueue({
        "type": "script.run",
        "script_name": script_name,
        "inputs": resolved_inputs,
    }, priority=priority, provenance=provenance)
    log.info("Enqueued script.run for script=%s inputs=%s", script_name, resolved_inputs)


def _enqueue_service(
    inner: ServiceAction,
    resolve_value: ResolveValue,
    classification: dict[str, Any],
    provenance: str,
    priority: int,
) -> None:
    """Enqueue a single service task."""
    service_ref = inner.service
    call = service_ref.get("call", "")
    raw_inputs = service_ref.get("inputs", {})
    resolved_inputs = resolve_inputs(raw_inputs, resolve_value, classification)
    # Parse call: {type}.{name}.{service_name}
    parts = call.rsplit(".", 2)
    if len(parts) != 3:
        log.warning("Invalid service call format: %r (expected type.name.service)", call)
        return
    svc_type, svc_name, service_name = parts
    on_result = service_ref.get("on_result", [{"type": "note"}])
    payload: dict[str, Any] = {
        "type": f"service.{svc_type}.{service_name}",
        "integration": f"{svc_type}.{svc_name}",
        "inputs": resolved_inputs,
        "on_result": on_result,
    }
    raw_human_log = service_ref.get("human_log") or runtime.get_service_log_template(
        f"service.{svc_type}.{service_name}"
    )
    if raw_human_log:
        payload["human_log"] = _render_template(
            raw_human_log, resolve_value, classification, extra=resolved_inputs,
        )
    runtime.enqueue(payload, priority=priority, provenance=provenance)
    log.info("Enqueued service.%s.%s for integration=%s.%s inputs=%s",
             svc_type, service_name, svc_type, svc_name, resolved_inputs)


def enqueue_actions(
    actions: list[ActionType | YoloAction],
    platform_payload: dict[str, Any],
    resolve_value: ResolveValue,
    classification: dict[str, Any],
    provenance: str,
    priority: int = 7,
) -> None:
    """Partition actions into platform-specific and shared, enqueuing each appropriately.

    Script actions become individual script.run queue tasks.
    Service actions become individual service.* queue tasks.
    Remaining platform actions are bundled into a single platform act task.

    YoloAction wrappers are preserved as ``{"!yolo": raw_action}`` in the
    payload so that runtime provenance checks can honour the override.
    """
    platform_actions: list[str | dict[str, Any]] = []
    for action in actions:
        inner, yolo = _unwrap_yolo(action)
        if isinstance(inner, ScriptAction):
            _enqueue_script(inner, resolve_value, classification, provenance, priority)
        elif isinstance(inner, ServiceAction):
            _enqueue_service(inner, resolve_value, classification, provenance, priority)
        else:
            platform_actions.append(_action_to_payload(inner, yolo))

    if platform_actions:
        platform_payload["actions"] = platform_actions
        runtime.enqueue(platform_payload, priority=priority, provenance=provenance)
