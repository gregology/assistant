from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi_crons import Crons

from app import queue
from app.config import config
from app.queue_policy import _parse_duration_seconds, policy_enqueue
from app.integrations import ENTRY_TASKS

log = logging.getLogger(__name__)


def interval_to_cron(interval: str) -> str:
    """Convert a friendly interval like '30m' or '2h' to a cron expression."""
    match = re.fullmatch(r"(\d+)\s*([mhd])", interval.strip().lower())
    if not match:
        raise ValueError(f"Invalid interval format: {interval!r} (expected e.g. '30m', '2h', '1d')")

    value, unit = int(match.group(1)), match.group(2)

    if unit == "m":
        if value < 1 or value > 59:
            raise ValueError(f"Minute interval must be 1-59, got {value}")
        return f"*/{value} * * * *"
    if unit == "h":
        if value < 1 or value > 23:
            raise ValueError(f"Hour interval must be 1-23, got {value}")
        return f"0 */{value} * * *"
    if unit == "d":
        if value != 1:
            raise ValueError(f"Day interval only supports '1d' (daily), got {value}d")
        return "0 0 * * *"

    raise ValueError(f"Unknown unit: {unit}")


def _resolve_cron_expr(schedule: Any) -> str | None:
    """Return the cron expression for a schedule, or None if not configured."""
    if schedule.cron:
        return schedule.cron  # type: ignore[no-any-return]
    if schedule.every:
        return interval_to_cron(schedule.every)
    return None


def _make_job(
    task_type: str, integration_id: str, platform_name: str
) -> Callable[[], None]:
    """Create a scheduled job closure that enqueues a task."""
    def job() -> None:
        payload = {
            "type": task_type,
            "integration": integration_id,
            "platform": platform_name,
        }
        log.info("Scheduled job: enqueueing %s", payload)
        policy_enqueue(payload)
    return job


def _register_platform_schedules(
    crons: Crons, integration: Any, expr: str
) -> None:
    """Register cron jobs for all enabled platforms in an integration."""
    platforms = getattr(integration, "platforms", None)
    if platforms is None:
        return

    for platform_name in type(platforms).model_fields:
        if getattr(platforms, platform_name) is None:
            continue
        entry_task_name = ENTRY_TASKS.get(f"{integration.type}.{platform_name}")
        if entry_task_name is None:
            log.warning("No entry task for %s.%s", integration.type, platform_name)
            continue
        name = f"{integration.id}_{platform_name}"
        crons.cron(expr, name=name)(_make_job(entry_task_name, integration.id, platform_name))
        log.info("Registered schedule: %s [%s]", name, expr)


def _make_prune_job(retention_seconds: int) -> Callable[[], None]:
    """Create a scheduled job that prunes old done/failed task files."""
    def job() -> None:
        log.info("Running scheduled queue prune (retention: %ds)", retention_seconds)
        queue.prune_completed(retention_seconds)
    return job


def init_schedules(app: FastAPI) -> Crons:
    crons = Crons(app)

    for integration in config.integrations:
        if integration.schedule is None:
            continue
        expr = _resolve_cron_expr(integration.schedule)
        if expr is None:
            continue
        _register_platform_schedules(crons, integration, expr)

    # Daily pruning of completed/failed tasks past retention
    retention_seconds = _parse_duration_seconds(config.queue_policies.retention)
    crons.cron("0 0 * * *", name="queue_prune")(
        _make_prune_job(retention_seconds)
    )
    retention = config.queue_policies.retention
    log.info("Registered queue prune schedule [0 0 * * *], retention: %s", retention)

    return crons
