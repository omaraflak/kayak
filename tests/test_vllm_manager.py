import asyncio
from typing import Any, Dict, Optional

import pytest

from docker.errors import APIError

from backend.app.inference.manager import ServerManager, StatusBroadcaster
from backend.app.inference.models import DeploymentProgress, ServerState
from backend.app.inference.vllm_runtime import VLLMRuntime


class FakeContainer:
    """Stands in for a docker-py container object."""

    def __init__(self, status: str, exit_code: int = 0, oom_killed: bool = False):
        self.id = "c" * 64
        self.status = status
        self.attrs: Dict[str, Any] = {
            "State": {"ExitCode": exit_code, "OOMKilled": oom_killed},
            "Config": {"Cmd": ["--model", "Org/Model"]},
        }


class FakeContainers:
    def __init__(self, container: Optional[FakeContainer]):
        self._container = container

    def get(self, _name: str) -> FakeContainer:
        if self._container is None:
            raise RuntimeError("No such container")
        return self._container


class FakeDockerClient:
    def __init__(self, container: Optional[FakeContainer]):
        self.containers = FakeContainers(container)


class UnreachableClient:
    """An httpx.AsyncClient whose every request fails, i.e. nothing is serving."""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("nothing listening")


@pytest.fixture
def manager(monkeypatch) -> ServerManager:
    """A manager with Docker stubbed out and no live endpoint."""
    monkeypatch.setattr("backend.app.inference.manager.httpx.AsyncClient", UnreachableClient)

    instance = ServerManager.__new__(ServerManager)
    instance._runtime = VLLMRuntime()
    instance._broadcaster = StatusBroadcaster()
    instance._init_state()
    instance._docker_available = True
    return instance


class TestCrashedContainerIsReported:
    """
    A vLLM container that dies during startup -- which is what running out of memory
    looks like -- used to leave the status on 'loading' forever: the endpoint never
    answers, and no branch handled a container that exists but is not running.
    """

    def test_a_container_that_exited_while_loading_becomes_an_error(self, manager):
        manager._client = FakeDockerClient(FakeContainer("exited", exit_code=1))
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert status.exit_code == 1
        assert "exited with code 1" in (status.error or "")

    def test_an_out_of_memory_kill_says_so(self, manager):
        manager._client = FakeDockerClient(
            FakeContainer("exited", exit_code=137, oom_killed=True)
        )
        manager._status.state = ServerState.STARTING_CONTAINER

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        # The remedy belongs in the message; the exit code alone explains nothing.
        assert "memory" in (status.error or "").lower()
        assert "max-model-len" in (status.error or "")

    def test_a_running_container_still_reports_as_initializing(self, manager):
        manager._client = FakeDockerClient(FakeContainer("running"))
        manager._status.state = ServerState.LOADING

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.LOADING

    def test_an_idle_manager_with_no_container_stays_idle(self, manager):
        manager._client = FakeDockerClient(None)

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.IDLE

    def test_a_ready_server_that_stops_answering_is_reported_stopped(self, manager):
        manager._client = FakeDockerClient(None)
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.STOPPED

    def test_a_serving_model_that_crashes_reports_why(self, manager):
        # A long context can exhaust memory well after the server came up healthy.
        manager._client = FakeDockerClient(
            FakeContainer("exited", exit_code=137, oom_killed=True)
        )
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert "memory" in (status.error or "").lower()

    def test_an_orderly_shutdown_is_not_an_error(self, manager):
        manager._client = FakeDockerClient(FakeContainer("exited", exit_code=0))
        manager._status.state = ServerState.READY

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.STOPPED


class TestServingGuard:
    def test_weights_in_use_are_recognized(self, manager):
        manager._status.model_id = "Org/Model"
        manager._status.state = ServerState.READY

        assert manager.is_serving("Org/Model") is True
        assert manager.is_serving("Org/Other") is False

    def test_a_stopped_server_does_not_hold_its_weights(self, manager):
        manager._status.model_id = "Org/Model"
        manager._status.state = ServerState.STOPPED

        assert manager.is_serving("Org/Model") is False


class TestMetalIntegration:
    def test_deploy_mlx_model_when_metal_supported(self, manager, tmp_path, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus, DeployRequest

        monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/Qwen2.5-7B"),
        )

        progress = asyncio.run(
            manager.deploy_model(DeployRequest(model_id="mlx-community/Qwen2.5-7B"))
        )

        assert progress.state in (ServerState.STARTING_CONTAINER, ServerState.LOADING, ServerState.READY)
        assert progress.model_id == "mlx-community/Qwen2.5-7B"

        # Check desired file was written
        desired_file = tmp_path / metal.CONTROL_DIRNAME / metal.DESIRED_FILENAME
        assert desired_file.exists()
        import json
        payload = json.loads(desired_file.read_text(encoding="utf-8"))
        assert payload["metal"]["running"] is True
        assert payload["metal"]["model"] == "mlx-community/Qwen2.5-7B"

    def test_deploy_mlx_model_when_metal_unsupported(self, manager, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus, DeployRequest

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=False),
        )

        progress = asyncio.run(
            manager.deploy_model(DeployRequest(model_id="mlx-community/Qwen2.5-7B"))
        )

        assert progress.state == ServerState.ERROR
        assert "MLX models require Apple Silicon" in progress.message

    def test_check_and_sync_status_reports_metal_ready(self, manager, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/Qwen2.5-7B", port=8001),
        )

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.READY
        assert status.model_id == "mlx-community/Qwen2.5-7B"
        assert status.port == 8001

    def test_check_and_sync_status_reports_metal_starting(self, manager, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="starting", model="mlx-community/Qwen2.5-7B"),
        )

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.LOADING
        assert status.model_id == "mlx-community/Qwen2.5-7B"

    def test_monitor_ignores_a_status_about_another_model(self, manager, monkeypatch):
        """The launcher reconciles on a delay, so right after a start request
        the status file still describes the previous server — often as
        "ready". Acting on it is the bug where a model showed as serving the
        moment start was clicked, before anything was listening."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        responses = [
            MetalStatus(supported=True, state="ready", model="mlx-community/Old"),
            MetalStatus(supported=True, state="ready", model="mlx-community/New"),
        ]
        monkeypatch.setattr(
            metal, "read_status", lambda: responses.pop(0) if len(responses) > 1 else responses[0]
        )

        async def instant_sleep(_delay):
            pass

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", instant_sleep)

        manager._status.model_id = "mlx-community/New"
        manager._status.state = ServerState.LOADING
        seen_states = []
        original = manager._update_status

        def record(**kwargs):
            seen_states.append(kwargs.get("state"))
            original(**kwargs)

        manager._update_status = record
        asyncio.run(manager._run_metal_deployment("mlx-community/New", timeout_seconds=30))

        # The stale "ready" for the old model must not have produced a READY;
        # only the status naming the new model may.
        assert seen_states == [ServerState.READY]
        assert manager._status.model_id == "mlx-community/New"

    def test_monitor_waits_for_its_own_request_to_be_answered(self, manager, monkeypatch):
        """With a launcher new enough to echo tokens, even a "ready" for the
        right model is ignored until it carries this deployment's token — the
        file may predate the restart that was just asked for."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        responses = [
            MetalStatus(
                supported=True, state="ready", model="mlx-community/M",
                request="stale", acknowledges_requests=True,
            ),
            MetalStatus(
                supported=True, state="ready", model="mlx-community/M",
                request="tok", acknowledges_requests=True,
            ),
        ]
        monkeypatch.setattr(
            metal, "read_status", lambda: responses.pop(0) if len(responses) > 1 else responses[0]
        )

        async def instant_sleep(_delay):
            pass

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", instant_sleep)

        manager._status.model_id = "mlx-community/M"
        manager._status.state = ServerState.LOADING
        asyncio.run(
            manager._run_metal_deployment("mlx-community/M", request_token="tok", timeout_seconds=30)
        )

        assert manager._status.state == ServerState.READY

    def test_check_and_sync_defers_to_a_deployment_in_flight(self, manager, monkeypatch):
        """While a Metal deployment is running, a lagging status file must not
        overwrite the in-flight state with the previous server's "ready"."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus, DeploymentProgress

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/Old"),
        )

        async def scenario():
            manager._status = DeploymentProgress(
                model_id="mlx-community/New", state=ServerState.LOADING
            )
            manager._metal_monitor_task = asyncio.create_task(asyncio.sleep(30))
            try:
                return await manager.check_and_sync_status()
            finally:
                manager._metal_monitor_task.cancel()

        status = asyncio.run(scenario())

        assert status.state == ServerState.LOADING
        assert status.model_id == "mlx-community/New"

    def test_concurrent_deploys_serialize_and_the_last_one_wins(self, manager, tmp_path, monkeypatch):
        """Two rapid start clicks used to race two deployment runners against each
        other. Serialized, the second supersedes the first and only the second's
        monitor may write status."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus, DeployRequest

        monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/B"),
        )

        real_sleep = asyncio.sleep

        async def quick_sleep(_delay):
            await real_sleep(0)

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", quick_sleep)

        async def scenario():
            await asyncio.gather(
                manager.deploy_model(DeployRequest(model_id="mlx-community/A")),
                manager.deploy_model(DeployRequest(model_id="mlx-community/B")),
            )
            await asyncio.wait_for(manager._metal_monitor_task, timeout=5)
            return manager.get_status()

        status = asyncio.run(scenario())

        assert status.model_id == "mlx-community/B"
        assert status.state == ServerState.READY

        import json
        desired_file = tmp_path / metal.CONTROL_DIRNAME / metal.DESIRED_FILENAME
        payload = json.loads(desired_file.read_text(encoding="utf-8"))
        assert payload["metal"]["model"] == "mlx-community/B"

    def test_stop_supersedes_an_in_flight_deployment(self, manager, tmp_path, monkeypatch):
        """Stopping mid-deployment must silence the deployment's monitor: it used to
        keep running and could flip the status back after the stop."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus, DeployRequest

        monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="starting", model="mlx-community/A"),
        )

        real_sleep = asyncio.sleep

        async def quick_sleep(_delay):
            await real_sleep(0)

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", quick_sleep)

        async def scenario():
            await manager.deploy_model(DeployRequest(model_id="mlx-community/A"))
            monitor = manager._metal_monitor_task
            await manager.stop_server()
            # Give a still-running monitor every chance to misbehave.
            for _ in range(5):
                await real_sleep(0)
            return monitor

        monitor = asyncio.run(scenario())

        assert monitor.done()
        assert manager.get_status().state == ServerState.STOPPED

        import json
        desired_file = tmp_path / metal.CONTROL_DIRNAME / metal.DESIRED_FILENAME
        payload = json.loads(desired_file.read_text(encoding="utf-8"))
        assert payload["metal"]["running"] is False

    def test_superseded_monitor_never_writes_status(self, manager, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/M"),
        )

        async def instant_sleep(_delay):
            pass

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", instant_sleep)

        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"
        stale_generation = manager._deploy_generation
        manager._deploy_generation += 1  # a stop or newer deploy has happened

        asyncio.run(
            manager._run_metal_deployment(
                "mlx-community/M", generation=stale_generation, timeout_seconds=30
            )
        )

        assert manager.get_status().state == ServerState.LOADING

    def test_check_and_sync_defers_to_a_docker_deployment_in_flight(self, manager, monkeypatch):
        """A lagging Metal status file must not clobber an in-flight *Docker*
        deployment either: right after switching from a Metal model to a container
        model, the file still says "ready" for the old server."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/Old"),
        )

        async def scenario():
            manager._status = DeploymentProgress(
                model_id="Org/New", state=ServerState.LOADING
            )
            manager._monitor_task = asyncio.create_task(asyncio.sleep(30))
            try:
                return await manager.check_and_sync_status()
            finally:
                manager._monitor_task.cancel()

        status = asyncio.run(scenario())

        assert status.state == ServerState.LOADING
        assert status.model_id == "Org/New"

    def test_stop_server_stops_metal_when_supported(self, manager, tmp_path, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(supported=True, state="ready", model="mlx-community/Qwen2.5-7B"),
        )

        asyncio.run(manager.stop_server())

        desired_file = tmp_path / metal.CONTROL_DIRNAME / metal.DESIRED_FILENAME
        assert desired_file.exists()
        import json
        payload = json.loads(desired_file.read_text(encoding="utf-8"))
        assert payload["metal"]["running"] is False


class ModelListClient:
    """An httpx.AsyncClient whose /v1/models answers with a fixed model list."""

    payload: Dict[str, Any] = {"data": []}

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return ModelListClient.payload

        return Response()


class TestHealthPollVerifiesTheModel:
    """READY used to mean "something answered 200 on the port". During a backend
    switch that something can be the previous server still winding down, so the
    poll must insist the answer names the model being deployed."""

    def test_ready_when_the_endpoint_serves_the_requested_model(self, manager, monkeypatch):
        monkeypatch.setattr("backend.app.inference.manager.httpx.AsyncClient", ModelListClient)
        ModelListClient.payload = {"data": [{"id": "Org/Model", "object": "model"}]}

        async def instant_sleep(_delay):
            pass

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", instant_sleep)

        manager._client = FakeDockerClient(FakeContainer("running"))
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        asyncio.run(manager._poll_health_endpoint("Org/Model", timeout_seconds=30))

        assert manager.get_status().state == ServerState.READY

    def test_an_answer_for_another_model_is_not_ready(self, manager, monkeypatch):
        monkeypatch.setattr("backend.app.inference.manager.httpx.AsyncClient", ModelListClient)
        ModelListClient.payload = {"data": [{"id": "Org/Previous", "object": "model"}]}

        iterations = 0

        async def counting_sleep(_delay):
            nonlocal iterations
            iterations += 1
            if iterations > 4:
                # End the poll the way a stop would, so the test does not have to
                # wait out the wall-clock deadline.
                manager._deploy_generation += 1

        monkeypatch.setattr("backend.app.inference.manager.asyncio.sleep", counting_sleep)

        manager._client = FakeDockerClient(FakeContainer("running"))
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        asyncio.run(manager._poll_health_endpoint("Org/Model", timeout_seconds=30))

        assert manager.get_status().state == ServerState.LOADING

    def test_endpoint_serves_model_handles_malformed_payloads(self, manager):
        serves = manager._endpoint_serves_model
        assert serves({"data": [{"id": "Org/M"}]}, "Org/M") is True
        assert serves({"data": [{"id": "Org/Other"}]}, "Org/M") is False
        assert serves({"data": []}, "Org/M") is False
        assert serves({}, "Org/M") is False
        assert serves(["not", "a", "dict"], "Org/M") is False
        assert serves({"data": ["not-a-dict"]}, "Org/M") is False


class TestForceRestart:
    @pytest.fixture
    def docker_manager(self, manager, tmp_path, monkeypatch):
        """A manager whose docker deployment runner is recorded rather than run."""
        from backend.app.inference import manager as manager_module
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        monkeypatch.setattr(metal, "status_path", lambda: tmp_path / "absent.json")

        manager._client = FakeDockerClient(None)
        manager.deploy_calls = []

        async def fake_run_deployment(request, _hf_cache_dir, generation):
            manager.deploy_calls.append((request.model_id, generation))

        manager._run_deployment = fake_run_deployment
        return manager

    def test_a_bare_deploy_of_the_active_model_is_a_no_op(self, docker_manager):
        from backend.app.inference.models import DeployRequest

        docker_manager._status.state = ServerState.READY
        docker_manager._status.model_id = "Org/Model"

        status = asyncio.run(
            docker_manager.deploy_model(DeployRequest(model_id="Org/Model"))
        )

        assert status.state == ServerState.READY
        assert docker_manager.deploy_calls == []

    def test_force_restart_redeploys_the_active_model(self, docker_manager):
        from backend.app.inference.models import DeployRequest

        docker_manager._status.state = ServerState.READY
        docker_manager._status.model_id = "Org/Model"

        status = asyncio.run(
            docker_manager.deploy_model(
                DeployRequest(model_id="Org/Model", max_model_len=4096, force_restart=True)
            )
        )

        assert status.state == ServerState.STARTING_CONTAINER
        assert [call[0] for call in docker_manager.deploy_calls] == ["Org/Model"]


class TestInfrastructureResilience:
    def test_docker_probe_is_retried_when_unavailable(self, manager):
        """Starting Docker Desktop after Kayak must not require restarting Kayak."""
        manager._docker_available = False
        manager._client = None

        def fake_init():
            manager._client = FakeDockerClient(None)
            manager._docker_available = True

        manager._init_docker = fake_init

        assert asyncio.run(manager._ensure_docker()) is True
        assert manager._docker_available is True

    def test_a_full_listener_queue_keeps_the_newest_status(self, manager):
        """A slow SSE consumer must lose old events, never the latest one -- dropping
        the final "ready" left that tab showing a deployment in progress forever."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        manager._broadcaster._listeners.add(queue)

        for index in range(4):
            manager._broadcast({"type": "status", "index": index})

        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())

        assert drained[-1]["index"] == 3
        assert len(drained) == 2


class TestMetalCrashAfterReady:
    def test_a_metal_server_that_errors_while_serving_reports_the_reason(self, manager, monkeypatch):
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(
            metal,
            "read_status",
            lambda: MetalStatus(
                supported=True,
                state="error",
                model="mlx-community/M",
                error="the GPU ran out of memory",
            ),
        )

        manager._status.state = ServerState.READY
        manager._status.model_id = "mlx-community/M"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert "out of memory" in (status.error or "")


class TestContextRetryDecision:
    FITTING_LINE = (
        "(EngineCore pid=110) ERROR ValueError: To serve at least one request with the"
        " model's max seq len (40960), (4.38 GiB KV cache is needed, which is larger"
        " than the available KV cache memory (1.0 GiB). Based on the available memory,"
        " the estimated maximum model length is 9344."
    )

    def test_a_default_context_failure_retries_with_what_fits(self, manager):
        from backend.app.inference.models import DeployRequest

        manager._status.state = ServerState.ERROR
        manager._log_history = [self.FITTING_LINE]

        retry = manager._runtime.retry_request(
            DeployRequest(model_id="Org/M"), manager._status, manager._log_history
        )

        assert retry is not None
        assert retry.max_model_len == 9344
        assert retry.force_restart is True

    def test_an_explicit_context_choice_is_never_overridden(self, manager):
        from backend.app.inference.models import DeployRequest

        manager._status.state = ServerState.ERROR
        manager._log_history = [self.FITTING_LINE]

        retry = manager._runtime.retry_request(
            DeployRequest(model_id="Org/M", max_model_len=32768),
            manager._status,
            manager._log_history,
        )

        assert retry is None

    def test_other_failures_do_not_retry(self, manager):
        from backend.app.inference.models import DeployRequest

        manager._status.state = ServerState.ERROR
        manager._log_history = ["RuntimeError: something unrelated"]

        assert manager._runtime.retry_request(
            DeployRequest(model_id="Org/M"), manager._status, manager._log_history
        ) is None

    def test_a_successful_deployment_does_not_retry(self, manager):
        from backend.app.inference.models import DeployRequest

        manager._status.state = ServerState.READY
        manager._log_history = [self.FITTING_LINE]

        assert manager._runtime.retry_request(
            DeployRequest(model_id="Org/M"), manager._status, manager._log_history
        ) is None


class TestContainerResourceLimits:
    """The user's RAM/CPU allocation must reach Docker as real container limits."""

    class RecordingDocker:
        def __init__(self):
            self.run_kwargs = None
            outer = self

            class Images:
                def get(self, _name):
                    return object()  # image present, no pull

            class Containers:
                def get(self, _name):
                    raise RuntimeError("no such container")

                def run(self, **kwargs):
                    outer.run_kwargs = kwargs
                    raise RuntimeError("stop here: the container itself is not under test")

            class Info:
                pass

            self.images = Images()
            self.containers = Containers()

        def info(self):
            return {"MemTotal": 32 * 1024 ** 3, "NCPU": 16}

    def test_memory_and_cpu_limits_are_applied_to_the_container(self, manager, tmp_path, monkeypatch):
        from backend.app.inference import manager as manager_module
        from backend.app.inference.models import DeployRequest

        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(manager_module.shutil, "which", lambda _name: None)  # CPU path
        monkeypatch.setattr(
            manager_module.DockerPathResolver,
            "resolve_volume_source",
            staticmethod(lambda path, fallback_named_volume=None: str(path)),
        )

        docker_client = self.RecordingDocker()
        manager._client = docker_client

        request = DeployRequest(
            model_id="Org/Model", memory_limit_gb=12, cpu_limit=6
        )

        async def scenario():
            generation = manager._deploy_generation
            await manager._run_deployment(request, tmp_path, generation)

        asyncio.run(scenario())

        assert docker_client.run_kwargs is not None
        assert docker_client.run_kwargs["mem_limit"] == f"{12 * 1024}m"
        assert docker_client.run_kwargs["nano_cpus"] == 6_000_000_000
        # The KV cache is sized from the 12 GiB allocation, not the 32 GiB machine.
        assert docker_client.run_kwargs["environment"]["VLLM_CPU_KVCACHE_SPACE"] == "3"

    def test_without_limits_the_container_is_unbounded(self, manager, tmp_path, monkeypatch):
        from backend.app.inference import manager as manager_module
        from backend.app.inference.models import DeployRequest

        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(manager_module.shutil, "which", lambda _name: None)
        monkeypatch.setattr(
            manager_module.DockerPathResolver,
            "resolve_volume_source",
            staticmethod(lambda path, fallback_named_volume=None: str(path)),
        )

        docker_client = self.RecordingDocker()
        manager._client = docker_client

        async def scenario():
            generation = manager._deploy_generation
            await manager._run_deployment(
                DeployRequest(model_id="Org/Model"), tmp_path, generation
            )

        asyncio.run(scenario())

        assert docker_client.run_kwargs is not None
        assert "mem_limit" not in docker_client.run_kwargs
        assert "nano_cpus" not in docker_client.run_kwargs
        # Default KV cache: a quarter of the machine's 32 GiB.
        assert docker_client.run_kwargs["environment"]["VLLM_CPU_KVCACHE_SPACE"] == "8"


class TestPortFallback:
    """On launcher installs Kayak itself occupies the default vLLM port, so the
    manager must publish the server on the port next door instead of failing
    with "port is already allocated"."""

    class PortSqueezedDocker:
        """A docker client whose default port is taken; the next one is free."""

        def __init__(self, taken_ports):
            self.taken_ports = set(taken_ports)
            self.run_kwargs = None
            outer = self

            class Images:
                def get(self, _name):
                    return object()

            class Containers:
                def get(self, _name):
                    raise RuntimeError("no such container")

                def run(self, **kwargs):
                    port = kwargs["ports"]["8000/tcp"]
                    if port in outer.taken_ports:
                        raise APIError(
                            f"driver failed programming external connectivity: "
                            f"Bind for 0.0.0.0:{port} failed: port is already allocated"
                        )
                    outer.run_kwargs = kwargs
                    raise RuntimeError("stop here: only the binding is under test")

            self.images = Images()
            self.containers = Containers()

        def info(self):
            return {"MemTotal": 16 * 1024 ** 3, "NCPU": 8}

    @pytest.fixture
    def deploy_env(self, manager, tmp_path, monkeypatch):
        from backend.app.inference import manager as manager_module

        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)
        monkeypatch.setattr(manager_module.shutil, "which", lambda _name: None)
        monkeypatch.setattr(
            manager_module.DockerPathResolver,
            "resolve_volume_source",
            staticmethod(lambda path, fallback_named_volume=None: str(path)),
        )
        return manager_module

    def test_a_taken_port_falls_through_to_the_next_free_one(self, manager, tmp_path, deploy_env):
        from backend.app.inference.models import DeployRequest

        base_port = deploy_env.settings.VLLM_PORT
        docker_client = self.PortSqueezedDocker(taken_ports=[base_port])
        manager._client = docker_client

        asyncio.run(
            manager._run_deployment(
                DeployRequest(model_id="Org/Model"), tmp_path, manager._deploy_generation
            )
        )

        assert docker_client.run_kwargs is not None
        assert docker_client.run_kwargs["ports"]["8000/tcp"] == base_port + 1

    def test_running_out_of_ports_reports_an_error(self, manager, tmp_path, deploy_env):
        from backend.app.inference.models import DeployRequest

        base_port = deploy_env.settings.VLLM_PORT
        taken = range(base_port, base_port + manager.PORT_FALLBACK_ATTEMPTS)
        manager._client = self.PortSqueezedDocker(taken_ports=taken)

        asyncio.run(
            manager._run_deployment(
                DeployRequest(model_id="Org/Model"), tmp_path, manager._deploy_generation
            )
        )

        assert manager.get_status().state == ServerState.ERROR
        assert "No free port" in (manager.get_status().error or "")

    def test_api_base_follows_the_chosen_port(self, manager):
        from backend.app.config import settings

        assert manager._api_base_for_port(settings.VLLM_PORT) == settings.VLLM_API_BASE
        other = manager._api_base_for_port(settings.VLLM_PORT + 1)
        assert str(settings.VLLM_PORT + 1) in other
        assert other.endswith("/v1")

    def test_probe_urls_target_the_active_port(self, manager):
        urls = manager._get_endpoint_urls(9107)
        assert urls, "expected probe URLs"
        assert all(":9107/" in url for url in urls)

    def test_reads_the_published_port_from_a_discovered_container(self, manager):
        container = FakeContainer("running")
        container.attrs["NetworkSettings"] = {
            "Ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8002"}]}
        }
        assert manager._container_host_port(container) == 8002

        bare = FakeContainer("running")
        bare.attrs["NetworkSettings"] = {"Ports": {}}
        assert manager._container_host_port(bare) is None


class TestSyncAdoptsTheContainersPort:
    def test_a_rediscovered_container_reports_the_port_it_publishes(self, manager, monkeypatch):
        """After a backend restart, the running container may be published on a
        fallback port; assuming the configured default pointed status, probes,
        and chat routing at the wrong address."""
        from backend.app.inference import metal
        from backend.app.inference.models import MetalStatus

        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())

        container = FakeContainer("running")
        container.attrs["NetworkSettings"] = {
            "Ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8002"}]}
        }
        manager._client = FakeDockerClient(container)
        manager._status.state = ServerState.STOPPED

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.LOADING
        assert status.port == 8002
        assert ":8002" in status.endpoint


class TestWatchdog:
    def test_start_watchdog_is_idempotent(self, manager):
        async def scenario():
            manager.start_watchdog()
            first = manager._watchdog_task
            manager.start_watchdog()
            second = manager._watchdog_task
            first.cancel()
            return first is second

        assert asyncio.run(scenario()) is True
