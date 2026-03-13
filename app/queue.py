import contextlib
import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import yaml

from app.config import config
from assistant_sdk.task import TaskRecord

log = logging.getLogger(__name__)

BASE_DIR = Path(config.directories.task_queue)
DIRS = ("pending", "active", "done", "failed")


def init() -> None:
    for d in DIRS:
        (BASE_DIR / d).mkdir(parents=True, exist_ok=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def fingerprint(payload: dict[str, Any]) -> str:
    """Canonical JSON -> SHA-256 -> first 8 hex chars."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def task_type_from_payload(payload: dict[str, Any]) -> str:
    """Extract the task type string from a payload dict."""
    return str(payload.get("type", "unknown"))


def parse_filename(filename: str) -> dict[str, str] | None:
    """Parse a task filename into its components.

    Expected format: {priority}_{timestamp}_{uuid}--{fingerprint}--{task_type}.yaml
    Returns dict with keys: priority, timestamp, uuid, fingerprint, task_type
    or None if the filename doesn't match the expected format.
    """
    stem = filename.removesuffix(".yaml") if filename.endswith(".yaml") else filename

    parts = stem.split("--")
    if len(parts) != 3:
        return None

    prefix, fp, task_type = parts

    # prefix is {priority}_{timestamp}_{uuid}
    prefix_parts = prefix.split("_", 2)
    if len(prefix_parts) != 3:
        return None

    return {
        "priority": prefix_parts[0],
        "timestamp": prefix_parts[1],
        "uuid": prefix_parts[2],
        "fingerprint": fp,
        "task_type": task_type,
    }


def has_pending_duplicate(fp: str, task_type: str) -> bool:
    """Check if a task with the same fingerprint and type is already pending."""
    pending_dir = BASE_DIR / "pending"
    pattern = f"*--{fp}--{task_type}.yaml"
    return any(pending_dir.glob(pattern))


def count_recent(task_type: str, seconds: int) -> int:
    """Count tasks of a given type across all dirs within a time window."""
    cutoff = _now().timestamp() - seconds
    count = 0
    pattern = f"*--*--{task_type}.yaml"

    for d in DIRS:
        dir_path = BASE_DIR / d
        for f in dir_path.glob(pattern):
            parsed = parse_filename(f.name)
            if parsed is None:
                continue
            try:
                ts = datetime.strptime(parsed["timestamp"], "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=UTC
                )
                if ts.timestamp() >= cutoff:
                    count += 1
            except ValueError:
                continue

    return count


def _make_id(priority: int, fp: str, task_type: str) -> str:
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{priority}_{ts}_{short_uuid}--{fp}--{task_type}"


def enqueue(payload: dict[str, Any], priority: int = 5, provenance: str | None = None) -> str:
    fp = fingerprint(payload)
    task_type = task_type_from_payload(payload)
    task_id = _make_id(priority, fp, task_type)
    task = {
        "id": task_id,
        "created_at": _now().isoformat(),
        "status": "pending",
        "priority": priority,
        "payload": payload,
    }
    if provenance is not None:
        task["provenance"] = provenance
    path = BASE_DIR / "pending" / f"{task_id}.yaml"
    _atomic_write(path, yaml.dump(task, default_flow_style=False, sort_keys=False))
    return task_id


def dequeue() -> TaskRecord | None:
    pending_dir = BASE_DIR / "pending"
    files = sorted(f.name for f in pending_dir.iterdir() if f.suffix == ".yaml")

    for filename in files:
        src = pending_dir / filename
        dst = BASE_DIR / "active" / filename

        try:
            os.rename(src, dst)
        except FileNotFoundError:
            continue  # Another worker grabbed it, try next

        try:
            task = yaml.safe_load(dst.read_text())
        except Exception:
            log.warning("Corrupted task file %s, moving to failed/", filename)
            os.rename(dst, BASE_DIR / "failed" / filename)
            continue

        task["status"] = "active"
        _atomic_write(dst, yaml.dump(task, default_flow_style=False, sort_keys=False))
        return task  # type: ignore[no-any-return]

    return None


def complete(task_id: str, result: dict[str, Any] | None = None) -> None:
    filename = f"{task_id}.yaml"
    src = BASE_DIR / "active" / filename
    dst = BASE_DIR / "done" / filename

    task: TaskRecord = yaml.safe_load(src.read_text())
    task["status"] = "done"
    task["completed_at"] = _now().isoformat()
    if result is not None:
        task["result"] = result
    _atomic_write(dst, yaml.dump(task, default_flow_style=False, sort_keys=False))
    with contextlib.suppress(FileNotFoundError):
        src.unlink()


def fail(task_id: str, error: str) -> None:
    filename = f"{task_id}.yaml"
    src = BASE_DIR / "active" / filename
    dst = BASE_DIR / "failed" / filename

    task: TaskRecord = yaml.safe_load(src.read_text())
    task["status"] = "failed"
    task["failed_at"] = _now().isoformat()
    task["error"] = error
    _atomic_write(dst, yaml.dump(task, default_flow_style=False, sort_keys=False))
    with contextlib.suppress(FileNotFoundError):
        src.unlink()


def recover_stale_active() -> int:
    """Move stale tasks in active/ to failed/ at startup.

    On a single-host system, any task in active/ at startup was left by a
    crashed worker. Returns the number of recovered tasks.
    """
    active_dir = BASE_DIR / "active"
    recovered = 0

    for f in sorted(active_dir.iterdir()):
        if f.suffix != ".yaml":
            continue

        filename = f.name
        done_path = BASE_DIR / "done" / filename
        failed_path = BASE_DIR / "failed" / filename

        # Crash between atomic write to dest and unlink of source
        if done_path.exists() or failed_path.exists():
            log.info("Removing duplicate active/ file %s (already in done/ or failed/)", filename)
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
            recovered += 1
            continue

        # Orphaned task: move to failed with recovery error
        try:
            task = yaml.safe_load(f.read_text())
            task["status"] = "failed"
            task["failed_at"] = _now().isoformat()
            task["error"] = "Worker crashed while processing this task"
            _atomic_write(failed_path, yaml.dump(task, default_flow_style=False, sort_keys=False))
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
        except Exception:
            log.warning("Corrupted active/ file %s, moving to failed/ as-is", filename)
            os.rename(f, failed_path)

        recovered += 1

    if recovered:
        log.info("Recovered %d stale task(s) from active/", recovered)
    return recovered
