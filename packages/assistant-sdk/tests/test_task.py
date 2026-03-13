"""Smoke tests for TaskPayload and TaskRecord TypedDicts."""

from assistant_sdk.task import TaskPayload, TaskRecord


def test_imports():
    """TaskPayload and TaskRecord are importable from assistant_sdk.task."""
    assert TaskPayload is not None
    assert TaskRecord is not None


def test_top_level_reexport():
    """TaskPayload and TaskRecord are re-exported from assistant_sdk."""
    from assistant_sdk import TaskPayload as TP, TaskRecord as TR

    assert TP is TaskPayload
    assert TR is TaskRecord


def test_conforming_dict():
    """A dict matching the TaskRecord shape is accepted as valid."""
    record: TaskRecord = {
        "id": "5_20260304T120000Z_abcd1234--deadbeef--email.inbox.check",
        "created_at": "2026-03-04T12:00:00+00:00",
        "status": "pending",
        "priority": 5,
        "payload": {"type": "email.inbox.check", "integration": "email.personal"},
    }
    assert record["id"].startswith("5_")
    assert record["payload"]["type"] == "email.inbox.check"


def test_payload_without_integration():
    """TaskPayload works without the optional integration field."""
    payload: TaskPayload = {"type": "script.run"}
    assert payload["type"] == "script.run"
