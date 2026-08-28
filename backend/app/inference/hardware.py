"""Probes what this machine can actually serve.

The deployment path already branches on GPU availability -- it silently picks the CPU
image when ``nvidia-smi`` is missing -- but nothing ever told the user, so the first
sign of a CPU deployment was a multi-gigabyte image pull followed by generation slow
enough to look broken. Reporting the host's capability up front turns that into a
decision instead of a discovery.
"""

import asyncio
import shutil
import subprocess
from typing import List, Optional

from backend.app.inference.models import GPUDevice, HostCapability

NVIDIA_SMI_QUERY = [
    "--query-gpu=name,memory.total",
    "--format=csv,noheader,nounits",
]

# nvidia-smi occasionally blocks on a wedged driver; a probe is never worth hanging a
# request for.
PROBE_TIMEOUT_SECONDS = 3.0


def parse_nvidia_smi_output(raw: str) -> List[GPUDevice]:
    """Parses ``name, memory.total`` CSV rows into GPU descriptions.

    Args:
        raw: Raw stdout from nvidia-smi in ``csv,noheader,nounits`` format.

    Returns:
        One entry per GPU. Malformed rows are skipped rather than failing the probe --
        a partially readable GPU list is more useful than none.
    """
    devices: List[GPUDevice] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            memory_mb = int(float(parts[1]))
        except ValueError:
            continue
        if not parts[0]:
            continue
        devices.append(GPUDevice(name=parts[0], total_memory_mb=memory_mb))

    return devices


def _probe_gpus_blocking() -> List[GPUDevice]:
    """Runs nvidia-smi and parses its output. Returns an empty list on any failure."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []

    try:
        completed = subprocess.run(
            [executable, *NVIDIA_SMI_QUERY],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return []

    if completed.returncode != 0:
        return []

    return parse_nvidia_smi_output(completed.stdout)


async def probe_host_capability(
    docker_available: bool,
    image_present: Optional[bool] = None,
) -> HostCapability:
    """Describes the host's ability to serve local models.

    Args:
        docker_available: Whether the Docker daemon responded to a ping.
        image_present: Whether the vLLM image is already pulled, when known.

    Returns:
        HostCapability: GPU inventory plus the image the deployment path would select.
    """
    loop = asyncio.get_running_loop()
    gpus = await loop.run_in_executor(None, _probe_gpus_blocking)

    return HostCapability(
        docker_available=docker_available,
        gpus=gpus,
        total_vram_mb=sum(gpu.total_memory_mb for gpu in gpus),
        accelerator="cuda" if gpus else "cpu",
        image_present=image_present,
    )
