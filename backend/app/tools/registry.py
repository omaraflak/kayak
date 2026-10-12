"""Every tool an agent can call: Kayak's built-ins and the custom tools in data/tools.

Built-ins are Kayak's own functions and run in the server. Custom tools are read from
their source and only ever run inside the calling conversation's sandbox: the server
holds the Docker socket, so code an agent wrote must never execute in it.
"""

from dataclasses import dataclass
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

from backend.app.config import settings
from backend.app.models import ToolDefinition
from backend.app.tools.metadata import ToolMetadata, read_function_metadata
from backend.app.tools.schema import (
    CustomTool,
    ToolSourceError,
    extract_tool_schema,
    read_custom_tool,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _InstalledTool:
    """A custom tool found on disk."""
    tool: CustomTool
    source: str
    verify_source: Optional[str]


class ToolRegistry:

    def __init__(self) -> None:
        self._builtins: Dict[str, Callable] = {}
        self._builtin_schemas: Dict[str, Dict[str, Any]] = {}
        self._custom: Dict[str, _InstalledTool] = {}

    def register_builtin(self, func: Callable) -> None:
        """Registers one of Kayak's own tool functions."""
        self._builtins[func.__name__] = func
        self._builtin_schemas[func.__name__] = extract_tool_schema(func)

    def load_custom_tools(self) -> None:
        """Reads every data/tools/<name>/tool.py, replacing what was loaded before."""
        loaded: Dict[str, _InstalledTool] = {}
        tools_dir = settings.TOOLS_DIR
        folders = sorted(tools_dir.iterdir()) if tools_dir.exists() else []

        for folder in folders:
            tool_file = folder / "tool.py"
            if not folder.is_dir() or not tool_file.exists():
                continue
            name = folder.name
            if name in self._builtins:
                logger.warning("Ignoring custom tool '%s': a built-in has that name.", name)
                continue
            try:
                source = tool_file.read_text(encoding="utf-8")
                tool = read_custom_tool(name, source)
            except (OSError, ToolSourceError) as error:
                logger.warning("Ignoring custom tool '%s': %s", name, error)
                continue
            verify_file = folder / "verify.py"
            loaded[name] = _InstalledTool(
                tool=tool,
                source=source,
                verify_source=(
                    verify_file.read_text(encoding="utf-8") if verify_file.exists() else None
                ),
            )

        self._custom = loaded

    def has_tool(self, name: str) -> bool:
        return name in self._builtins or name in self._custom

    def is_custom(self, name: str) -> bool:
        return name in self._custom

    def metadata(self, name: str) -> ToolMetadata:
        """Category, risk and read-only flag of a tool; defaults for an unknown one."""
        if name in self._builtins:
            return read_function_metadata(self._builtins[name])
        if name in self._custom:
            return self._custom[name].tool.metadata
        return ToolMetadata()

    def is_read_only(self, name: str) -> bool:
        """True if the tool has no side effects and is safe for concurrent execution."""
        return self.has_tool(name) and self.metadata(name).read_only

    def _schemas(self) -> Dict[str, Dict[str, Any]]:
        return {
            **self._builtin_schemas,
            **{name: installed.tool.schema for name, installed in self._custom.items()},
        }

    def get_tool_definitions(
        self, allowed_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Function schemas for LiteLLM, limited to the allowed tools when given."""
        schemas = self._schemas()
        if allowed_names is None:
            return list(schemas.values())
        return [schema for name, schema in schemas.items() if name in allowed_names]

    def list_all_tools(self) -> List[ToolDefinition]:
        """Every tool with its metadata and, for custom tools, its source."""
        result: List[ToolDefinition] = []
        for name, schema in self._schemas().items():
            function = schema["function"]
            meta = self.metadata(name)
            installed = self._custom.get(name)
            result.append(
                ToolDefinition(
                    name=name,
                    description=function.get("description", ""),
                    parameters=function.get("parameters", {}),
                    is_builtin=installed is None,
                    category=meta.category,
                    risk=meta.risk,
                    read_only=meta.read_only,
                    source_code=installed.source if installed else None,
                    verify_code=installed.verify_source if installed else None,
                )
            )
        return result

    async def execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Runs a tool with the model's arguments and the turn's runtime context."""
        context = context or {}

        installed = self._custom.get(name)
        if installed:
            container_id = context.get("container_id")
            if not container_id:
                return "Error: Custom tools run in the conversation's sandbox, and none is available."
            from backend.app.agent.sandbox import sandbox_manager

            return await sandbox_manager.exec_custom_tool(
                container_id=container_id,
                entrypoint=installed.tool.entrypoint,
                tool_code=installed.source,
                arguments=arguments,
            )

        func = self._builtins.get(name)
        if not func:
            return f"Error: Tool '{name}' not found."

        call_kwargs = dict(arguments)
        for param_name in inspect.signature(func).parameters:
            if param_name == "context":
                call_kwargs["context"] = context
            elif param_name in context:
                call_kwargs[param_name] = context[param_name]

        result = func(**call_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return str(result)


tool_registry = ToolRegistry()
