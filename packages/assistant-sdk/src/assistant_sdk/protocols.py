"""Protocol definitions for SDK contracts.

These protocols define the callable signatures used across the SDK
boundary — handler functions, value resolvers, and enqueue callbacks.
Integration code types against these instead of concrete implementations.
"""

from __future__ import annotations

from typing import Any, Protocol

from assistant_sdk.task import TaskRecord


class TaskHandler(Protocol):
    """Handler function for processing a queue task."""

    def __call__(self, task: TaskRecord) -> dict[str, Any] | None: ...


class ResolveValue(Protocol):
    """Platform-specific value resolver for automation conditions.

    Given a key and classification dict, returns the resolved value
    or the ``MISSING`` sentinel if the key cannot be resolved.
    """

    def __call__(self, key: str, classification: dict[str, Any]) -> Any: ...


class EnqueueFn(Protocol):
    """Enqueue a task payload into the queue.

    Returns the task ID string, or None when rejected by policy.
    """

    def __call__(
        self,
        payload: dict[str, Any],
        *,
        priority: int = ...,
        provenance: str | None = ...,
    ) -> str | None: ...
