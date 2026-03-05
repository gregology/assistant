"""Tests for the process supervisor and restart endpoint."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.supervisor import SENTINEL, ManagedProcess

# ---------------------------------------------------------------------------
# ManagedProcess
# ---------------------------------------------------------------------------


class TestManagedProcess:
    def test_not_running_before_start(self):
        mp = ManagedProcess("test", [sys.executable, "-c", "pass"])
        assert not mp.is_running

    def test_start_and_is_running(self):
        mp = ManagedProcess("test", [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            mp.start()
            assert mp.is_running
        finally:
            mp.stop(timeout=5)

    def test_stop_terminates_process(self):
        mp = ManagedProcess("test", [sys.executable, "-c", "import time; time.sleep(30)"])
        mp.start()
        assert mp.is_running
        mp.stop(timeout=5)
        assert not mp.is_running

    def test_stop_noop_when_not_running(self):
        mp = ManagedProcess("test", [sys.executable, "-c", "pass"])
        mp.stop()  # should not raise

    def test_restart_lifecycle(self):
        mp = ManagedProcess("test", [sys.executable, "-c", "import time; time.sleep(30)"])
        mp.start()
        first_pid = mp._proc.pid
        mp.restart()
        try:
            assert mp.is_running
            assert mp._proc.pid != first_pid
        finally:
            mp.stop(timeout=5)

    def test_env_var_passed_to_child(self):
        mp = ManagedProcess(
            "test",
            [sys.executable, "-c", "import os, sys; sys.exit(0 if os.environ.get('GAAS_SUPERVISOR') == '1' else 1)"],
        )
        mp.start()
        mp._proc.wait(timeout=5)
        assert mp._proc.returncode == 0


# ---------------------------------------------------------------------------
# Sentinel file
# ---------------------------------------------------------------------------


class TestSentinelFile:
    def test_sentinel_path_is_in_project_root(self):
        project_root = Path(__file__).resolve().parent.parent
        assert SENTINEL.parent == project_root

    def test_sentinel_create_detect_delete(self, tmp_path):
        sentinel = tmp_path / ".gaas-restart"
        assert not sentinel.exists()
        sentinel.touch()
        assert sentinel.exists()
        sentinel.unlink()
        assert not sentinel.exists()


# ---------------------------------------------------------------------------
# Restart endpoint
# ---------------------------------------------------------------------------


class TestRestartEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_restart_creates_sentinel(self, client, tmp_path):
        sentinel = tmp_path / ".gaas-restart"
        with patch("app.ui.routes._SENTINEL", sentinel):
            response = client.post("/ui/system/restart")

        assert response.status_code == 200
        assert sentinel.exists()

    def test_restart_returns_standalone_html(self, client, tmp_path):
        sentinel = tmp_path / ".gaas-restart"
        with patch("app.ui.routes._SENTINEL", sentinel):
            response = client.post("/ui/system/restart")

        body = response.text
        assert "<!DOCTYPE html>" in body
        assert "Restarting GaaS" in body
        # Must have health-polling JS
        assert 'fetch("/")' in body
        # Standalone: should NOT contain base.html navbar
        assert "navbar" not in body
