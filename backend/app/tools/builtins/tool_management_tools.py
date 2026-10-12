from backend.app.identifiers import clean_identifier
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.activation import activate_verified_tool
from backend.app.tools.metadata import tool_metadata
from backend.app.tools.registry import tool_registry
from backend.app.tools.verifier import run_tool_verification


@tool_metadata(category=ToolCategory.TOOLING, risk=ToolRisk.MODERATE)
async def verify_tool(tool_name: str, tool_code: str, verify_code: str) -> str:
    """Runs a tool's verify.py suite in a fresh sandbox and reports the schema read from tool.py.

    The fresh sandbox has none of the packages installed in this conversation, which is
    deliberate: an installed tool must work in any conversation.

    Args:
        tool_name: Identifier for the tool (e.g. 'fetch_weather').
        tool_code: Python source code for `tool.py`.
        verify_code: Python verification test code for `verify.py`.
    """
    result = await run_tool_verification(
        tool_name=tool_name,
        tool_code=tool_code,
        verify_code=verify_code,
    )

    schema_info = f"\n✓ Auto-extracted JSON Schema: {result.parsed_schema}" if result.parsed_schema else ""
    if result.success:
        return f"✓ Verification Passed!\n\nSTDOUT:\n{result.stdout}{schema_info}"
    else:
        return f"✗ Verification Failed ({result.error})\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"


@tool_metadata(category=ToolCategory.TOOLING, risk=ToolRisk.HIGH)
async def activate_tool(
    tool_name: str, tool_code: str, verify_code: str
) -> str:
    """Saves a tool to data/tools/<tool_name>/ and registers it in the Kayak runtime ecosystem.

    Verification runs again as part of activation and the tool is only installed if its
    tests pass, so call this once the code is final.

    Args:
        tool_name: Identifier for the tool.
        tool_code: Python source code for `tool.py`.
        verify_code: Python verification test code for `verify.py`.
    """
    result = await activate_verified_tool(
        tool_name=tool_name,
        tool_code=tool_code,
        verify_code=verify_code,
    )
    return result.message


@tool_metadata(category=ToolCategory.TOOLING, risk=ToolRisk.LOW, read_only=True)
def get_tool_source(tool_name: str) -> str:
    """Reads the current source code and test suite of an installed custom tool.

    Call this before modifying an existing tool so the edit is based on what is
    actually installed rather than a remembered version.

    Args:
        tool_name: Identifier of the tool to read (e.g. 'generate_uuidv4').
    """
    clean_name = clean_identifier(tool_name)

    definition = next(
        (tool for tool in tool_registry.list_all_tools() if tool.name == clean_name),
        None,
    )
    if not definition:
        available = [t.name for t in tool_registry.list_all_tools() if not t.is_builtin]
        return (
            f"Error: Tool '{tool_name}' not found. Custom tools available:"
            f" {', '.join(available) if available else '(none)'}"
        )

    if definition.is_builtin:
        return (
            f"Error: '{clean_name}' is a built-in tool and has no editable source in"
            " data/tools. Built-ins can only be changed in the Kayak codebase."
        )

    sections = [
        f"=== Tool: {clean_name} ===",
        f"Description: {definition.description}",
        "\n--- tool.py ---",
        definition.source_code or "(missing)",
    ]
    if definition.verify_code:
        sections.extend(["\n--- verify.py ---", definition.verify_code])

    return "\n".join(sections)
