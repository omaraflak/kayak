from typing import Any, Dict, List
import shutil
from fastapi import APIRouter, HTTPException
from backend.app.config import settings
from backend.app.models import ToolCategoryInfo, ToolDefinition
from backend.app.tools.metadata import list_categories
from backend.app.tools.registry import tool_registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=List[ToolDefinition])
async def list_all_tools() -> List[ToolDefinition]:
    """Lists all registered tools (both builtins and verified custom tools).

    Returns:
        A list of ToolDefinition representations.
    """
    return tool_registry.list_all_tools()


@router.get("/categories", response_model=List[ToolCategoryInfo])
async def list_tool_categories() -> List[ToolCategoryInfo]:
    """Lists tool categories with display labels, in presentation order.

    Served from the backend so clients group tools from data rather than carrying
    their own copy of the taxonomy, which would drift as tools are added.

    Returns:
        Ordered list of ToolCategoryInfo entries.
    """
    return list_categories()


@router.post("/reload")
async def reload_tool_registry() -> Dict[str, Any]:
    """Scans data/tools/ and reloads all valid custom tool implementations.

    Returns:
        Status response with total count of loaded tools.
    """
    tool_registry.load_custom_tools()
    return {
        "status": "reloaded",
        "total_tools": len(tool_registry.list_all_tools()),
    }


@router.delete("/{tool_name}")
async def delete_custom_tool(tool_name: str) -> Dict[str, str]:
    """Deletes a custom user tool from data/tools/ and unloads it from the registry.

    Args:
        tool_name: Identifier for the custom tool to delete.

    Returns:
        Status response dictionary.

    Raises:
        HTTPException: If the tool does not exist or is a protected builtin tool.
    """
    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"Tool '{tool_name}' not found"
        )

    tool_directory = settings.TOOLS_DIR / tool_name
    if not tool_directory.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete builtin or non-directory tool '{tool_name}'",
        )

    shutil.rmtree(tool_directory, ignore_errors=True)
    tool_registry.load_custom_tools()
    return {"status": "deleted"}
