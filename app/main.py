from typing import Any

from fastapi import FastAPI, HTTPException

import app.human_log
from app import queue
from app.config import config, safety_warnings
from app.runtime_init import register_runtime
from app.loader import load_all_modules
from app.integrations import ENTRY_TASKS, register_all
from app.ui import router as ui_router
from gaas_sdk.logging import get_logger

_log = get_logger(__name__)

# Register SDK runtime before loading integration modules
register_runtime()

# Load integration modules and register handlers
load_all_modules()
register_all()

app: FastAPI = FastAPI()  # type: ignore[no-redef]
app.include_router(ui_router)  # type: ignore[attr-defined]
queue.init()

from app.scheduler import init_schedules  # noqa: E402

init_schedules(app)  # type: ignore[arg-type]

for _w in safety_warnings:
    _log.human(_w)


@app.get("/")  # type: ignore[attr-defined, untyped-decorator]
async def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/integrations")  # type: ignore[attr-defined, untyped-decorator]
async def list_integrations() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i in config.integrations:
        entry: dict[str, Any] = {"id": i.id, "name": i.name, "type": i.type}
        platforms = getattr(i, "platforms", None)
        if platforms is not None:
            entry["platforms"] = [
                name for name in type(platforms).model_fields
                if getattr(platforms, name) is not None
            ]
        results.append(entry)
    return results


def _run_integration(integration_id: str, platform: str | None = None) -> dict[str, Any]:
    """Shared logic for running integration platforms by composite ID."""
    try:
        integration = config.get_integration(integration_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Integration {integration_id!r} not found",
        ) from None

    platforms_obj = getattr(integration, "platforms", None)
    if platforms_obj is None:
        raise HTTPException(
            status_code=400,
            detail=f"Integration {integration_id!r} has no platforms configured",
        )

    task_ids: list[str] = []

    if platform:
        plat = getattr(platforms_obj, platform, None)
        if plat is None:
            raise HTTPException(
                status_code=404,
                detail=f"Platform {platform!r} not configured in {integration_id!r}",
            )
        entry_task = ENTRY_TASKS.get(f"{integration.type}.{platform}")
        if entry_task is None:
            raise HTTPException(
                status_code=400,
                detail=f"No entry task for {integration.type}.{platform}",
            )
        payload = {"type": entry_task, "integration": integration_id, "platform": platform}
        task_ids.append(queue.enqueue(payload))
    else:
        for platform_name in type(platforms_obj).model_fields:
            if getattr(platforms_obj, platform_name) is None:
                continue
            entry_task = ENTRY_TASKS.get(f"{integration.type}.{platform_name}")
            if entry_task is None:
                _log.warning("No entry task for %s.%s", integration.type, platform_name)
                continue
            payload = {"type": entry_task, "integration": integration_id, "platform": platform_name}
            task_ids.append(queue.enqueue(payload))

    if not task_ids:
        raise HTTPException(
            status_code=400,
            detail=f"No entry tasks found for enabled platforms in {integration_id!r}",
        )

    return {"task_ids": task_ids, "status": "pending"}


@app.post("/integrations/{integration_id}/run")  # type: ignore[attr-defined, untyped-decorator]
async def run_all_platforms(integration_id: str) -> dict[str, Any]:
    return _run_integration(integration_id)


@app.post("/integrations/{integration_id}/{platform}/run")  # type: ignore[attr-defined, untyped-decorator]
async def run_platform(integration_id: str, platform: str) -> dict[str, Any]:
    return _run_integration(integration_id, platform)
