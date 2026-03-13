"""Google Gemini integration for Assistant.

Provides service-only capabilities (no platforms). Services are callable
from automation rules via the shared action layer.
"""

from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"
