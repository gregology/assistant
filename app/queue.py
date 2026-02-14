import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import cfg

BASE_DIR = Path(cfg("queue.data_dir", "data/queue"))
DIRS = ("pending", "active", "done", "failed")


def init():
    for d in DIRS:
        (BASE_DIR / d).mkdir(parents=True, exist_ok=True)


def _now():
    return datetime.now(timezone.utc)


def _make_id(priority: int) -> str:
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{priority}_{ts}_{short_uuid}"


def enqueue(payload: dict, priority: int = 5) -> str:
    task_id = _make_id(priority)
    task = {
        "id": task_id,
        "created_at": _now().isoformat(),
        "status": "pending",
        "priority": priority,
        "payload": payload,
    }
    path = BASE_DIR / "pending" / f"{task_id}.yaml"
    path.write_text(yaml.dump(task, default_flow_style=False, sort_keys=False))
    return task_id


def dequeue() -> dict | None:
    pending_dir = BASE_DIR / "pending"
    files = sorted(f.name for f in pending_dir.iterdir() if f.suffix == ".yaml")
    if not files:
        return None

    filename = files[0]
    src = pending_dir / filename
    dst = BASE_DIR / "active" / filename

    try:
        os.rename(src, dst)
    except FileNotFoundError:
        # Another worker grabbed it first
        return None

    task = yaml.safe_load(dst.read_text())
    task["status"] = "active"
    dst.write_text(yaml.dump(task, default_flow_style=False, sort_keys=False))
    return task


def complete(task_id: str):
    filename = f"{task_id}.yaml"
    src = BASE_DIR / "active" / filename
    dst = BASE_DIR / "done" / filename

    task = yaml.safe_load(src.read_text())
    task["status"] = "done"
    task["completed_at"] = _now().isoformat()
    src.write_text(yaml.dump(task, default_flow_style=False, sort_keys=False))

    os.rename(src, dst)


def fail(task_id: str, error: str):
    filename = f"{task_id}.yaml"
    src = BASE_DIR / "active" / filename
    dst = BASE_DIR / "failed" / filename

    task = yaml.safe_load(src.read_text())
    task["status"] = "failed"
    task["failed_at"] = _now().isoformat()
    task["error"] = error
    src.write_text(yaml.dump(task, default_flow_style=False, sort_keys=False))

    os.rename(src, dst)
