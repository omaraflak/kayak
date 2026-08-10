import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Optional
from backend.app.agent.sandbox import sandbox_manager
from backend.app.config import settings
from backend.app.tools.registry import extract_tool_schema, tool_registry


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
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name.lower().strip())

    if container_id:
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

    with tempfile.TemporaryDirectory(
        prefix=f"kayak_verify_{clean_name}_"
    ) as tmpdir:
        tmp_path = Path(tmpdir)
        tool_file = tmp_path / "tool.py"
        verify_file = tmp_path / "verify.py"

        tool_file.write_text(tool_code, encoding="utf-8")
        verify_file.write_text(verify_code, encoding="utf-8")

        # 1. Schema check
        schema_info = ""
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
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if callable(attr) and not attr_name.startswith("_"):
                            target_func = attr
                            break
                if target_func:
                    schema = extract_tool_schema(target_func, clean_name).get(
                        "function"
                    )
                    schema_info = f"\n✓ Auto-extracted JSON Schema: {schema}"
        except Exception as e:
            return f"Error importing tool.py: {str(e)}"

        # 2. Run verify.py
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
                process.communicate(), timeout=30
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if process.returncode == 0:
                return (
                    f"✓ Verification Passed!\n\nSTDOUT:\n{stdout}{schema_info}"
                )
            else:
                return (
                    f"✗ Verification Failed (exit code"
                    f" {process.returncode})\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
                )

        except asyncio.TimeoutError:
            return "✗ Verification timed out after 30 seconds."
        except Exception as e:
            return f"✗ Verification error: {str(e)}"


async def activate_tool(
    tool_name: str, tool_code: str, verify_code: str
) -> str:
    """Saves a verified tool to data/tools/<tool_name>/ and registers it in the Kayak runtime ecosystem.

    Args:
        tool_name: Identifier for the tool.
        tool_code: Python source code for `tool.py`.
        verify_code: Python verification test code for `verify.py`.
    """
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name.lower().strip())
    tool_dir = settings.TOOLS_DIR / clean_name
    tool_dir.mkdir(parents=True, exist_ok=True)

    (tool_dir / "tool.py").write_text(tool_code, encoding="utf-8")
    (tool_dir / "verify.py").write_text(verify_code, encoding="utf-8")

    tool_registry.load_custom_tools()
    return f"Successfully activated and registered tool '{clean_name}'. It is now available across Kayak!"
