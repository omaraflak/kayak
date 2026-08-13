"""Tests for the tool activation gate.

Activated tools are imported and executed inside the Kayak server process, so
"verification passed" has to hold for every caller. It was previously enforced only
by the UI's disabled button, leaving the agent's own activate_tool free to install
code whose tests fail.
"""

from pathlib import Path
import pytest
from backend.app.config import settings
from backend.app.tools.activation import activate_verified_tool
from backend.app.tools.builtins.tool_management_tools import get_tool_source
from backend.app.tools.registry import tool_registry

PASSING_TOOL = '''
def execute(text: str) -> str:
    """Upper-cases text.

    Args:
        text: Text to transform.
    """
    return text.upper()
'''

PASSING_VERIFY = """
from tool import execute

assert execute("hi") == "HI"
print("ok")
"""

FAILING_VERIFY = """
from tool import execute

assert execute("hi") == "definitely not this"
"""

BROKEN_TOOL = """
def execute(text:
    return text
"""


@pytest.fixture
def tools_dir(tmp_path: Path, monkeypatch) -> Path:
    """Points the registry at an empty tools directory for each test."""
    directory = tmp_path / "tools"
    directory.mkdir()
    monkeypatch.setattr(settings, "TOOLS_DIR", directory)
    tool_registry.load_custom_tools()
    yield directory
    tool_registry.load_custom_tools()


@pytest.mark.asyncio
async def test_passing_tool_is_installed_and_registered(tools_dir: Path):
    result = await activate_verified_tool("Shouty Tool", PASSING_TOOL, PASSING_VERIFY)

    assert result.success
    assert result.tool_name == "shouty_tool"
    assert (tools_dir / "shouty_tool" / "tool.py").exists()
    assert (tools_dir / "shouty_tool" / "verify.py").exists()
    assert tool_registry.get_tool("shouty_tool") is not None


@pytest.mark.asyncio
async def test_failing_tests_block_installation(tools_dir: Path):
    result = await activate_verified_tool("bad_tool", PASSING_TOOL, FAILING_VERIFY)

    assert not result.success
    # Nothing may be written: a partially installed tool would be loaded on the next
    # registry reload even though its tests never passed.
    assert not (tools_dir / "bad_tool").exists()
    assert tool_registry.get_tool("bad_tool") is None


@pytest.mark.asyncio
async def test_unparseable_tool_is_rejected(tools_dir: Path):
    result = await activate_verified_tool("broken", BROKEN_TOOL, PASSING_VERIFY)

    assert not result.success
    assert not (tools_dir / "broken").exists()


@pytest.mark.asyncio
async def test_missing_verify_suite_is_rejected(tools_dir: Path):
    result = await activate_verified_tool("untested", PASSING_TOOL, "   ")

    assert not result.success
    assert "verify.py test suite is required" in result.message
    assert not (tools_dir / "untested").exists()


@pytest.mark.asyncio
async def test_empty_source_is_rejected(tools_dir: Path):
    result = await activate_verified_tool("empty", "", PASSING_VERIFY)

    assert not result.success
    assert not (tools_dir / "empty").exists()


@pytest.mark.asyncio
async def test_reactivation_overwrites_in_place(tools_dir: Path):
    await activate_verified_tool("shouty_tool", PASSING_TOOL, PASSING_VERIFY)

    updated_tool = PASSING_TOOL.replace("text.upper()", "text.upper() + '!'")
    updated_verify = PASSING_VERIFY.replace('== "HI"', '== "HI!"')
    result = await activate_verified_tool("shouty_tool", updated_tool, updated_verify)

    assert result.success
    assert "!" in (tools_dir / "shouty_tool" / "tool.py").read_text()


@pytest.mark.asyncio
async def test_failed_reactivation_leaves_the_installed_version_intact(tools_dir: Path):
    await activate_verified_tool("shouty_tool", PASSING_TOOL, PASSING_VERIFY)
    original = (tools_dir / "shouty_tool" / "tool.py").read_text()

    result = await activate_verified_tool("shouty_tool", PASSING_TOOL, FAILING_VERIFY)

    assert not result.success
    assert (tools_dir / "shouty_tool" / "tool.py").read_text() == original


class TestGetToolSource:
    @pytest.mark.asyncio
    async def test_returns_source_and_tests_for_a_custom_tool(self, tools_dir: Path):
        await activate_verified_tool("shouty_tool", PASSING_TOOL, PASSING_VERIFY)

        output = get_tool_source("shouty_tool")

        assert "tool.py" in output
        assert "verify.py" in output
        assert "text.upper()" in output

    def test_unknown_tool_reports_what_is_available(self, tools_dir: Path):
        assert get_tool_source("nope").startswith("Error:")

    def test_builtin_tools_have_no_editable_source(self, tools_dir: Path):
        output = get_tool_source("read_file")

        assert output.startswith("Error:")
        assert "built-in" in output
