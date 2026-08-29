"""Probes what this machine can actually serve.

The deployment path branches on GPU availability -- it picks the CPU image when there
is none -- but nothing used to tell the user, so the first sign of a CPU deployment was
a multi-gigabyte image pull followed by generation slow enough to look broken.
Reporting the host's capability up front turns that into a decision instead of a
discovery.

The question being asked matters. It is not "can this process see a GPU" but "can the
Docker daemon give one to a container", and those differ exactly where it counts:
Kayak normally runs inside a container of its own, which has no `nvidia-smi` and no
device access, so asking the first question answered "no GPU" on every machine that
ships this way -- a workstation with a 4090 quietly served models on its CPU.

The daemon knows, and says so: the NVIDIA container toolkit registers a runtime, which
`docker info` lists. `nvidia-smi` on this process's own PATH remains a second signal,
for a Kayak run directly on the host.
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


#: Runtime the NVIDIA container toolkit registers with the Docker daemon. Its
#: presence is what decides whether a container can be given a GPU at all.
NVIDIA_RUNTIME = "nvidia"


def daemon_offers_gpu(docker_info: Optional[dict]) -> bool:
    """Whether the Docker daemon can hand a GPU to a container.

    Read from the daemon rather than from this process: Kayak usually runs inside
    a container that has neither the NVIDIA tools nor device access, so anything
    it can see for itself says nothing about what it can start.

    Args:
        docker_info: The daemon's ``info()`` payload, or None if unavailable.
    """
    if not docker_info:
        return False
    runtimes = docker_info.get("Runtimes") or {}
    return any(NVIDIA_RUNTIME in str(name).lower() for name in runtimes)


async def probe_host_capability(
    docker_available: bool,
    image_present: Optional[bool] = None,
    docker_info: Optional[dict] = None,
) -> HostCapability:
    """Describes the host's ability to serve local models.

    Args:
        docker_available: Whether the Docker daemon responded to a ping.
        image_present: Whether the vLLM image is already pulled, when known.
        docker_info: The daemon's info payload, which names its runtimes.

    Returns:
        HostCapability: GPU inventory plus the image the deployment path would select.
    """
    loop = asyncio.get_running_loop()
    gpus = await loop.run_in_executor(None, _probe_gpus_blocking)
    # Either signal is enough: the daemon's runtime list covers the containerised
    # Kayak, and nvidia-smi covers one run directly on the host. The inventory of
    # cards is only readable in the second case, so a GPU with no listed devices
    # is normal rather than a contradiction.
    has_gpu = bool(gpus) or daemon_offers_gpu(docker_info)

    return HostCapability(
        docker_available=docker_available,
        gpus=gpus,
        total_vram_mb=sum(gpu.total_memory_mb for gpu in gpus),
        accelerator="cuda" if has_gpu else "cpu",
        image_present=image_present,
    )
