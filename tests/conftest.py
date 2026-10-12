"""Shared test configuration.

Tests point the app at a throwaway data directory before importing anything from
``backend.app``: importing ``config`` creates directories and reads settings.json as
an import side effect, and the suite must not touch a developer's real workspace.
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile

_REPO = Path(__file__).resolve().parent.parent
_TMP_DATA_DIR = Path(tempfile.mkdtemp(prefix="kayak_tests_"))
os.environ.setdefault("KAYAK_DATA_DIR", str(_TMP_DATA_DIR))

# A new install starts with the default agents the image ships in data/agents.
shutil.copytree(_REPO / "data" / "agents", _TMP_DATA_DIR / "agents")

# Ensure the repository root is importable as `backend.app.*`.
sys.path.insert(0, str(_REPO))

import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def isolated_runs_locally(monkeypatch):
    """Runs "throwaway sandbox" commands in a local temporary directory instead.

    The real runner starts a container from the sandbox image, which a unit test
    cannot assume; what is under test is what happens around the run.
    """
    from backend.app.agent.sandbox import IsolatedRun, sandbox_manager

    async def run_locally(files, command, timeout):
        with tempfile.TemporaryDirectory() as directory:
            for name, text in files.items():
                (Path(directory) / name).write_text(text, encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *command[1:],
                cwd=directory,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            except asyncio.TimeoutError:
                process.kill()
                return IsolatedRun(exit_code=None, stdout="", stderr="")
            return IsolatedRun(process.returncode, stdout.decode(), stderr.decode())

    monkeypatch.setattr(sandbox_manager, "run_isolated", run_locally)
