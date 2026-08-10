import asyncio
import importlib.util
import os
from pathlib import Path
import re
import tempfile
from fastapi import APIRouter, HTTPException
from backend.app.config import settings
from backend.app.models import (
    ActivateToolRequest,
    ToolDefinition,
    VerifyToolRequest,
    VerifyToolResponse,
)
from backend.app.tools.registry import extract_tool_schema, tool_registry

router = APIRouter(prefix="/api/tool-builder", tags=["tool-builder"])


@router.post("/verify", response_model=VerifyToolResponse)
async def verify_tool_code(request: VerifyToolRequest) -> VerifyToolResponse:
    """Executes the tool verification script in an isolated temp directory to validate correctness.

    Args:
        request: VerifyToolRequest containing tool_name, tool_code, and verify_code.

    Returns:
        VerifyToolResponse containing test execution output, extracted schema, and status.
    """
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", request.tool_name.lower().strip())

    with tempfile.TemporaryDirectory(
        prefix=f"kayak_verify_{clean_name}_"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)
        tool_file = temporary_path / "tool.py"
        verify_file = temporary_path / "verify.py"

        tool_file.write_text(request.tool_code, encoding="utf-8")
        verify_file.write_text(request.verify_code, encoding="utf-8")

        # 1. Parse and extract schema from tool.py
        parsed_schema = None
        try:
            spec = importlib.util.spec_from_file_location(
                f"tmp_verify_{clean_name}", tool_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                target_func = (
                    getattr(module, "execute", None)
                    or getattr(module, "main", None)
                    or getattr(module, clean_name, None)
                )

                if not target_func:
                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)
                        if callable(attribute) and not attribute_name.startswith("_"):
                            target_func = attribute
                            break

                if target_func:
                    schema_data = extract_tool_schema(target_func, clean_name)
                    parsed_schema = schema_data.get("function")
        except Exception as error:
            return VerifyToolResponse(
                success=False,
                stdout="",
                stderr=f"Syntax/Import error in tool.py: {str(error)}",
                parsed_schema=None,
                error=f"Syntax/Import error in tool.py: {str(error)}",
            )

        # 2. Run verify.py in isolated subprocess
        try:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = f"{str(temporary_path)}:{environment.get('PYTHONPATH', '')}"

            process = await asyncio.create_subprocess_exec(
                "python3",
                "verify.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(temporary_path),
                env=environment,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=30
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            success = process.returncode == 0

            return VerifyToolResponse(
                success=success,
                stdout=stdout,
                stderr=stderr,
                parsed_schema=parsed_schema,
                error=None
                if success
                else f"Tests failed with exit code {process.returncode}",
            )
        except asyncio.TimeoutError:
            return VerifyToolResponse(
                success=False,
                stdout="",
                stderr="Verification script timed out after 30 seconds.",
                parsed_schema=parsed_schema,
                error="Timeout",
            )
        except Exception as error:
            return VerifyToolResponse(
                success=False,
                stdout="",
                stderr=str(error),
                parsed_schema=parsed_schema,
                error=str(error),
            )


@router.post("/activate", response_model=ToolDefinition)
async def activate_tool(request: ActivateToolRequest) -> ToolDefinition:
    """Saves the verified tool code into data/tools/<name>/ and activates it in the runtime registry.

    Args:
        request: ActivateToolRequest with tool files and name.

    Returns:
        The newly activated ToolDefinition record.

    Raises:
        HTTPException: If tool files could not be registered.
    """
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", request.tool_name.lower().strip())
    tool_directory = settings.TOOLS_DIR / clean_name
    tool_directory.mkdir(parents=True, exist_ok=True)

    (tool_directory / "tool.py").write_text(request.tool_code, encoding="utf-8")
    (tool_directory / "verify.py").write_text(request.verify_code, encoding="utf-8")

    # Reload registry
    tool_registry.load_custom_tools()

    # Retrieve activated tool definition
    all_tools = tool_registry.list_all_tools()
    for tool in all_tools:
        if tool.name == clean_name:
            return tool

    raise HTTPException(
        status_code=500,
        detail="Tool files saved but failed to load in registry.",
    )
