"""Tests for worker task dispatch."""

import pytest

from app.worker import handle


class TestHandle:
    def test_unknown_task_type_raises(self):
        """Unknown task types raise ValueError so they move to failed/."""
        task = {"payload": {"type": "nonexistent.task.type"}}
        with pytest.raises(ValueError, match="Unknown task type: nonexistent.task.type"):
            handle(task)

    def test_missing_task_type_raises(self):
        """Tasks without a type field raise ValueError."""
        task = {"payload": {}}
        with pytest.raises(ValueError, match="Unknown task type: None"):
            handle(task)
