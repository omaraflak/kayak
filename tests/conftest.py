"""Shared test configuration.

Tests point the app at a throwaway data directory before importing anything from
``backend.app``: importing ``config`` creates directories and reads settings.json as
an import side effect, and the suite must not touch a developer's real workspace.
"""

import os
from pathlib import Path
import sys
import tempfile

_TMP_DATA_DIR = Path(tempfile.mkdtemp(prefix="kayak_tests_"))
os.environ.setdefault("KAYAK_DATA_DIR", str(_TMP_DATA_DIR))

# Ensure the repository root is importable as `backend.app.*`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
