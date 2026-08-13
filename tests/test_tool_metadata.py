"""Tests for tool classification.

Category and risk are declared on the tool itself so that no separate list has to be
maintained in step with it. These guard the properties that makes that work: every
built-in is annotated, every category is displayable, and user-authored tools can
classify themselves without importing Kayak.
"""

from pathlib import Path
import pytest
from backend.app.config import settings
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import (
    DEFAULT_CATEGORY,
    DEFAULT_RISK,
    list_categories,
)
from backend.app.tools.registry import tool_registry

TOOL_WITH_METADATA = '''
CATEGORY = "web"
RISK = "high"


def execute(url: str) -> str:
    """Does something webby.

    Args:
        url: Target URL.
    """
    return url
'''

TOOL_WITHOUT_METADATA = '''
def execute(value: str) -> str:
    """Echoes a value.

    Args:
        value: Text to echo.
    """
    return value
'''

TOOL_WITH_BAD_METADATA = '''
CATEGORY = "not_a_real_category"
RISK = "catastrophic"


def execute(value: str) -> str:
    """Echoes a value.

    Args:
        value: Text to echo.
    """
    return value
'''


@pytest.fixture
def custom_tools_dir(tmp_path: Path, monkeypatch) -> Path:
    directory = tmp_path / "tools"
    directory.mkdir()
    monkeypatch.setattr(settings, "TOOLS_DIR", directory)
    yield directory
    tool_registry.load_custom_tools()


def _install(directory: Path, name: str, source: str) -> None:
    folder = directory / name
    folder.mkdir()
    (folder / "tool.py").write_text(source)


def _find(name: str):
    return next((t for t in tool_registry.list_all_tools() if t.name == name), None)


class TestCategoryCatalog:
    def test_every_category_has_display_metadata(self):
        # A category with no entry here would render as an unlabelled group.
        described = {info.value for info in list_categories()}
        assert described == set(ToolCategory)

    def test_categories_are_returned_in_a_stable_order(self):
        assert [info.value for info in list_categories()] == [
            info.value for info in list_categories()
        ]

    def test_labels_are_human_readable(self):
        for info in list_categories():
            assert info.label
            assert info.label[0].isupper()
            assert info.description


class TestBuiltinAnnotations:
    def test_every_builtin_declares_a_category(self):
        # An unannotated built-in silently lands in "Custom", which is misleading.
        import backend.app.tools  # noqa: F401  (registers the built-ins)

        unclassified = [
            tool.name
            for tool in tool_registry.list_all_tools()
            if tool.is_builtin and tool.category == DEFAULT_CATEGORY
        ]
        assert unclassified == []

    @pytest.mark.parametrize(
        "name,category,risk",
        [
            ("run_command", ToolCategory.EXECUTION, ToolRisk.HIGH),
            ("read_file", ToolCategory.FILESYSTEM, ToolRisk.LOW),
            ("write_file", ToolCategory.FILESYSTEM, ToolRisk.MODERATE),
            ("web_search", ToolCategory.WEB, ToolRisk.LOW),
            ("spawn_subagent", ToolCategory.ORCHESTRATION, ToolRisk.MODERATE),
            ("load_skill", ToolCategory.KNOWLEDGE, ToolRisk.LOW),
            ("activate_tool", ToolCategory.TOOLING, ToolRisk.HIGH),
        ],
    )
    def test_representative_builtins_are_classified(self, name, category, risk):
        import backend.app.tools  # noqa: F401

        tool = _find(name)
        assert tool is not None
        assert tool.category == category
        assert tool.risk == risk

    def test_code_executing_tools_are_marked_high_risk(self):
        import backend.app.tools  # noqa: F401

        for name in ("run_command", "start_background_task", "activate_tool"):
            assert _find(name).risk == ToolRisk.HIGH


class TestCustomToolMetadata:
    def test_declared_metadata_is_read(self, custom_tools_dir: Path):
        _install(custom_tools_dir, "webby", TOOL_WITH_METADATA)
        tool_registry.load_custom_tools()

        tool = _find("webby")
        assert tool.category == ToolCategory.WEB
        assert tool.risk == ToolRisk.HIGH

    def test_undeclared_metadata_falls_back_to_custom(self, custom_tools_dir: Path):
        _install(custom_tools_dir, "plain", TOOL_WITHOUT_METADATA)
        tool_registry.load_custom_tools()

        tool = _find("plain")
        assert tool.category == DEFAULT_CATEGORY
        # User tools execute in the server process, so the default is not "low".
        assert tool.risk == DEFAULT_RISK == ToolRisk.MODERATE

    def test_unrecognized_values_fall_back_instead_of_failing(self, custom_tools_dir: Path):
        _install(custom_tools_dir, "weird", TOOL_WITH_BAD_METADATA)
        tool_registry.load_custom_tools()

        tool = _find("weird")
        assert tool is not None
        assert tool.category == DEFAULT_CATEGORY
        assert tool.risk == DEFAULT_RISK

    def test_metadata_is_dropped_when_a_custom_tool_is_removed(self, custom_tools_dir: Path):
        _install(custom_tools_dir, "webby", TOOL_WITH_METADATA)
        tool_registry.load_custom_tools()
        assert _find("webby") is not None

        import shutil

        shutil.rmtree(custom_tools_dir / "webby")
        tool_registry.load_custom_tools()
        assert _find("webby") is None
