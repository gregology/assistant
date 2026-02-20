"""Shared condition evaluation primitives for automation dispatch.

Pure computation — no I/O, no side effects, no integration-specific knowledge.
Both email and GitHub evaluate handlers import from here.
"""

import logging
import operator
import re
from datetime import datetime, timezone

from app.config import ClassificationConfig

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


def check_condition(value, condition, cls_config: ClassificationConfig) -> bool:
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


def check_deterministic_condition(value, condition) -> bool:
    if isinstance(condition, str) and _NOW_RE.match(condition):
        return eval_now_operator(value, condition)
    if isinstance(condition, bool):
        return value is condition
    if isinstance(condition, list):
        return value in condition
    return value == condition
