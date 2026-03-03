import hashlib
import os
import tempfile
from pathlib import Path

import yaml
from hypothesis import settings as hp_settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from app import queue


def snapshot_tree(base: Path) -> dict:
    """Capture full directory state: file counts per subdirectory and total."""
    counts = {}
    for subdir in sorted(base.iterdir()):
        if subdir.is_dir():
            counts[subdir.name] = len(list(subdir.iterdir()))
    return {"counts": counts, "total": sum(counts.values())}


class TestQueueLifecycle:
    def test_enqueue_creates_pending_file(self, queue_dir):
        task_id = queue.enqueue({"type": "test"})
        snap = snapshot_tree(queue_dir)

        assert snap["counts"]["pending"] == 1
        assert snap["counts"]["active"] == 0
        assert snap["counts"]["done"] == 0
        assert snap["counts"]["failed"] == 0
        assert snap["total"] == 1

        pending_file = queue_dir / "pending" / f"{task_id}.yaml"
        task = yaml.safe_load(pending_file.read_text())
        assert task["status"] == "pending"
        assert task["payload"] == {"type": "test"}

    def test_dequeue_moves_to_active(self, queue_dir):
        queue.enqueue({"type": "test"})
        task = queue.dequeue()
        snap = snapshot_tree(queue_dir)

        assert task is not None
        assert task["status"] == "active"
        assert snap["counts"]["pending"] == 0
        assert snap["counts"]["active"] == 1

    def test_complete_moves_to_done(self, queue_dir):
        queue.enqueue({"type": "test"})
        task = queue.dequeue()
        queue.complete(task["id"])
        snap = snapshot_tree(queue_dir)

        assert snap["counts"]["pending"] == 0
        assert snap["counts"]["active"] == 0
        assert snap["counts"]["done"] == 1
        assert snap["counts"]["failed"] == 0
        assert snap["total"] == 1

        done_file = queue_dir / "done" / f"{task['id']}.yaml"
        done_task = yaml.safe_load(done_file.read_text())
        assert done_task["status"] == "done"
        assert "completed_at" in done_task

    def test_fail_moves_to_failed(self, queue_dir):
        queue.enqueue({"type": "test"})
        task = queue.dequeue()
        queue.fail(task["id"], "something broke")
        snap = snapshot_tree(queue_dir)

        assert snap["counts"]["pending"] == 0
        assert snap["counts"]["active"] == 0
        assert snap["counts"]["done"] == 0
        assert snap["counts"]["failed"] == 1

        failed_file = queue_dir / "failed" / f"{task['id']}.yaml"
        failed_task = yaml.safe_load(failed_file.read_text())
        assert failed_task["status"] == "failed"
        assert failed_task["error"] == "something broke"

    def test_task_conservation(self, queue_dir):
        ids = [queue.enqueue({"type": f"test_{i}"}) for i in range(5)]
        assert snapshot_tree(queue_dir)["total"] == 5

        tasks = [queue.dequeue() for _ in range(3)]
        assert snapshot_tree(queue_dir)["total"] == 5

        queue.complete(tasks[0]["id"])
        queue.complete(tasks[1]["id"])
        assert snapshot_tree(queue_dir)["total"] == 5

        queue.fail(tasks[2]["id"], "err")
        snap = snapshot_tree(queue_dir)
        assert snap["total"] == 5
        assert snap["counts"]["pending"] == 2
        assert snap["counts"]["active"] == 0
        assert snap["counts"]["done"] == 2
        assert snap["counts"]["failed"] == 1

    def test_dequeue_returns_none_when_empty(self, queue_dir):
        assert queue.dequeue() is None

    def test_dequeue_priority_ordering(self, queue_dir):
        queue.enqueue({"type": "low"}, priority=9)
        queue.enqueue({"type": "high"}, priority=1)
        queue.enqueue({"type": "mid"}, priority=5)

        first = queue.dequeue()
        second = queue.dequeue()
        third = queue.dequeue()

        assert first["payload"]["type"] == "high"
        assert second["payload"]["type"] == "mid"
        assert third["payload"]["type"] == "low"

    def test_complete_with_result(self, queue_dir):
        """Result dict is stored in the completed task YAML."""
        queue.enqueue({"type": "service.gemini.web_research"})
        task = queue.dequeue()
        result = {"text": "Research output", "sources": [{"title": "S1", "url": "https://s1.com"}]}
        queue.complete(task["id"], result=result)

        done_file = queue_dir / "done" / f"{task['id']}.yaml"
        done_task = yaml.safe_load(done_file.read_text())
        assert done_task["status"] == "done"
        assert done_task["result"] == result
        assert done_task["result"]["text"] == "Research output"
        assert len(done_task["result"]["sources"]) == 1

    def test_complete_without_result(self, queue_dir):
        """Completing without a result does not add a result field."""
        queue.enqueue({"type": "test"})
        task = queue.dequeue()
        queue.complete(task["id"])

        done_file = queue_dir / "done" / f"{task['id']}.yaml"
        done_task = yaml.safe_load(done_file.read_text())
        assert "result" not in done_task

    def test_concurrent_dequeue_simulation(self, queue_dir):
        task_id = queue.enqueue({"type": "test"})

        # Simulate another worker grabbing the file first
        pending_file = queue_dir / "pending" / f"{task_id}.yaml"
        os.remove(pending_file)

        assert queue.dequeue() is None


class QueueStateMachine(RuleBasedStateMachine):
    """Verify task conservation and uniqueness across all queue states."""

    def __init__(self):
        super().__init__()
        self.expected_total = 0
        self.active_ids: list[str] = []
        # Each run gets its own temp directory
        self._dir = tempfile.mkdtemp()
        from pathlib import Path

        self._base = Path(self._dir)
        for d in queue.DIRS:
            (self._base / d).mkdir()
        # Point queue at our temp directory
        queue.BASE_DIR = self._base

    def teardown(self):
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)

    @rule()
    def enqueue_task(self):
        queue.enqueue({"type": "stateful_test"})
        self.expected_total += 1

    @rule()
    def dequeue_task(self):
        task = queue.dequeue()
        if task is not None:
            self.active_ids.append(task["id"])

    @rule()
    def complete_active(self):
        if self.active_ids:
            task_id = self.active_ids.pop(0)
            queue.complete(task_id)

    @rule()
    def fail_active(self):
        if self.active_ids:
            task_id = self.active_ids.pop(0)
            queue.fail(task_id, "test failure")

    @invariant()
    def total_tasks_conserved(self):
        snap = snapshot_tree(queue.BASE_DIR)
        assert snap["total"] == self.expected_total, (
            f"Expected {self.expected_total} tasks, found {snap['total']}"
        )

    @invariant()
    def no_task_in_two_directories(self):
        all_ids = []
        for d in queue.DIRS:
            dir_path = queue.BASE_DIR / d
            if dir_path.exists():
                for f in dir_path.iterdir():
                    if f.suffix == ".yaml":
                        all_ids.append(f.stem)
        assert len(all_ids) == len(set(all_ids)), "Duplicate task ID across directories"


TestQueueStateMachine = QueueStateMachine.TestCase
TestQueueStateMachine.settings = hp_settings(max_examples=50, stateful_step_count=20)
