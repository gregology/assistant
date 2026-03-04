"""TypedDict definitions for task payloads and queue records.

These define the canonical shape of task dicts that flow through the queue,
worker, result routes, and every integration handler. Structural typing only —
no runtime enforcement. Per-task-type fields (uid, org, repo, inputs, etc.)
are accessed via .get() and not enforced here.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class TaskPayload(TypedDict):
    """Shared fields present in every task payload.

    Per-task-type keys (uid, org, repo, inputs, on_result, etc.)
    are not declared here — handlers access them via .get().
    """

    type: str
    integration: NotRequired[str]


class TaskRecord(TypedDict):
    """A task as it exists on disk in the queue.

    Created by queue.enqueue(), read by queue.dequeue(), and passed
    through the worker to handler functions.

    The payload field is typed as dict[str, Any] rather than TaskPayload
    because handlers access per-task-type keys (uid, org, inputs, etc.)
    via .get(). TaskPayload documents the shared fields; this keeps
    dynamic access patterns working without mypy complaints.
    """

    id: str
    created_at: str
    status: str
    priority: int
    payload: dict[str, Any]
    provenance: NotRequired[str]
    completed_at: NotRequired[str]
    result: NotRequired[dict]
    failed_at: NotRequired[str]
    error: NotRequired[str]
