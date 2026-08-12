from pathlib import Path
from typing import Optional
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings
from backend.app.tools.registry import tool_registry
from backend.app.tools.verifier import clean_tool_identifier, run_tool_verification


async def _verify_tool_in_container(
    container_id: str,
    tool_code: str,
    verify_code: str,
) -> str:
    """Executes verification tests inside a running Docker sandbox container."""
    script = f"""
import sys
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="kayak_verify_") as tmpdir:
    tmp_path = Path(tmpdir)
    tool_file = tmp_path / "tool.py"
    verify_file = tmp_path / "verify.py"
    
    tool_file.write_text({repr(tool_code)}, encoding="utf-8")
    verify_file.write_text({repr(verify_code)}, encoding="utf-8")
    
    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{{str(tmp_path)}}:{{env.get('PYTHONPATH', '')}}"
    
    res = subprocess.run(
        [sys.executable, "verify.py"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    if res.returncode == 0:
        print(f"✓ Verification Passed!\\n\\nSTDOUT:\\n{{res.stdout}}")
    else:
        print(f"✗ Verification Failed (exit code {{res.returncode}})\\n\\nSTDOUT:\\n{{res.stdout}}\\n\\nSTDERR:\\n{{res.stderr}}")
"""
    return await sandbox_manager.exec_python(container_id, script)


async def verify_tool(
    tool_name: str,
    tool_code: str,
    verify_code: str,
    container_id: Optional[str] = None,
) -> str:
    """Executes the verification unit tests for a tool in an isolated test environment and checks schema extraction.

    Args:
        tool_name: Identifier for the tool (e.g. 'fetch_weather').
        tool_code: Python source code for `tool.py`.
        verify_code: Python verification test code for `verify.py`.
        container_id: Optional container ID for running verification inside the sandbox.
    """
    if container_id:
        return await _verify_tool_in_container(container_id, tool_code, verify_code)

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


async def activate_tool(
    tool_name: str, tool_code: str, verify_code: str
) -> str:
    """Saves a verified tool to data/tools/<tool_name>/ and registers it in the Kayak runtime ecosystem.

    Args:
        tool_name: Identifier for the tool.
        tool_code: Python source code for `tool.py`.
        verify_code: Python verification test code for `verify.py`.
    """
    clean_name = clean_tool_identifier(tool_name)
    tool_dir = settings.TOOLS_DIR / clean_name
    tool_dir.mkdir(parents=True, exist_ok=True)

    (tool_dir / "tool.py").write_text(tool_code, encoding="utf-8")
    (tool_dir / "verify.py").write_text(verify_code, encoding="utf-8")

    tool_registry.load_custom_tools()
    return f"Successfully activated and registered tool '{clean_name}'. It is now available across Kayak!"
