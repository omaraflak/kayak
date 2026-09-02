"""Classification that travels with the tool.

A tool's category, risk, and whether it only reads are properties of the tool, so they
are declared where it is defined rather than in a lookup table somewhere else. A
separate mapping -- in the registry, or worse in the agent configuration UI -- would
silently go stale every time a tool is added, renamed, or removed, and would not cover
user-authored tools at all.

``read_only`` marks a tool that observes without changing anything, which is what lets
the engine run several of them at once; leaving it unset is always the safe answer,
since the cost is only that the calls run one after another.

Built-in tools carry the metadata as function attributes set by :func:`tool_metadata`.
Custom tools in ``data/tools/<name>/tool.py`` declare module-level ``CATEGORY`` and
``RISK`` strings and an optional ``READ_ONLY`` boolean; they cannot import from Kayak
because they also run standalone during verification and inside the sandbox container.
"""

from dataclasses import dataclass
from typing import Any, Callable, List, Tuple, TypeVar
from backend.app.models import ToolCategory, ToolCategoryInfo, ToolRisk

F = TypeVar("F", bound=Callable[..., Any])

METADATA_ATTRIBUTE = "__kayak_metadata__"

# Anything a user writes runs in the Kayak server process just like a built-in, so an
# unclassified tool is assumed to be more than trivially capable.
DEFAULT_CATEGORY = ToolCategory.CUSTOM
DEFAULT_RISK = ToolRisk.MODERATE
DEFAULT_READ_ONLY = False


@dataclass(frozen=True)
class ToolMetadata:
    """Classification and execution properties of a tool."""
    category: ToolCategory = DEFAULT_CATEGORY
    risk: ToolRisk = DEFAULT_RISK
    read_only: bool = DEFAULT_READ_ONLY


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


def tool_metadata(
    category: ToolCategory,
    risk: ToolRisk,
    read_only: bool = False,
) -> Callable[[F], F]:
    """Annotates a built-in tool with its category, risk, and read-only status.

    Attributes are attached to the function itself rather than wrapping it, so
    signature introspection and coroutine detection keep working unchanged.
    """
    metadata = ToolMetadata(category=category, risk=risk, read_only=read_only)

    def decorator(func: F) -> F:
        setattr(func, METADATA_ATTRIBUTE, metadata)
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


def read_function_metadata(func: Callable) -> ToolMetadata:
    """Reads category, risk, and read-only flag declared on a built-in tool function."""
    meta = getattr(func, METADATA_ATTRIBUTE, None)
    if isinstance(meta, ToolMetadata):
        return meta
    return ToolMetadata()


def read_module_metadata(module: Any) -> ToolMetadata:
    """Reads optional CATEGORY, RISK, and READ_ONLY constants declared by a custom tool module."""
    return ToolMetadata(
        category=_coerce(getattr(module, "CATEGORY", None), ToolCategory, DEFAULT_CATEGORY),
        risk=_coerce(getattr(module, "RISK", None), ToolRisk, DEFAULT_RISK),
        read_only=bool(getattr(module, "READ_ONLY", DEFAULT_READ_ONLY)),
    )


def list_categories() -> List[ToolCategoryInfo]:
    """Returns every tool category with display metadata, in presentation order."""
    return [
        ToolCategoryInfo(value=value, label=label, description=description)
        for value, label, description in _CATEGORY_CATALOG
    ]
