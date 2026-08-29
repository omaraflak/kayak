"""What a local model server is, independent of which one it is.

:class:`~backend.app.inference.manager.ServerManager` owns lifecycle -- pulling the
image, starting and watching the container, streaming its logs, reconciling reality
after a restart, stopping it again. None of that is specific to what is being served.

A :class:`Runtime` owns everything that is: which image to run, what to pass it, which
health answer counts as ready, and which of the deploy settings it actually honours.
Adding a modality is therefore a new Runtime, not a change to the manager, and a
misbehaving backend cannot destabilise the lifecycle logic that every modality shares.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.app.inference.models import (
    DeployRequest,
    DeploymentProgress,
    HostCapability,
    Modality,
    RuntimeDescriptor,
)


@dataclass(frozen=True)
class SpecContext:
    """What a runtime is allowed to know about the machine when building a spec.

    Passed in rather than read from the manager so that a runtime cannot reach into
    lifecycle state, and so that building a spec is a pure function in tests.
    """
    #: Whether an NVIDIA GPU was detected on the host.
    has_gpu: bool
    #: Memory Docker reports for its host, if it would say.
    docker_memory_bytes: Optional[int]
    #: The Hugging Face token to hand the container, if one is configured.
    hf_token: Optional[str]


@dataclass
class ContainerSpec:
    """Everything needed to run one server container."""
    image: str
    command: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    #: Extra keyword arguments for ``containers.run``: device requests, shm size.
    #: Resource ceilings the user chose are applied by the manager, which owns them
    #: for every runtime alike.
    run_kwargs: Dict[str, Any] = field(default_factory=dict)
    #: Lines explaining the choices above, streamed into the deployment log so that
    #: what the runtime decided is visible rather than implicit.
    notes: List[str] = field(default_factory=list)


class Runtime(ABC):
    """One kind of local model server."""

    #: What this runtime produces. Identifies its manager in the registry.
    modality: Modality
    #: Stable short name, used in logs and as the API path segment.
    key: str
    label: str
    #: How the server is named in status messages and logs, as a sentence subject.
    server_label: str
    description: str
    #: Name of the Docker container. Must be unique per runtime: the manager force
    #: removes whatever holds this name before starting, so two runtimes sharing one
    #: would evict each other.
    container_name: str
    #: Port inside the container that the server listens on.
    container_port: int = 8000
    #: Whether this runtime can also be served by the launcher on the Apple GPU.
    #: Metal is a text-only path; every other runtime skips it entirely.
    supports_metal: bool = False
    #: Hugging Face pipeline tags and libraries this runtime serves. Consumed by the
    #: catalogue through the API so no client carries its own copy.
    pipeline_tags: Tuple[str, ...] = ()
    supported_libraries: Tuple[str, ...] = ()
    supported_id_fragments: Tuple[str, ...] = ()
    #: DeployRequest fields this runtime honours. Anything else is ignored, and the
    #: launch dialog is told not to offer it.
    tunable_fields: Tuple[str, ...] = ()
    #: What the catalogue searches for when this runtime is selected.
    default_query: str = ""

    @property
    @abstractmethod
    def default_port(self) -> int:
        """First host port to try. Neighbours are tried when it is taken."""

    @abstractmethod
    def api_base(self, port: int) -> str:
        """The OpenAI-compatible base URL for a server published on ``port``."""

    @abstractmethod
    async def container_spec(
        self, request: DeployRequest, context: SpecContext
    ) -> ContainerSpec:
        """Builds the image, command and environment for one deployment."""

    @abstractmethod
    def candidate_images(self) -> Tuple[str, ...]:
        """Images this runtime may run, for reporting whether one is already pulled."""

    def augment_capability(
        self, capability: HostCapability, docker_memory_bytes: Optional[int]
    ) -> None:
        """Adds anything this runtime can say about what the machine can serve.

        Default is to add nothing: most runtimes have no sizing advice to give.
        """
        return None

    def health_path(self) -> str:
        """Path whose 200 response proves the server is up and says what it serves."""
        return "/v1/models"

    def serves_model(self, payload: Any, model_id: str) -> bool:
        """Whether a health response says the requested model is being served.

        A 200 alone is not proof: during a switch, the server being replaced can
        still be answering on the same port, so readiness has to be attributed to a
        specific model. The OpenAI ``/v1/models`` shape is the default; a runtime
        answering differently overrides this.
        """
        try:
            served = payload.get("data", [])
        except AttributeError:
            return False
        return any(
            isinstance(entry, dict) and entry.get("id") == model_id for entry in served
        )

    def retry_request(
        self,
        request: DeployRequest,
        status: DeploymentProgress,
        log_history: List[str],
    ) -> Optional[DeployRequest]:
        """A follow-up deployment that would fix a failure this runtime recognises.

        Returning None -- the default -- means a failed start is reported as it is.
        """
        return None

    def can_serve(
        self,
        repo_id: str,
        library_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """Whether this runtime can load a repository, from Hub metadata alone.

        Matched on library first, then on repository-id fragments, because the Hub
        reports no library at all for some of the most popular models -- Kokoro among
        them. A runtime that declares neither serves anything with its pipeline tag,
        which is the case for vLLM.
        """
        if not self.supported_libraries and not self.supported_id_fragments:
            return True
        if library_name and library_name in self.supported_libraries:
            return True
        lowered = repo_id.lower()
        if any(fragment in lowered for fragment in self.supported_id_fragments):
            return True
        return bool(tags and any(tag in self.supported_libraries for tag in tags))

    def describe(self) -> RuntimeDescriptor:
        """The client-facing description of this runtime."""
        return RuntimeDescriptor(
            modality=self.modality,
            key=self.key,
            label=self.label,
            description=self.description,
            pipeline_tags=list(self.pipeline_tags),
            supported_libraries=list(self.supported_libraries),
            supported_id_fragments=list(self.supported_id_fragments),
            tunable_fields=list(self.tunable_fields),
            default_query=self.default_query,
        )
