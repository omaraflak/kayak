"""Classification that travels with the tool.

A tool's category and risk are properties of the tool, so they are declared where it
is defined rather than in a lookup table somewhere else. A separate mapping -- in the
registry, or worse in the agent configuration UI -- would silently go stale every time
a tool is added, renamed, or removed, and would not cover user-authored tools at all.

Built-in tools carry the metadata as function attributes set by :func:`tool_metadata`.
Custom tools in ``data/tools/<name>/tool.py`` declare module-level ``CATEGORY`` and
``RISK`` strings; they cannot import from Kayak because they also run standalone during
verification and inside the sandbox container.
"""

from typing import Any, Callable, List, Tuple, TypeVar
from backend.app.models import ToolCategory, ToolCategoryInfo, ToolRisk

F = TypeVar("F", bound=Callable[..., Any])

CATEGORY_ATTRIBUTE = "__kayak_category__"
RISK_ATTRIBUTE = "__kayak_risk__"

# Anything a user writes runs in the Kayak server process just like a built-in, so an
# unclassified tool is assumed to be more than trivially capable.
DEFAULT_CATEGORY = ToolCategory.CUSTOM
DEFAULT_RISK = ToolRisk.MODERATE

# Display order is declaration order here; clients render categories as served.
_CATEGORY_CATALOG: Tuple[Tuple[ToolCategory, str, str], ...] = (
    (
        ToolCategory.FILESYSTEM,
        "Filesystem",
        "Read and write files inside the conversation workspace.",
    ),
    (
        ToolCategory.EXECUTION,
        "Execution",
        "Run commands and processes. These reach the host unless the conversation uses a Docker sandbox.",
    ),
    (
        ToolCategory.WEB,
        "Web",
        "Search and fetch public web content. Fetched pages are untrusted input.",
    ),
    (
        ToolCategory.ORCHESTRATION,
        "Orchestration",
        "Delegate work to sub-agents. These consume additional model usage.",
    ),
    (
        ToolCategory.KNOWLEDGE,
        "Skills & Knowledge",
        "Load and author the markdown skills that shape agent behavior.",
    ),
    (
        ToolCategory.TOOLING,
        "Tool Management",
        "Inspect, test, and install tools. Installed tools execute in the Kayak server process.",
    ),
    (
        ToolCategory.CUSTOM,
        "Custom",
        "Tools written for this workspace, from data/tools.",
    ),
)


def tool_metadata(category: ToolCategory, risk: ToolRisk) -> Callable[[F], F]:
    """Annotates a built-in tool with its category and risk.

    Attributes are attached to the function itself rather than wrapping it, so
    signature introspection and coroutine detection keep working unchanged.
    """

    def decorator(func: F) -> F:
        setattr(func, CATEGORY_ATTRIBUTE, category)
        setattr(func, RISK_ATTRIBUTE, risk)
        return func

    return decorator


def _coerce(value: Any, enum_type: Any, default: Any) -> Any:
    """Converts a declared value to its enum member, falling back when unrecognized."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().lower())
        except ValueError:
            return default
    return default


def read_function_metadata(func: Callable) -> Tuple[ToolCategory, ToolRisk]:
    """Reads category and risk declared on a built-in tool function."""
    return (
        _coerce(getattr(func, CATEGORY_ATTRIBUTE, None), ToolCategory, DEFAULT_CATEGORY),
        _coerce(getattr(func, RISK_ATTRIBUTE, None), ToolRisk, DEFAULT_RISK),
    )


def read_module_metadata(module: Any) -> Tuple[ToolCategory, ToolRisk]:
    """Reads optional CATEGORY and RISK constants declared by a custom tool module."""
    return (
        _coerce(getattr(module, "CATEGORY", None), ToolCategory, DEFAULT_CATEGORY),
        _coerce(getattr(module, "RISK", None), ToolRisk, DEFAULT_RISK),
    )


def list_categories() -> List[ToolCategoryInfo]:
    """Returns every tool category with display metadata, in presentation order."""
    return [
        ToolCategoryInfo(value=value, label=label, description=description)
        for value, label, description in _CATEGORY_CATALOG
    ]
