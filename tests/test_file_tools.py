"""Tests for workspace path confinement.

An agent's file paths are untrusted input: they can be shaped by web content the
agent fetched. These assert that nothing resolves outside the conversation workspace.
"""

from pathlib import Path
import pytest
from backend.app.tools.builtins.file_tools import (
    PathOutsideWorkspaceError,
    read_file,
    resolve_workspace_path,
    write_file,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


class TestResolveWorkspacePath:
    def test_relative_path_resolves_inside_workspace(self, workspace: Path):
        resolved = resolve_workspace_path("notes/todo.md", workspace)
        assert resolved == (workspace / "notes/todo.md").resolve()

    def test_workspace_root_itself_is_allowed(self, workspace: Path):
        assert resolve_workspace_path(".", workspace) == workspace.resolve()

    def test_absolute_path_outside_workspace_is_rejected(self, workspace: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            resolve_workspace_path("/etc/passwd", workspace)

    def test_parent_traversal_is_rejected(self, workspace: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            resolve_workspace_path("../../../etc/passwd", workspace)

    def test_traversal_hidden_mid_path_is_rejected(self, workspace: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            resolve_workspace_path("safe/../../outside.txt", workspace)

    def test_symlink_escape_is_rejected(self, workspace: Path, tmp_path: Path):
        # A link planted inside the workspace must not become a way out of it.
        secret = tmp_path / "secret.txt"
        secret.write_text("classified")
        (workspace / "escape").symlink_to(secret)

        with pytest.raises(PathOutsideWorkspaceError):
            resolve_workspace_path("escape", workspace)

    def test_sibling_directory_with_shared_prefix_is_rejected(self, tmp_path: Path):
        # "/data/ws" must not be treated as a parent of "/data/ws-other".
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (tmp_path / "ws-other").mkdir()

        with pytest.raises(PathOutsideWorkspaceError):
            resolve_workspace_path(str(tmp_path / "ws-other" / "f.txt"), workspace)


class TestFileToolConfinement:
    @pytest.mark.asyncio
    async def test_read_outside_workspace_returns_error(self, workspace: Path):
        result = await read_file("/etc/passwd", workspace_dir=workspace)
        assert "Access denied" in result

    @pytest.mark.asyncio
    async def test_write_outside_workspace_is_refused(self, workspace: Path, tmp_path: Path):
        target = tmp_path / "escaped.txt"

        result = await write_file(
            "../escaped.txt", "payload", workspace_dir=workspace
        )

        assert "Access denied" in result
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_write_then_read_inside_workspace_round_trips(self, workspace: Path):
        await write_file("nested/hello.txt", "world", workspace_dir=workspace)
        result = await read_file("nested/hello.txt", workspace_dir=workspace)

        assert "world" in result
        assert (workspace / "nested/hello.txt").read_text() == "world"
