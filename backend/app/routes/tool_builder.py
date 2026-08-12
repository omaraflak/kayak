from fastapi import APIRouter, HTTPException
from backend.app.config import settings
from backend.app.models import (
    ActivateToolRequest,
    ToolDefinition,
    VerifyToolRequest,
    VerifyToolResponse,
)
from backend.app.tools.registry import tool_registry
from backend.app.tools.verifier import clean_tool_identifier, run_tool_verification

router = APIRouter(prefix="/api/tool-builder", tags=["tool-builder"])


@router.post("/verify", response_model=VerifyToolResponse)
async def verify_tool_code(request: VerifyToolRequest) -> VerifyToolResponse:
    """Executes the tool verification script in an isolated temp directory to validate correctness.

    Args:
        request: VerifyToolRequest containing tool_name, tool_code, and verify_code.

    Returns:
        VerifyToolResponse containing test execution output, extracted schema, and status.
    """
    result = await run_tool_verification(
        tool_name=request.tool_name,
        tool_code=request.tool_code,
        verify_code=request.verify_code,
    )
    return VerifyToolResponse(
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        parsed_schema=result.parsed_schema,
        error=result.error,
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
    clean_name = clean_tool_identifier(request.tool_name)
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
