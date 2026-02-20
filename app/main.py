import logging

from fastapi import FastAPI, HTTPException

import app.human_log  # noqa: F401 — registers log.human()
from app import queue
from app.config import config, safety_warnings
from app.integrations import ENTRY_TASKS
from app.scheduler import init_schedules

_log = logging.getLogger(__name__)

app = FastAPI()
queue.init()
init_schedules(app)

for _w in safety_warnings:
    _log.human(_w)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/integrations")
async def list_integrations():
    return [
        {"name": i.name, "type": i.type}
        for i in config.integrations
    ]


@app.post("/integrations/{integration_type}/{name}/run")
async def run_integration(integration_type: str, name: str):
    try:
        integration = config.get_integration(name, integration_type)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Integration '{integration_type}/{name}' not found")

    entry_task = ENTRY_TASKS.get(integration.type)
    if entry_task is None:
        raise HTTPException(
            status_code=400,
            detail=f"No entry task for integration type: {integration.type}",
        )

    payload = {"type": entry_task, "integration": name}
    if hasattr(integration, "limit"):
        payload["limit"] = integration.limit

    task_id = queue.enqueue(payload)
    return {"task_id": task_id, "status": "pending"}
