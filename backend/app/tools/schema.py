"""Tool schemas: what the model is told about a tool's parameters.

Built-in tools are Kayak's own code and are described from their live signatures.
Custom tools are written by agents and users, so the server never imports them --
they only ever run inside a sandbox container -- and everything it needs to know
about one is read from its source instead: which function is the entrypoint, the
parameters it takes, and the classification it declares.
"""

import ast
import inspect
import re
import types
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.tools.metadata import ToolMetadata, coerce_metadata

#: Parameters injected by the runtime, never shown to the model.
CONTEXT_PARAMS = frozenset({
    "context", "workspace_dir", "container_id", "conversation_id", "task_manager",
    "agent_depth", "caller_agent_id",
})

#: Function names tried, in order, before falling back to the first public function.
ENTRYPOINT_NAMES = ("execute", "main")

_JSON_TYPES: Dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "List": "array",
    "tuple": "array",
    "Tuple": "array",
    "dict": "object",
    "Dict": "object",
}

_ARGS_HEADERS = {"args:", "arguments:", "parameters:", "params:"}
_PARAM_LINE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\([^)]*\))?\s*:\s*(.*)$")


def parse_docstring(docstring: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Splits a Google-style docstring into its description and per-argument text."""
    if not docstring:
        return "", {}

    description: List[str] = []
    params: Dict[str, str] = {}
    in_args = False
    current: Optional[str] = None
    current_text: List[str] = []

    for line in docstring.strip().split("\n"):
        stripped = line.strip()
        if stripped.lower() in _ARGS_HEADERS:
            in_args = True
            continue
        if not in_args:
            description.append(stripped)
            continue
        match = _PARAM_LINE.match(stripped)
        if match:
            if current:
                params[current] = " ".join(current_text).strip()
            current, current_text = match.group(1), [match.group(2)]
        elif current and stripped:
            current_text.append(stripped)

    if current:
        params[current] = " ".join(current_text).strip()
    return "\n".join(description).strip(), params


def _schema(
    name: str, docstring: Optional[str], params: List[Tuple[str, str, bool]]
) -> Dict[str, Any]:
    """Assembles an OpenAI function schema from (name, JSON type, required) triples."""
    description, param_descriptions = parse_docstring(docstring)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"Executes {name}",
            "parameters": {
                "type": "object",
                "properties": {
                    param: {
                        "type": json_type,
                        "description": param_descriptions.get(param, f"Parameter: {param}"),
                    }
                    for param, json_type, _required in params
                },
                "required": [param for param, _type, required in params if required],
            },
        },
    }


def _visible(param: str) -> bool:
    return not param.startswith("_") and param not in CONTEXT_PARAMS


# --- Built-in tools: live functions ------------------------------------------------


def python_type_to_json_type(py_type: Any) -> str:
    """Maps a type hint to a JSON schema type, seeing through Optional."""
    origin = typing.get_origin(py_type)
    if origin in (typing.Union, types.UnionType):
        members = [arg for arg in typing.get_args(py_type) if arg is not type(None)]
        return python_type_to_json_type(members[0]) if len(members) == 1 else "string"
    target = origin or py_type
    return _JSON_TYPES.get(getattr(target, "__name__", ""), "string")


def extract_tool_schema(func: Callable, name: Optional[str] = None) -> Dict[str, Any]:
    """Describes a built-in tool from its signature, type hints and docstring."""
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}
    params = [
        (param, python_type_to_json_type(hints.get(param, str)), spec.default is inspect.Parameter.empty)
        for param, spec in inspect.signature(func).parameters.items()
        if _visible(param)
    ]
    return _schema(name or func.__name__, inspect.getdoc(func), params)


# --- Custom tools: source only -----------------------------------------------------


@dataclass(frozen=True)
class CustomTool:
    """Everything the server knows about a custom tool, read without running it."""
    entrypoint: str
    schema: Dict[str, Any]
    metadata: ToolMetadata


class ToolSourceError(ValueError):
    """Raised when tool source cannot be read as a usable tool."""


def _annotation_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):  # typing.List
        return node.attr
    return ""


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _annotation_json_type(node: Optional[ast.AST]) -> str:
    """Maps a parameter annotation, as written, to a JSON schema type."""
    if node is None:
        return "string"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return "string"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        members = [side for side in (node.left, node.right) if not _is_none(side)]
        return _annotation_json_type(members[0]) if len(members) == 1 else "string"
    if isinstance(node, ast.Subscript):
        base = _annotation_name(node.value)
        if base == "Optional":
            return _annotation_json_type(node.slice)
        if base == "Union":
            elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            members = [element for element in elements if not _is_none(element)]
            return _annotation_json_type(members[0]) if len(members) == 1 else "string"
        return _JSON_TYPES.get(base, "string")
    return _JSON_TYPES.get(_annotation_name(node), "string")


def _entrypoint(tree: ast.Module, tool_name: str) -> Optional[ast.AST]:
    """The function a tool exposes: execute, main, its own name, or the first public one."""
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    by_name = {function.name: function for function in functions}
    for name in (*ENTRYPOINT_NAMES, tool_name):
        if name in by_name:
            return by_name[name]
    return next((function for function in functions if not function.name.startswith("_")), None)


def _declared_constants(tree: ast.Module) -> Dict[str, Any]:
    """Module-level literal assignments, such as CATEGORY = "web"."""
    constants: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    constants[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    continue
    return constants


def read_custom_tool(tool_name: str, source: str) -> CustomTool:
    """Reads a custom tool's entrypoint, schema and classification from its source.

    Raises:
        ToolSourceError: If the source does not parse or defines no public function.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ToolSourceError(f"tool.py does not parse: {error}") from error

    function = _entrypoint(tree, tool_name)
    if function is None:
        raise ToolSourceError(
            "tool.py defines no public function. Name it execute, main, or after the tool."
        )

    arguments = function.args
    positional = arguments.posonlyargs + arguments.args
    # Defaults belong to the last positional parameters.
    first_default = len(positional) - len(arguments.defaults)
    params = [
        (arg.arg, _annotation_json_type(arg.annotation), index < first_default)
        for index, arg in enumerate(positional)
    ] + [
        (arg.arg, _annotation_json_type(arg.annotation), default is None)
        for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults)
    ]
    params = [param for param in params if _visible(param[0])]

    constants = _declared_constants(tree)
    return CustomTool(
        entrypoint=function.name,
        schema=_schema(tool_name, ast.get_docstring(function), params),
        metadata=coerce_metadata(
            constants.get("CATEGORY"), constants.get("RISK"), constants.get("READ_ONLY")
        ),
    )
