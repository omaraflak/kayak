from fastapi import APIRouter, HTTPException
from backend.app.models import (
    ActivateToolRequest,
    ToolDefinition,
    VerifyToolRequest,
    VerifyToolResponse,
)
from backend.app.tools.activation import activate_verified_tool
from backend.app.tools.verifier import run_tool_verification

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
    """Verifies the tool code and, if its tests pass, installs and registers it.

    Verification is re-run here rather than trusted from a prior call, so that this
    endpoint holds the same invariant no matter who calls it.

    Args:
        request: ActivateToolRequest with tool files and name.

    Returns:
        The newly activated ToolDefinition record.

    Raises:
        HTTPException: If verification fails or the tool could not be registered.
    """
    result = await activate_verified_tool(
        tool_name=request.tool_name,
        tool_code=request.tool_code,
        verify_code=request.verify_code,
    )

    if not result.success or not result.definition:
        raise HTTPException(status_code=422, detail=result.message)

    return result.definition
