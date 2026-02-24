import logging

from fastapi import FastAPI, HTTPException

import app.human_log  # noqa: F401 — registers log.human()
from app import queue
from app.config import config, safety_warnings
from app.loader import load_all_modules
from app.integrations import ENTRY_TASKS, register_all

_log = logging.getLogger(__name__)

# Load integration modules and register handlers
load_all_modules()
register_all()

app = FastAPI()
queue.init()

from app.scheduler import init_schedules

init_schedules(app)

for _w in safety_warnings:
    _log.human(_w)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/integrations")
async def list_integrations():
    results = []
    for i in config.integrations:
        entry = {"name": i.name, "type": i.type}
        platforms = getattr(i, "platforms", None)
        if platforms is not None:
            entry["platforms"] = [
                name for name in type(platforms).model_fields
                if getattr(platforms, name) is not None
            ]
        results.append(entry)
    return results


@app.post("/integrations/{integration_type}/{name}/run")
async def run_integration(integration_type: str, name: str, platform: str | None = None):
    try:
        integration = config.get_integration(name, integration_type)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Integration '{integration_type}/{name}' not found")

    platforms_obj = getattr(integration, "platforms", None)
    if platforms_obj is None:
        raise HTTPException(
            status_code=400,
            detail=f"Integration '{integration_type}/{name}' has no platforms configured",
        )

    task_ids = []

    if platform:
        # Target a specific platform
        plat = getattr(platforms_obj, platform, None)
        if plat is None:
            raise HTTPException(
                status_code=404,
                detail=f"Platform '{platform}' not configured in {integration_type}/{name}",
            )
        entry_task = ENTRY_TASKS.get(f"{integration.type}.{platform}")
        if entry_task is None:
            raise HTTPException(
                status_code=400,
                detail=f"No entry task for {integration.type}.{platform}",
            )
        payload = {"type": entry_task, "integration": name, "platform": platform}
        task_ids.append(queue.enqueue(payload))
    else:
        # Enqueue all enabled platforms
        for platform_name in type(platforms_obj).model_fields:
            if getattr(platforms_obj, platform_name) is None:
                continue
            entry_task = ENTRY_TASKS.get(f"{integration.type}.{platform_name}")
            if entry_task is None:
                _log.warning("No entry task for %s.%s", integration.type, platform_name)
                continue
            payload = {"type": entry_task, "integration": name, "platform": platform_name}
            task_ids.append(queue.enqueue(payload))

    if not task_ids:
        raise HTTPException(
            status_code=400,
            detail=f"No entry tasks found for enabled platforms in {integration_type}/{name}",
        )

    return {"task_ids": task_ids, "status": "pending"}
