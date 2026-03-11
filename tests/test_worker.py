"""Tests for worker task dispatch and lifecycle."""

import logging
from unittest.mock import patch, MagicMock

import pytest
import yaml

from app import queue
from app.integrations import HANDLERS
from app.worker import handle


def _snapshot(queue_dir):
    """Return file counts per queue subdirectory."""
    return {d: len(list((queue_dir / d).glob("*.yaml"))) for d in queue.DIRS}


class TestHandle:
    def test_unknown_task_type_raises(self):
        """Unknown task types raise ValueError so they move to failed/."""
        task = {"payload": {"type": "nonexistent.task.type"}}
        with pytest.raises(ValueError, match=r"Unknown task type: nonexistent\.task\.type"):
            handle(task)

    def test_missing_task_type_raises(self):
        """Tasks without a type field raise ValueError."""
        task = {"payload": {}}
        with pytest.raises(ValueError, match="Unknown task type: None"):
            handle(task)

    def test_dispatches_to_registered_handler(self):
        """handle() routes to the correct handler and returns its result."""
        sentinel = {"answer": 42}
        handler = MagicMock(return_value=sentinel)
        HANDLERS["test.dispatch"] = handler
        try:
            task = {"payload": {"type": "test.dispatch"}}
            result = handle(task)
            handler.assert_called_once_with(task)
            assert result is sentinel
        finally:
            HANDLERS.pop("test.dispatch", None)


class TestWorkerLoop:
    """Tests for the worker's main loop logic (inlined, not running main())."""

    def _run_one_task(self, queue_dir):
        """Dequeue one task and run the worker's try/except logic from main().

        Returns (task, result) on success, or (task, exception) on failure.
        """
        task = queue.dequeue()
        assert task is not None, "Expected a task in the queue"

        try:
            result = handle(task)
            queue.complete(task["id"], result=result)
        except Exception as exc:
            try:
                queue.fail(task["id"], str(exc))
            except Exception:
                logging.getLogger("app.worker").exception(
                    "Failed to record failure for task %s", task["id"]
                )
            return task, exc

        if result is not None:
            try:
                from app.result_routes import route_results
                route_results(result, task)
            except Exception:
                logging.getLogger("app.worker").exception(
                    "Task %s completed but result routing failed; "
                    "result preserved in done/",
                    task["id"],
                )

        return task, result

    def test_happy_path_handler_returns_result(self, queue_dir):
        """Handler returns result -> complete() called -> task in done/."""
        HANDLERS["test.happy"] = lambda t: {"output": "ok"}
        try:
            queue.enqueue({"type": "test.happy"})
            assert _snapshot(queue_dir) == {"pending": 1, "active": 0, "done": 0, "failed": 0}

            _task, result = self._run_one_task(queue_dir)

            assert result == {"output": "ok"}
            assert _snapshot(queue_dir) == {"pending": 0, "active": 0, "done": 1, "failed": 0}

            # Result is persisted in the done/ YAML
            done_files = list((queue_dir / "done").glob("*.yaml"))
            done_task = yaml.safe_load(done_files[0].read_text())
            assert done_task["result"] == {"output": "ok"}
            assert done_task["status"] == "done"
        finally:
            HANDLERS.pop("test.happy", None)

    def test_handler_returns_none(self, queue_dir):
        """Handler returns None -> complete() called without result, no routing."""
        HANDLERS["test.none"] = lambda t: None
        try:
            queue.enqueue({"type": "test.none"})
            _task, result = self._run_one_task(queue_dir)

            assert result is None
            assert _snapshot(queue_dir) == {"pending": 0, "active": 0, "done": 1, "failed": 0}

            # No result key in done/ YAML
            done_files = list((queue_dir / "done").glob("*.yaml"))
            done_task = yaml.safe_load(done_files[0].read_text())
            assert "result" not in done_task
        finally:
            HANDLERS.pop("test.none", None)

    def test_handler_raises_moves_to_failed(self, queue_dir):
        """Handler exception -> task moves to failed/ with error message."""
        HANDLERS["test.boom"] = MagicMock(side_effect=RuntimeError("handler exploded"))
        try:
            queue.enqueue({"type": "test.boom"})
            _task, exc = self._run_one_task(queue_dir)

            assert isinstance(exc, RuntimeError)
            assert _snapshot(queue_dir) == {"pending": 0, "active": 0, "done": 0, "failed": 1}

            # Error is recorded in the failed/ YAML
            failed_files = list((queue_dir / "failed").glob("*.yaml"))
            failed_task = yaml.safe_load(failed_files[0].read_text())
            assert failed_task["status"] == "failed"
            assert "handler exploded" in failed_task["error"]
        finally:
            HANDLERS.pop("test.boom", None)

    def test_route_results_failure_preserves_done_state(self, queue_dir, caplog):
        """route_results raises -> task stays in done/, error logged."""
        HANDLERS["test.route_fail"] = lambda t: {"text": "important data"}
        try:
            queue.enqueue({"type": "test.route_fail"})

            with (
                patch("app.result_routes.route_results", side_effect=RuntimeError("routing boom")),
                caplog.at_level(logging.ERROR),
            ):
                _task, result = self._run_one_task(queue_dir)

            # Task completed successfully despite routing failure
            assert result == {"text": "important data"}
            assert _snapshot(queue_dir) == {"pending": 0, "active": 0, "done": 1, "failed": 0}
            assert "result routing failed" in caplog.text
        finally:
            HANDLERS.pop("test.route_fail", None)


class TestWorkerResilience:
    def test_fail_resilience(self, queue_dir, caplog):
        """queue.fail() raising doesn't crash the worker loop."""
        queue.enqueue({"type": "test"})
        task = queue.dequeue()

        with caplog.at_level(logging.ERROR), \
             patch("app.worker.handle", side_effect=RuntimeError("handler boom")), \
             patch("app.queue.fail", side_effect=OSError("disk full")):
                    try:
                        handle(task)
                    except Exception as exc:
                        try:
                            queue.fail(task["id"], str(exc))
                        except Exception:
                            logging.getLogger("app.worker").exception(
                                "Failed to record failure for task %s", task["id"]
                            )

        assert "Failed to record failure" in caplog.text


class TestRecoverStaleActive:
    def test_orphaned_active_moves_to_failed(self, queue_dir):
        """Tasks left in active/ at startup are recovered to failed/."""
        queue.enqueue({"type": "test.stale"})
        queue.dequeue()
        assert _snapshot(queue_dir) == {"pending": 0, "active": 1, "done": 0, "failed": 0}

        # Simulate crash: task stays in active/
        recovered = queue.recover_stale_active()

        assert recovered == 1
        assert _snapshot(queue_dir) == {"pending": 0, "active": 0, "done": 0, "failed": 1}

        failed_files = list((queue_dir / "failed").glob("*.yaml"))
        failed_task = yaml.safe_load(failed_files[0].read_text())
        assert failed_task["status"] == "failed"
        assert "crashed" in failed_task["error"].lower()

    def test_duplicate_active_done_cleaned(self, queue_dir):
        """If task exists in both active/ and done/, active/ copy is removed."""
        queue.enqueue({"type": "test.dup"})
        task = queue.dequeue()
        task_id = task["id"]

        # Simulate: complete() wrote to done/ but didn't unlink active/
        queue.complete(task_id, result={"ok": True})
        # Manually recreate the active/ file to simulate the race
        active_path = queue_dir / "active" / f"{task_id}.yaml"
        active_path.write_text(yaml.dump(task))

        assert (queue_dir / "done" / f"{task_id}.yaml").exists()
        assert active_path.exists()

        recovered = queue.recover_stale_active()
        assert recovered == 1
        assert not active_path.exists()
        assert (queue_dir / "done" / f"{task_id}.yaml").exists()

    def test_no_stale_tasks(self, queue_dir):
        """Empty active/ returns 0."""
        assert queue.recover_stale_active() == 0


class TestSignalHandler:
    def test_shutdown_flag_set(self):
        """_shutdown_handler sets the _shutting_down flag."""
        import app.worker as w

        original = w._shutting_down
        try:
            w._shutting_down = False
            w._shutdown_handler(15, None)  # SIGTERM
            assert w._shutting_down is True
        finally:
            w._shutting_down = original
