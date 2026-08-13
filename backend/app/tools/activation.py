"""The single path by which tool code becomes live.

Activation writes Python that the server subsequently imports and executes in its own
process, so "the tests passed" has to be an invariant of activation rather than a
convention observed by whichever caller happens to remember it. Enforcing it in the
UI alone left the agent's own ``activate_tool`` free to install unverified code.
"""

from dataclasses import dataclass
from typing import Optional
from backend.app.config import settings
from backend.app.models import ToolDefinition
from backend.app.tools.registry import tool_registry
from backend.app.tools.verifier import clean_tool_identifier, run_tool_verification


@dataclass
class ActivationResult:
    """Outcome of an attempt to install a tool."""
    success: bool
    tool_name: str
    message: str
    definition: Optional[ToolDefinition] = None


async def activate_verified_tool(
    tool_name: str,
    tool_code: str,
    verify_code: str,
) -> ActivationResult:
    """Verifies tool code and, only if its tests pass, installs and registers it.

    Args:
        tool_name: Requested identifier for the tool.
        tool_code: Complete Python source for ``tool.py``.
        verify_code: Complete Python test suite for ``verify.py``.

    Returns:
        ActivationResult: On failure, nothing is written to disk.
    """
    clean_name = clean_tool_identifier(tool_name)

    if not tool_code.strip():
        return ActivationResult(
            success=False,
            tool_name=clean_name,
            message="Error: Cannot activate a tool with empty tool.py source.",
        )

    if not verify_code.strip():
        return ActivationResult(
            success=False,
            tool_name=clean_name,
            message=(
                "Error: A verify.py test suite is required. Tools run inside the Kayak "
                "server process, so unverified code is never installed."
            ),
        )

    verification = await run_tool_verification(
        tool_name=clean_name,
        tool_code=tool_code,
        verify_code=verify_code,
    )

    if not verification.success:
        return ActivationResult(
            success=False,
            tool_name=clean_name,
            message=(
                f"✗ Not activated: verification failed ({verification.error}).\n\n"
                f"STDOUT:\n{verification.stdout}\n\nSTDERR:\n{verification.stderr}"
            ),
        )

    tool_directory = settings.TOOLS_DIR / clean_name
    tool_directory.mkdir(parents=True, exist_ok=True)
    (tool_directory / "tool.py").write_text(tool_code, encoding="utf-8")
    (tool_directory / "verify.py").write_text(verify_code, encoding="utf-8")

    tool_registry.load_custom_tools()

    definition = next(
        (tool for tool in tool_registry.list_all_tools() if tool.name == clean_name),
        None,
    )
    if not definition:
        return ActivationResult(
            success=False,
            tool_name=clean_name,
            message=(
                f"Error: '{clean_name}' passed verification and was written to disk, but "
                "could not be loaded into the registry. Check that tool.py exposes a "
                "function named execute, main, or the tool name itself."
            ),
        )

    return ActivationResult(
        success=True,
        tool_name=clean_name,
        message=(
            f"Successfully verified and activated tool '{clean_name}'. "
            "It is now available across Kayak!"
        ),
        definition=definition,
    )
