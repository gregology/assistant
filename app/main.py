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


def _resolve_integration(integration_id: str) -> Any:
    """Look up integration by ID, raising HTTPException if not found."""
    try:
        return config.get_integration(integration_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Integration {integration_id!r} not found",
        ) from None


def _resolve_platforms(integration_id: str, integration: Any) -> Any:
    """Get the platforms object, raising HTTPException if absent."""
    platforms_obj = getattr(integration, "platforms", None)
    if platforms_obj is None:
        raise HTTPException(
            status_code=400,
            detail=f"Integration {integration_id!r} has no platforms configured",
        )
    return platforms_obj


def _enqueue_single_platform(
    integration_id: str, integration_type: str, platform: str, platforms_obj: Any
) -> str:
    """Validate and enqueue a single platform, returning its task ID."""
    if getattr(platforms_obj, platform, None) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Platform {platform!r} not configured in {integration_id!r}",
        )
    entry_task = ENTRY_TASKS.get(f"{integration_type}.{platform}")
    if entry_task is None:
        raise HTTPException(
            status_code=400,
            detail=f"No entry task for {integration_type}.{platform}",
        )
    payload = {"type": entry_task, "integration": integration_id, "platform": platform}
    return queue.enqueue(payload)


def _enqueue_all_platforms(
    integration_id: str, integration_type: str, platforms_obj: Any
) -> list[str]:
    """Enqueue entry tasks for all enabled platforms, returning task IDs."""
    task_ids: list[str] = []
    for platform_name in type(platforms_obj).model_fields:
        if getattr(platforms_obj, platform_name) is None:
            continue
        entry_task = ENTRY_TASKS.get(f"{integration_type}.{platform_name}")
        if entry_task is None:
            _log.warning("No entry task for %s.%s", integration_type, platform_name)
            continue
        payload = {"type": entry_task, "integration": integration_id, "platform": platform_name}
        task_ids.append(queue.enqueue(payload))
    return task_ids


def _run_integration(integration_id: str, platform: str | None = None) -> dict[str, Any]:
    """Shared logic for running integration platforms by composite ID."""
    integration = _resolve_integration(integration_id)
    platforms_obj = _resolve_platforms(integration_id, integration)

    if platform:
        task_ids = [_enqueue_single_platform(
            integration_id, integration.type, platform, platforms_obj,
        )]
    else:
        task_ids = _enqueue_all_platforms(integration_id, integration.type, platforms_obj)

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
