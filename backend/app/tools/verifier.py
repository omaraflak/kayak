import asyncio
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Optional
from backend.app.tools.registry import extract_tool_schema


@dataclass
class ToolVerificationResult:
    """Encapsulates output, status, and schema from a tool verification test run."""
    success: bool
    stdout: str
    stderr: str
    parsed_schema: Optional[Dict[str, Any]]
    error: Optional[str]


def clean_tool_identifier(name: str) -> str:
    """Sanitizes a tool name into a valid Python identifier."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())


def _extract_module_schema(tool_file: Path, clean_name: str) -> Optional[Dict[str, Any]]:
    """Loads a tool module and extracts its function schema."""
    spec = importlib.util.spec_from_file_location(
        f"tmp_verify_{clean_name}", tool_file
    )
    if not spec or not spec.loader:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target_func = (
        getattr(module, "execute", None)
        or getattr(module, "main", None)
        or getattr(module, clean_name, None)
    )

    if not target_func:
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and not attr_name.startswith("_"):
                target_func = attr
                break

    if target_func:
        schema_data = extract_tool_schema(target_func, clean_name)
        return schema_data.get("function")
    return None


async def run_tool_verification(
    tool_name: str,
    tool_code: str,
    verify_code: str,
    timeout_seconds: int = 30,
) -> ToolVerificationResult:
    """Executes a tool and its verify.py test suite in an isolated temporary directory.

    Args:
        tool_name: Raw name of the tool.
        tool_code: Complete Python source code for tool.py.
        verify_code: Complete Python unit test code for verify.py.
        timeout_seconds: Subprocess execution timeout.

    Returns:
        ToolVerificationResult with execution output, return status, and extracted schema.
    """
    clean_name = clean_tool_identifier(tool_name)

    with tempfile.TemporaryDirectory(prefix=f"kayak_verify_{clean_name}_") as tmpdir:
        tmp_path = Path(tmpdir)
        tool_file = tmp_path / "tool.py"
        verify_file = tmp_path / "verify.py"

        tool_file.write_text(tool_code, encoding="utf-8")
        verify_file.write_text(verify_code, encoding="utf-8")

        # 1. Parse and extract schema from tool.py
        parsed_schema = None
        try:
            parsed_schema = _extract_module_schema(tool_file, clean_name)
        except Exception as error:
            return ToolVerificationResult(
                success=False,
                stdout="",
                stderr=f"Syntax/Import error in tool.py: {str(error)}",
                parsed_schema=None,
                error=f"Syntax/Import error in tool.py: {str(error)}",
            )

        # 2. Run verify.py in isolated subprocess
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{str(tmp_path)}:{env.get('PYTHONPATH', '')}"

            process = await asyncio.create_subprocess_exec(
                "python3",
                "verify.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmp_path),
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            success = process.returncode == 0

            return ToolVerificationResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                parsed_schema=parsed_schema,
                error=None if success else f"Tests failed with exit code {process.returncode}",
            )
        except asyncio.TimeoutError:
            return ToolVerificationResult(
                success=False,
                stdout="",
                stderr=f"Verification script timed out after {timeout_seconds} seconds.",
                parsed_schema=parsed_schema,
                error="Timeout",
            )
        except Exception as error:
            return ToolVerificationResult(
                success=False,
                stdout="",
                stderr=str(error),
                parsed_schema=parsed_schema,
                error=str(error),
            )
