import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
import docker
from docker.errors import DockerException, NotFound
from backend.app.config import settings


class SandboxManager:
    """Manages Docker sandbox containers for isolated agent execution."""

    def __init__(self):
        self._client: Optional[docker.DockerClient] = None
        self._docker_available: bool = False
        self._init_client()

    def _init_client(self):
        try:
            self._client = docker.from_env()
            self._client.ping()
            self._docker_available = True
        except Exception:
            self._client = None
            self._docker_available = False

    @property
    def is_available(self) -> bool:
        return self._docker_available

    async def create_sandbox(
        self, conversation_id: str, workspace_dir: Path
    ) -> str:
        """Creates and starts an isolated Docker container for a conversation, mounting the workspace and full repository codebase.

        Args:
            conversation_id: Conversation identifier.
            workspace_dir: Host directory for conversation workspace.

        Returns:
            str: Container ID.
        """
        if not self._docker_available or not self._client:
            raise RuntimeError(
                "Docker is not available or Docker socket is not mounted."
            )

        container_name = f"kayak-sandbox-{conversation_id[:8]}"

        # Check if already exists
        try:
            existing = self._client.containers.get(container_name)
            if existing.status != "running":
                existing.start()
            return existing.id
        except NotFound:
            pass

        # Try designated image or fallback
        image_name = settings.DOCKER_SANDBOX_IMAGE
        try:
            self._client.images.get(image_name)
        except Exception:
            image_name = "python:3.11-slim"

        # Build volumes map: Mount workspace, full codebase, skills and tools
        volumes_map: Dict[str, Dict[str, str]] = {
            str(workspace_dir.resolve()): {
                "bind": "/workspace",
                "mode": "rw",
            },
        }

        # Mount codebase into /app if exists
        if settings.BASE_DIR.exists():
            volumes_map[str(settings.BASE_DIR.resolve())] = {
                "bind": "/app",
                "mode": "ro",
            }

        # Mount data directory into /data, /skills, /tools
        if settings.DATA_DIR.exists():
            volumes_map[str(settings.DATA_DIR.resolve())] = {
                "bind": "/data",
                "mode": "ro",
            }
        if settings.SKILLS_DIR.exists():
            volumes_map[str(settings.SKILLS_DIR.resolve())] = {
                "bind": "/skills",
                "mode": "ro",
            }
        if settings.TOOLS_DIR.exists():
            volumes_map[str(settings.TOOLS_DIR.resolve())] = {
                "bind": "/tools",
                "mode": "ro",
            }

        container = self._client.containers.run(
            image=image_name,
            name=container_name,
            command="tail -f /dev/null",  # Keep container alive
            detach=True,
            working_dir="/workspace",
            volumes=volumes_map,
            network_mode="bridge",
            mem_limit="2g",
            cpu_quota=100000,  # 1 CPU
            remove=False,
        )

        return container.id

    async def exec_command(
        self, container_id: str, command: str, timeout: Optional[int] = 60
    ) -> str:
        """Executes a command inside the container synchronously."""
        if not self._docker_available or not self._client:
            raise RuntimeError("Docker is not available.")

        container = self._client.containers.get(container_id)
        if container.status != "running":
            container.start()

        # Run via /bin/bash or /bin/sh
        exec_result = container.exec_run(
            cmd=["/bin/bash", "-c", command],
            workdir="/workspace",
            demux=True,  # demux stdout and stderr
        )

        exit_code = exec_result.exit_code
        stdout_bytes, stderr_bytes = exec_result.output

        stdout_str = (
            stdout_bytes.decode("utf-8", errors="replace")
            if stdout_bytes
            else ""
        )
        stderr_str = (
            stderr_bytes.decode("utf-8", errors="replace")
            if stderr_bytes
            else ""
        )

        output = []
        if stdout_str:
            output.append(stdout_str)
        if stderr_str:
            output.append(f"STDERR:\n{stderr_str}")
        if exit_code != 0:
            output.append(f"\n[Exit code: {exit_code}]")

        return "\n".join(output) if output else "Command executed with no output."

    async def exec_python(
        self, container_id: str, python_code: str, timeout: Optional[int] = 60
    ) -> str:
        """Executes Python code directly inside the container."""
        if not self._docker_available or not self._client:
            raise RuntimeError("Docker is not available.")

        container = self._client.containers.get(container_id)
        if container.status != "running":
            container.start()

        exec_result = container.exec_run(
            cmd=["python3", "-c", python_code],
            workdir="/workspace",
            demux=True,
        )

        stdout_bytes, stderr_bytes = exec_result.output
        stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if exec_result.exit_code != 0 and stderr_str:
            return f"Error ({exec_result.exit_code}):\n{stderr_str}"
        return stdout_str or stderr_str or "Execution completed with no output."

    async def exec_custom_tool(
        self,
        container_id: str,
        tool_name: str,
        tool_code: str,
        arguments: Dict[str, Any],
    ) -> str:
        """Executes custom tool python code inside the container sandbox."""
        runner_script = f"""
import json
import sys

# Define tool implementation
{tool_code}

# Target execution
args = json.loads({repr(json.dumps(arguments))})
fn = globals().get('execute') or globals().get('main') or globals().get({repr(tool_name)})
if not fn:
    # Try any function not starting with _
    for k, v in list(globals().items()):
        if callable(v) and not k.startswith('_') and k not in ['json', 'sys']:
            fn = v
            break

if not fn:
    print(f"Error: Function for tool '{tool_name}' not found.")
    sys.exit(1)

try:
    result = fn(**args)
    if result is not None:
        print(str(result))
except Exception as e:
    print(f"Error: {{e}}")
    sys.exit(1)
"""
        return await self.exec_python(container_id, runner_script)

    async def exec_background_command(
        self, container_id: str, command: str
    ) -> Any:
        """Executes a detached background command in the container."""
        if not self._docker_available or not self._client:
            raise RuntimeError("Docker is not available.")

        container = self._client.containers.get(container_id)
        return container.exec_run(
            cmd=["/bin/bash", "-c", f"nohup {command} > /tmp/task.log 2>&1 &"],
            workdir="/workspace",
            detach=True,
        )

    async def stop_and_remove_sandbox(self, container_id: str):
        """Stops and removes the container."""
        if not self._docker_available or not self._client:
            return

        try:
            container = self._client.containers.get(container_id)
            container.stop(timeout=2)
            container.remove(v=True, force=True)
        except Exception:
            pass


sandbox_manager = SandboxManager()
