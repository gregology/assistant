from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure a minimal config.yaml exists before any app modules are imported.
# config.py loads eagerly at import time, so this must happen at module level.
# config.yaml is gitignored so this won't affect the repo.
# ---------------------------------------------------------------------------

_project_root = Path(__file__).parent.parent
_config_path = _project_root / "config.yaml"

if not _config_path.exists():
    _config_path.write_text(
        "llms:\n"
        "  default:\n"
        "    model: test-model\n"
    )

from app import queue
from app.runtime_init import register_runtime

register_runtime()


@pytest.fixture
def queue_dir(tmp_path, monkeypatch):
    """Isolated queue directory with all subdirectories created."""
    for d in queue.DIRS:
        (tmp_path / d).mkdir()
    monkeypatch.setattr(queue, "BASE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def notes_dir(tmp_path):
    """Isolated directory for NoteStore operations."""
    return tmp_path
