import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_type_hints
from backend.app.config import settings
from backend.app.models import ToolDefinition

# Type mapping from Python to JSON Schema
TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    List: "array",
    Dict: "object",
    Any: "string",
}

# Context parameters injected at runtime, excluded from tool schemas
_CONTEXT_PARAMS = frozenset({
    "context", "workspace_dir", "container_id", "conversation_id", "task_manager",
    "agent_depth",
})


def python_type_to_json_type(py_type: Any) -> str:
    """Converts a Python type hint to JSON schema type."""
    origin = getattr(py_type, "__origin__", None)
    if origin is not None:
        return TYPE_MAP.get(origin, "string")
    return TYPE_MAP.get(py_type, "string")


def parse_docstring(docstring: Optional[str]) -> tuple[str, Dict[str, str]]:
    """Parses a docstring into a main description and per-argument descriptions."""
    if not docstring:
        return "", {}

    import re

    lines = docstring.strip().split("\n")
    main_desc = []
    param_descs: Dict[str, str] = {}

    in_args_section = False
    current_param = None
    current_param_text: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.lower() in [
            "args:",
            "arguments:",
            "parameters:",
            "params:",
        ]:
            in_args_section = True
            continue

        if not in_args_section:
            main_desc.append(stripped)
        else:
            match = re.match(
                r"^([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\([^)]*\))?\s*:\s*(.*)$",
                stripped,
            )
            if match:
                if current_param:
                    param_descs[current_param] = " ".join(
                        current_param_text
                    ).strip()
                current_param = match.group(1)
                current_param_text = [match.group(2)]
            elif current_param and stripped:
                current_param_text.append(stripped)

    if current_param:
        param_descs[current_param] = " ".join(current_param_text).strip()

    return "\n".join(main_desc).strip(), param_descs


def extract_tool_schema(func: Callable, name: Optional[str] = None) -> Dict[str, Any]:
    """Generates an OpenAI/LiteLLM tool definition schema from a Python function."""
    func_name = name or func.__name__
    docstring = inspect.getdoc(func)
    main_description, param_descriptions = parse_docstring(docstring)

    sig = inspect.signature(func)
    try:
        type_hints = get_type_hints(func)
    except Exception:
        type_hints = {}

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name.startswith("_") or param_name in _CONTEXT_PARAMS:
            continue

        param_type = type_hints.get(param_name, str)
        json_type = python_type_to_json_type(param_type)
        param_desc = param_descriptions.get(
            param_name, f"Parameter: {param_name}"
        )

        properties[param_name] = {
            "type": json_type,
            "description": param_desc,
        }

        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": func_name,
            "description": main_description or f"Executes {func_name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _find_tool_function(module: Any, tool_name: str) -> Optional[Callable]:
    """Locates the primary callable in a tool module by convention."""
    target = (
        getattr(module, "execute", None)
        or getattr(module, "main", None)
        or getattr(module, tool_name, None)
    )
    if target:
        return target
    # Fallback: first public function defined in this module
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            callable(attr)
            and not attr_name.startswith("_")
            and attr.__module__ == f"kayak_tool_{tool_name}"
        ):
            return attr
    return None


class ToolRegistry:

    def __init__(self):
        self._builtin_tools: Dict[str, Callable] = {}
        self._builtin_schemas: Dict[str, Dict[str, Any]] = {}
        self._custom_tools: Dict[str, Callable] = {}
        self._custom_schemas: Dict[str, Dict[str, Any]] = {}
        self._custom_source_codes: Dict[str, str] = {}
        self._custom_verify_codes: Dict[str, str] = {}

    def register_builtin(self, func: Callable, name: Optional[str] = None):
        """Registers a built-in tool function."""
        tool_name = name or func.__name__
        self._builtin_tools[tool_name] = func
        self._builtin_schemas[tool_name] = extract_tool_schema(func, tool_name)

    def load_custom_tools(self):
        """Scans data/tools/<name>/tool.py and loads each tool."""
        self._custom_tools.clear()
        self._custom_schemas.clear()
        self._custom_source_codes.clear()
        self._custom_verify_codes.clear()

        tools_dir = settings.TOOLS_DIR
        if not tools_dir.exists():
            return

        for tool_folder in tools_dir.iterdir():
            if not tool_folder.is_dir():
                continue

            tool_name = tool_folder.name
            tool_file = tool_folder / "tool.py"
            verify_file = tool_folder / "verify.py"

            if not tool_file.exists():
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"kayak_tool_{tool_name}", tool_file
                )
                if not spec or not spec.loader:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                target_func = _find_tool_function(module, tool_name)
                if not target_func:
                    continue

                self._custom_tools[tool_name] = target_func
                self._custom_schemas[tool_name] = extract_tool_schema(target_func, tool_name)
                self._custom_source_codes[tool_name] = tool_file.read_text(encoding="utf-8")
                if verify_file.exists():
                    self._custom_verify_codes[tool_name] = verify_file.read_text(encoding="utf-8")

            except Exception as e:
                print(f"Error loading custom tool '{tool_name}': {e}")

    def get_tool(self, name: str) -> Optional[Callable]:
        """Returns the callable for a tool."""
        return self._builtin_tools.get(name) or self._custom_tools.get(name)

    def get_tool_definitions(
        self, allowed_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Returns OpenAPI function definitions formatted for LiteLLM/OpenAI."""
        all_schemas = {**self._builtin_schemas, **self._custom_schemas}
        if allowed_names is None:
            return list(all_schemas.values())
        return [schema for name, schema in all_schemas.items() if name in allowed_names]

    def list_all_tools(self) -> List[ToolDefinition]:
        """Returns a list of all tools with metadata for UI management."""
        result: List[ToolDefinition] = []
        all_schemas = {**self._builtin_schemas, **self._custom_schemas}

        for name, schema in all_schemas.items():
            fn = schema.get("function", {})
            is_builtin = name in self._builtin_schemas
            result.append(
                ToolDefinition(
                    name=name,
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters", {}),
                    is_builtin=is_builtin,
                    source_code=self._custom_source_codes.get(name) if not is_builtin else None,
                    verify_code=self._custom_verify_codes.get(name) if not is_builtin else None,
                )
            )

        return result

    async def execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Executes a tool with the provided arguments and runtime context."""
        # If executing in a Docker sandbox container and this is a custom tool
        container_id = context.get("container_id") if context else None
        if container_id and name in self._custom_source_codes:
            from backend.app.agent.sandbox import sandbox_manager
            tool_code = self._custom_source_codes[name]
            return await sandbox_manager.exec_custom_tool(
                container_id=container_id,
                tool_name=name,
                tool_code=tool_code,
                arguments=arguments,
            )

        func = self.get_tool(name)
        if not func:
            return f"Error: Tool '{name}' not found."

        sig = inspect.signature(func)
        call_kwargs = dict(arguments)

        # Inject context parameters if function signature accepts them
        if context:
            for param_name in sig.parameters:
                if param_name in context:
                    call_kwargs[param_name] = context[param_name]
                elif param_name == "context":
                    call_kwargs["context"] = context

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**call_kwargs)
            else:
                result = func(**call_kwargs)
            return str(result)
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"


# Singleton instance
tool_registry = ToolRegistry()
