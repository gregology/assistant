from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi_crons import Crons

from app.config import config
from app.queue_policy import policy_enqueue
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


def init_schedules(app: FastAPI) -> Crons:
    crons = Crons(app)

    for integration in config.integrations:
        if integration.schedule is None:
            continue

        platforms = getattr(integration, "platforms", None)
        if platforms is None:
            continue

        schedule = integration.schedule
        if schedule.cron:
            expr = schedule.cron
        elif schedule.every:
            expr = interval_to_cron(schedule.every)
        else:
            continue

        for platform_name in type(platforms).model_fields:
            platform = getattr(platforms, platform_name)
            if platform is None:
                continue

            entry_task_name: str | None = ENTRY_TASKS.get(f"{integration.type}.{platform_name}")
            if entry_task_name is None:
                log.warning(
                    "No entry task for %s.%s", integration.type, platform_name
                )
                continue

            name = f"{integration.id}_{platform_name}"
            _task_type: str = entry_task_name

            def make_job(
                task_type: str = _task_type,
                int_entry: Any = integration,
                plat_name: str = platform_name,
            ) -> Callable[[], None]:
                def job() -> None:
                    payload = {
                        "type": task_type,
                        "integration": int_entry.id,
                        "platform": plat_name,
                    }
                    log.info("Scheduled job: enqueueing %s", payload)
                    policy_enqueue(payload)
                return job

            crons.cron(expr, name=name)(make_job())
            log.info("Registered schedule: %s [%s]", name, expr)

    return crons
