"""Shared automation evaluation engine.

This module contains the core logic for evaluating when/then automation
rules against classification results and snapshot data. It is
infrastructure — the same category as provenance resolution in config.py.

Platform-specific code (snapshot construction, value resolution) stays in
each platform's evaluate.py. This module provides the generic evaluation
functions that all platforms share.
"""

from __future__ import annotations

import logging
import operator
import re
from datetime import datetime, timezone
from typing import Any

from app.config import (
    AutomationConfig,
    ClassificationConfig,
    YoloAction,
    resolve_provenance,
)

log = logging.getLogger(__name__)

_OPS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

_OP_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*(\d+\.?\d*)\s*$")
_NOW_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*now\(\)\s*$")

MISSING = object()


def eval_operator(value: float, expr: str) -> bool:
    match = _OP_RE.match(expr)
    if not match:
        log.warning("Invalid confidence condition: %r", expr)
        return False
    op_fn = _OPS[match.group(1)]
    threshold = float(match.group(2))
    return op_fn(value, threshold)


def eval_now_operator(value: str, expr: str) -> bool:
    """Evaluate a now() comparison against an ISO 8601 datetime string.

    Supports date-only strings (e.g. "2026-01-15") which are treated as
    midnight UTC. Useful for calendar.end and calendar.start conditions.
    """
    match = _NOW_RE.match(expr)
    if not match:
        return False
    op_fn = _OPS[match.group(1)]
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        log.warning("Cannot parse datetime for now() comparison: %r", value)
        return False
    return op_fn(dt, datetime.now(timezone.utc))


def check_condition(value: Any, condition: Any, cls_config: ClassificationConfig) -> bool:
    if cls_config.type == "boolean":
        return value is condition

    if cls_config.type == "confidence":
        if isinstance(condition, (int, float)):
            return value >= condition
        if isinstance(condition, str):
            return eval_operator(value, condition)
        return False

    if cls_config.type == "enum":
        if isinstance(condition, list):
            return value in condition
        return value == condition

    return False


def check_deterministic_condition(value: Any, condition: Any) -> bool:
    if isinstance(condition, str) and _NOW_RE.match(condition):
        return eval_now_operator(value, condition)
    if isinstance(condition, bool):
        return value is condition
    if isinstance(condition, list):
        return value in condition
    return value == condition


def conditions_match(
    when: dict,
    resolve_value,
    classification: dict,
    classifications: dict[str, ClassificationConfig],
) -> bool:
    """Evaluate whether all conditions in a when dict match.

    resolve_value is a callable (key, classification) -> value that handles
    platform-specific value resolution. It should return MISSING if the
    key cannot be resolved.
    """
    for key, condition in when.items():
        value = resolve_value(key, classification)
        if value is MISSING:
            return False

        if key.startswith("classification."):
            cls_key = key[len("classification."):]
            if cls_key not in classifications:
                return False
            if not check_condition(value, condition, classifications[cls_key]):
                return False
        else:
            if not check_deterministic_condition(value, condition):
                return False
    return True


def evaluate_automations(
    automations: list[AutomationConfig],
    resolve_value,
    classification: dict,
    classifications: dict[str, ClassificationConfig],
) -> list:
    """Evaluate all automations and return the list of triggered actions.

    resolve_value is a callable (key, classification) -> value that handles
    platform-specific value resolution.
    """
    actions = []
    for automation in automations:
        if conditions_match(automation.when, resolve_value, classification, classifications):
            actions.extend(automation.then)
    return actions


def resolve_action_provenance(
    automations: list[AutomationConfig],
    resolve_value,
    classification: dict,
    classifications: dict[str, ClassificationConfig],
    deterministic_sources: frozenset[str],
) -> str:
    """Compute the aggregate provenance for all matching automations."""
    provenances = set()
    for automation in automations:
        if conditions_match(automation.when, resolve_value, classification, classifications):
            provenances.add(resolve_provenance(automation.when, deterministic_sources))
    if "llm" in provenances or "hybrid" in provenances:
        return "hybrid" if "rule" in provenances else "llm"
    return "rule"


def unwrap_actions(actions: list) -> list:
    """Unwrap YoloAction wrappers, returning plain action values."""
    return [a.value if isinstance(a, YoloAction) else a for a in actions]
