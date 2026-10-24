"""Testing a custom tool before it is trusted.

The tool and its verify.py suite run in a throwaway container from the sandbox image:
the same environment the tool will later run in, and never the server, which holds
the Docker socket. Its schema is read from the source without running anything.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.app.identifiers import clean_identifier
from backend.app.tools.schema import ToolSourceError, read_custom_tool
from backend.app.user_errors import describe_sandbox_error

#: How long verify.py may run before it is killed.
VERIFY_TIMEOUT_SECONDS = 60


@dataclass
class ToolVerificationResult:
    """Encapsulates output, status, and schema from a tool verification test run."""
    success: bool
    stdout: str
    stderr: str
    parsed_schema: Optional[Dict[str, Any]]
    error: Optional[str]


async def run_tool_verification(
    tool_name: str,
    tool_code: str,
    verify_code: str,
    timeout_seconds: int = VERIFY_TIMEOUT_SECONDS,
) -> ToolVerificationResult:
    """Reads a tool's schema and runs its verify.py suite in a throwaway sandbox.

    Args:
        tool_name: Raw name of the tool.
        tool_code: Complete Python source code for tool.py.
        verify_code: Complete Python unit test code for verify.py.
        timeout_seconds: How long the suite may run.

    Returns:
        ToolVerificationResult with execution output, return status, and extracted schema.
    """
    clean_name = clean_identifier(tool_name)

    try:
        schema = read_custom_tool(clean_name, tool_code).schema["function"]
    except ToolSourceError as error:
        return ToolVerificationResult(
            success=False, stdout="", stderr=str(error), parsed_schema=None, error=str(error)
        )

    from backend.app.agent.sandbox import sandbox_manager

    try:
        run = await sandbox_manager.run_isolated(
            files={"tool.py": tool_code, "verify.py": verify_code},
            command=["python3", "verify.py"],
            timeout=timeout_seconds,
        )
    except Exception as error:
        return ToolVerificationResult(
            success=False,
            stdout="",
            stderr=str(error),
            parsed_schema=schema,
            error=describe_sandbox_error(error),
        )

    if run.exit_code is None:
        message = f"The tests took longer than {timeout_seconds} seconds and were stopped."
        return ToolVerificationResult(
            success=False, stdout=run.stdout, stderr=message, parsed_schema=schema, error=message
        )

    success = run.exit_code == 0
    return ToolVerificationResult(
        success=success,
        stdout=run.stdout,
        stderr=run.stderr,
        parsed_schema=schema,
        error=None if success else "The tool's tests failed. Their output is below.",
    )
