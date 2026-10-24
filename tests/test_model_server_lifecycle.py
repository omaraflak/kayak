"""Tests for the parts of a model's life that used to go wrong without anyone touching it.

A model that came back after Stop, a start that waited half an hour for a launcher that
had quit, a ready model dropping to "Starting" after one slow answer, a failed download
reported as a success: each of these happened between clicks, in the reconcile loop or
a monitor, so they are tested here by driving those directly.
"""

import asyncio
import json
import os
import stat
from typing import Any, Dict, List, Optional

import pytest

from backend.app.inference import failures, manager as manager_module, metal
from backend.app.inference.manager import (
    DownloadProgress,
    ServerManager,
    StatusBroadcaster,
)
from backend.app.inference.models import (
    DeployRequest,
    DeploymentProgress,
    MetalStatus,
    ServerState,
)
from backend.app.inference.vllm_runtime import VLLMRuntime


class FakeContainer:
    def __init__(self, status: str, exit_code: int = 0, model: str = "Org/Model"):
        self.id = "c" * 64
        self.status = status
        self.removed = False
        self.stopped = False
        self.attrs: Dict[str, Any] = {
            "State": {"ExitCode": exit_code, "OOMKilled": False},
            "Config": {"Cmd": ["--model", model]},
        }

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: Optional[FakeContainer]):
        self.container = container

    def get(self, _name: str) -> FakeContainer:
        if self.container is None:
            raise RuntimeError("No such container")
        return self.container


class FakeDocker:
    def __init__(self, container: Optional[FakeContainer] = None, alive: bool = True):
        self.containers = FakeContainers(container)
        self.alive = alive

    def ping(self) -> bool:
        if not self.alive:
            raise ConnectionError("Docker is not running")
        return True


class Unreachable:
    """An httpx.AsyncClient for which nothing is listening."""

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("nothing listening")


class Serving:
    """An httpx.AsyncClient whose server answers for one model."""

    model = "Org/Model"

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
                return {"data": [{"id": Serving.model}]}

        return Response()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(metal.settings, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def manager(data_dir, monkeypatch) -> ServerManager:
    monkeypatch.setattr(manager_module.httpx, "AsyncClient", Unreachable)
    instance = ServerManager.__new__(ServerManager)
    instance._runtime = VLLMRuntime()
    instance._broadcaster = StatusBroadcaster()
    instance._init_state()
    instance._docker_available = True
    instance._client = FakeDocker()
    return instance


@pytest.fixture
def instant_sleep(monkeypatch):
    real_sleep = asyncio.sleep

    async def quick(_delay):
        await real_sleep(0)

    monkeypatch.setattr(manager_module.asyncio, "sleep", quick)


def launcher_says(monkeypatch, **fields) -> None:
    monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=True, **fields))


class TestAStoppedGpuModelStaysStopped:
    """The launcher lags a stop by a couple of seconds, and its status file says
    "ready" until it catches up. Believing it brought the model back."""

    def test_a_stale_ready_after_stop_is_ignored(self, manager, monkeypatch):
        launcher_says(monkeypatch, state="ready", model="mlx-community/M", port=8001)
        metal.write_desired(running=False)
        manager._status.state = ServerState.STOPPED
        manager._status.model_id = "mlx-community/M"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.STOPPED

    def test_a_stale_starting_after_stop_is_not_adopted(self, manager, monkeypatch):
        launcher_says(monkeypatch, state="starting", model="mlx-community/M")
        metal.write_desired(running=False)

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.IDLE
        assert manager._metal_monitor_task is None

    def test_a_status_answering_another_request_is_ignored(self, manager, monkeypatch):
        launcher_says(
            monkeypatch,
            state="ready",
            model="mlx-community/M",
            request="old",
            acknowledges_requests=True,
        )
        metal.write_desired(running=True, model="mlx-community/M", request="new")

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state != ServerState.READY

    def test_an_adopted_start_keeps_its_request_token(self, manager, monkeypatch):
        launcher_says(
            monkeypatch,
            state="starting",
            model="mlx-community/M",
            request="tok",
            acknowledges_requests=True,
        )
        metal.write_desired(running=True, model="mlx-community/M", request="tok")
        seen: List[Optional[str]] = []

        async def record(model_id, request_token=None, generation=None, timeout_seconds=0):
            seen.append(request_token)

        manager._run_metal_deployment = record

        async def scenario():
            status = await manager.check_and_sync_status()
            await asyncio.sleep(0)
            return status

        status = asyncio.run(scenario())

        assert status.state == ServerState.LOADING
        assert seen == ["tok"]


class TestDesiredStateIsReset:
    def test_shutdown_tells_the_launcher_to_stop(self, manager):
        metal.write_desired(running=True, model="mlx-community/M", request="t")

        asyncio.run(manager.shutdown())

        # Left "running", the launcher started the last GPU model by itself at the
        # next launch.
        assert metal.read_desired().running is False

    def test_a_gpu_failure_tells_the_launcher_to_stop(self, manager, monkeypatch, instant_sleep):
        launcher_says(monkeypatch, state="error", model="mlx-community/M", installed=True,
                      error="This model needs more memory than this Mac has free.")
        metal.write_desired(running=True, model="mlx-community/M")
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"

        asyncio.run(manager._run_metal_deployment("mlx-community/M", timeout_seconds=30))

        assert manager._status.state == ServerState.ERROR
        assert metal.read_desired().running is False

    def test_a_gpu_start_that_runs_out_of_time_is_stopped(self, manager, monkeypatch, instant_sleep):
        launcher_says(monkeypatch, state="starting", model="mlx-community/M")
        metal.write_desired(running=True, model="mlx-community/M")
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"

        asyncio.run(manager._run_metal_deployment("mlx-community/M", timeout_seconds=0))

        assert manager._status.state == ServerState.ERROR
        assert manager._status.error == failures.TIMED_OUT
        assert metal.read_desired().running is False


class TestTheLauncherGoingAway:
    def test_a_silent_launcher_ends_the_start_within_seconds(self, manager, monkeypatch, instant_sleep):
        """A dead launcher's status reads "unsupported" forever; the start used to
        wait out its whole thirty minutes."""
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=False))
        clock = {"now": 1000.0}

        def tick():
            clock["now"] += 5
            return clock["now"]

        monkeypatch.setattr(manager_module.time, "monotonic", tick)
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"

        asyncio.run(manager._run_metal_deployment("mlx-community/M", timeout_seconds=10_000))

        assert manager._status.state == ServerState.ERROR
        assert manager._status.error == failures.LAUNCHER_GONE
        assert manager._status.message == "M couldn't start."


class TestStartsThatCannotWriteTheirFiles:
    def test_a_gpu_start_that_cannot_write_its_request_fails_cleanly(self, manager, monkeypatch):
        launcher_says(monkeypatch, state="stopped")

        def full_disk(**_kwargs):
            if _kwargs.get("running"):
                raise OSError(28, "No space left on device")

        monkeypatch.setattr(metal, "write_desired", full_disk)

        status = asyncio.run(manager.deploy_model(DeployRequest(model_id="mlx-community/M")))

        # It used to stay "Starting" for good, every Start disabled behind it.
        assert status.state == ServerState.ERROR
        assert status.model_id == "mlx-community/M"
        assert status.error == failures.SAVE_FAILED
        assert manager._metal_monitor_task is None

    def test_a_cpu_start_that_cannot_create_its_cache_fails_cleanly(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())

        def unwritable(self, *args, **kwargs):
            raise PermissionError("read-only")

        monkeypatch.setattr(manager_module.Path, "mkdir", unwritable)

        status = asyncio.run(manager.deploy_model(DeployRequest(model_id="Org/Model")))

        assert status.state == ServerState.ERROR
        assert status.error == failures.SAVE_FAILED
        assert manager._monitor_task is None

    def test_stop_still_stops_when_the_launcher_file_cannot_be_written(self, manager, monkeypatch):
        launcher_says(monkeypatch, state="ready", model="mlx-community/M")

        def full_disk(**_kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(metal, "write_desired", full_disk)
        manager._status.state = ServerState.READY
        manager._status.model_id = "mlx-community/M"

        asyncio.run(manager.stop_server())

        assert manager._status.state == ServerState.STOPPED

    def test_docker_not_running_names_the_model(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())

        async def no_docker():
            return False

        monkeypatch.setattr(manager, "_ensure_docker", no_docker)

        status = asyncio.run(manager.deploy_model(DeployRequest(model_id="Org/Model")))

        assert status.state == ServerState.ERROR
        assert status.model_id == "Org/Model"
        assert status.message == "Docker Desktop isn't running."
        assert "Open Docker Desktop" in (status.error or "")


class TestNothingComesBackOnItsOwn:
    def test_a_running_container_does_not_revive_an_error(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.ERROR
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR

    def test_an_answering_server_does_not_revive_a_stop(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        monkeypatch.setattr(manager_module.httpx, "AsyncClient", Serving)
        manager._status.state = ServerState.STOPPED
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.STOPPED


class TestAReadyModelStaysReady:
    def test_one_slow_answer_does_not_drop_it_to_starting(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.READY

    def test_two_minutes_of_unanswered_checks_report_it(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"
        checks = manager_module.READY_FAILURES_BEFORE_ERROR

        async def scenario():
            states = []
            for _ in range(checks):
                states.append((await manager.check_and_sync_status()).state)
            return states

        states = asyncio.run(scenario())

        # Checks run every WATCHDOG_INTERVAL_SECONDS: a busy Mac gets two minutes.
        assert checks * manager_module.WATCHDOG_INTERVAL_SECONDS >= 120
        assert states == [ServerState.READY] * (checks - 1) + [ServerState.ERROR]
        assert manager._status.crashed is True
        assert manager._status.message == "Model stopped unexpectedly."

    def test_a_model_declared_stopped_that_answers_again_is_ready(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        async def scenario():
            for _ in range(manager_module.READY_FAILURES_BEFORE_ERROR):
                await manager.check_and_sync_status()
            monkeypatch.setattr(manager_module.httpx, "AsyncClient", Serving)
            return await manager.check_and_sync_status()

        status = asyncio.run(scenario())

        assert status.state == ServerState.READY
        assert status.crashed is False

    def test_an_answer_in_between_resets_the_count(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        async def scenario():
            await manager.check_and_sync_status()
            await manager.check_and_sync_status()
            monkeypatch.setattr(manager_module.httpx, "AsyncClient", Serving)
            await manager.check_and_sync_status()
            monkeypatch.setattr(manager_module.httpx, "AsyncClient", Unreachable)
            return (await manager.check_and_sync_status()).state

        assert asyncio.run(scenario()) == ServerState.READY

    def test_a_crash_after_ready_is_not_a_failed_start(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("exited", exit_code=1))
        manager._status.state = ServerState.READY
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert status.crashed is True
        assert status.message == "Model stopped unexpectedly."


class TestAStartNobodyIsWatching:
    def test_with_nothing_left_to_finish_it_it_fails(self, manager, monkeypatch):
        """No monitor, no container, nothing answering, nothing asked of the
        launcher: it used to stay "Starting" forever."""
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(None)
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert status.error == failures.interrupted("Org/Model")

    def test_docker_gone_is_said_as_that(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(None, alive=False)
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        status = asyncio.run(manager.check_and_sync_status())

        assert status.state == ServerState.ERROR
        assert status.error == failures.DOCKER_STOPPED

    def test_a_running_container_is_watched_again_with_a_deadline(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus())
        manager._client = FakeDocker(FakeContainer("running"))
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"
        watched: List[str] = []

        async def record(model_id, port, generation):
            watched.append(model_id)

        manager._watch_adopted_start = record

        async def scenario():
            status = await manager.check_and_sync_status()
            await asyncio.sleep(0)
            return status

        status = asyncio.run(scenario())

        assert status.state == ServerState.LOADING
        assert watched == ["Org/Model"]

    def test_a_gpu_start_wanted_of_the_launcher_is_watched_again(self, manager, monkeypatch):
        monkeypatch.setattr(metal, "read_status", lambda: MetalStatus(supported=False))
        metal.write_desired(running=True, model="mlx-community/M", request="tok")
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"
        seen: List[Optional[str]] = []

        async def record(model_id, request_token=None, generation=None, timeout_seconds=0):
            seen.append(request_token)

        manager._run_metal_deployment = record

        async def scenario():
            status = await manager.check_and_sync_status()
            await asyncio.sleep(0)
            return status

        status = asyncio.run(scenario())

        assert status.state == ServerState.LOADING
        assert seen == ["tok"]


class TestTimeouts:
    def test_a_silent_start_is_given_up_and_its_container_removed(self, manager, monkeypatch, instant_sleep):
        container = FakeContainer("running")
        manager._client = FakeDocker(container)
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"
        manager._last_activity -= manager_module.START_IDLE_TIMEOUT_SECONDS + 1

        asyncio.run(manager._poll_health_endpoint("Org/Model", timeout_seconds=10_000))

        assert manager._status.state == ServerState.ERROR
        assert manager._status.error == failures.TIMED_OUT
        # A timeout used to leave the container running, holding its memory.
        assert container.removed is True

    def test_output_pushes_the_deadline_back(self, manager):
        manager._last_activity -= 10_000

        manager._add_log("Downloading shards...")

        assert not manager._start_overdue(
            manager_module.time.monotonic(), manager_module.START_TIMEOUT_CAP_SECONDS
        )


class TestEngineDownload:
    class Image:
        def __init__(self, image_id: str):
            self.id = image_id
            self.labels = {}

    class Api:
        def __init__(self, lines):
            self.lines = lines

        def pull(self, *_args, **_kwargs):
            return iter(self.lines)

    class Images:
        def __init__(self, images):
            self.images = list(images)
            self.removed: List[str] = []

        def get(self, _name):
            if not self.images:
                raise RuntimeError("No such image")
            return self.images.pop(0) if len(self.images) > 1 else self.images[0]

        def remove(self, image_id):
            self.removed.append(image_id)

    def _client(self, lines, images):
        client = FakeDocker()
        client.api = self.Api(lines)
        client.images = self.Images(images)
        return client

    def test_an_error_line_fails_the_download(self, manager):
        manager._client = self._client(
            [{"status": "Pulling fs layer", "id": "a"}, {"error": "toomanyrequests: slow down",
             "errorDetail": {"message": "toomanyrequests: slow down"}}],
            [],
        )
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="toomanyrequests"):
                manager._stream_pull("img", loop, manager._deploy_generation)
        finally:
            loop.close()

    def test_a_failed_download_is_reported_not_celebrated(self, manager, monkeypatch, data_dir):
        monkeypatch.setattr(manager_module.docker_host, "host_path", lambda path: str(path))
        manager._client = self._client(
            [{"error": "toomanyrequests: slow down"}],
            [],
        )
        manager._client.info = lambda: {"MemTotal": 16 * 1024 ** 3, "NCPU": 8}
        manager._status.model_id = "Org/Model"

        asyncio.run(
            manager._run_deployment(DeployRequest(model_id="Org/Model"), data_dir, manager._deploy_generation)
        )

        assert manager._status.state == ServerState.ERROR
        assert manager._status.error == failures.ENGINE_RATE_LIMITED
        assert not any("Downloaded" in line for line in manager._log_history)

    def test_progress_becomes_a_percentage(self, manager):
        manager._client = self._client(
            [
                {"status": "Downloading", "id": "a", "progressDetail": {"current": 50, "total": 100}},
                {"status": "Downloading", "id": "b", "progressDetail": {"current": 0, "total": 100}},
                {"status": "Download complete", "id": "a"},
            ],
            [],
        )
        manager._status.state = ServerState.PULLING_IMAGE
        loop = asyncio.new_event_loop()
        try:
            manager._stream_pull("img", loop, manager._deploy_generation)
            loop.run_until_complete(asyncio.sleep(0))
        finally:
            loop.close()

        assert manager._status.message == f"{manager_module.ENGINE_DOWNLOAD_MESSAGE} 50%"

    def test_an_unchanged_image_is_not_deleted(self, manager, monkeypatch, data_dir):
        """An update that brought nothing new used to delete the only working copy."""
        monkeypatch.setattr(manager, "_is_from_another_release", lambda image: True)
        same = self.Image("sha256:same")
        manager._client = self._client([{"status": "Status: Image is up to date"}], [same])
        manager._client.info = lambda: {"MemTotal": 16 * 1024 ** 3, "NCPU": 8}

        def stop_here(**_kwargs):
            raise RuntimeError("stop here")

        manager._client.containers.run = stop_here

        asyncio.run(
            manager._run_deployment(DeployRequest(model_id="Org/Model"), data_dir, manager._deploy_generation)
        )

        assert manager._client.images.removed == []


class TestAutomaticRetries:
    def test_an_unknown_tool_parser_is_retried_with_the_general_one(self):
        runtime = VLLMRuntime()
        status = DeploymentProgress(state=ServerState.ERROR)
        logs = ["KeyError: 'invalid tool call parser: kimi_k3 (chose from { hermes })'"]

        retry = runtime.retry_request(DeployRequest(model_id="moonshot/Kimi-K3"), status, logs)

        assert retry is not None
        assert retry.tool_parser == "hermes"

        # Once is enough: a second failure is reported.
        assert runtime.retry_request(retry, status, logs) is None

    def test_the_retried_parser_reaches_the_server(self):
        from backend.app.inference.runtimes import SpecContext

        spec = asyncio.run(
            VLLMRuntime().container_spec(
                DeployRequest(model_id="moonshot/Kimi-K3", tool_parser="hermes"),
                SpecContext(docker_memory_bytes=None, hf_token=None),
            )
        )

        assert spec.command[spec.command.index("--tool-call-parser") + 1] == "hermes"

    def test_a_fixable_failure_is_retried_instead_of_shown(self, manager):
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "moonshot/Kimi-K3"
        manager._active_request = DeployRequest(model_id="moonshot/Kimi-K3")
        manager._log_history = ["KeyError: 'invalid tool call parser: kimi_k3'"]
        scheduled: List[DeployRequest] = []
        manager._schedule_retry = scheduled.append

        manager._report_container_exit(1, False)

        assert manager._status.state == ServerState.LOADING
        assert [retry.tool_parser for retry in scheduled] == ["hermes"]

    def test_a_retry_after_a_stop_does_not_run(self, manager):
        started: List[str] = []

        async def record(request):
            started.append(request.model_id)

        manager._deploy_locked = record

        async def scenario():
            generation = manager._deploy_generation
            manager._deploy_generation += 1  # a stop happened in between
            await manager._redeploy_retry(DeployRequest(model_id="Org/M"), generation)

        asyncio.run(scenario())

        assert started == []


class TestDownloadProgress:
    def test_parallel_files_add_up(self):
        progress = DownloadProgress()

        assert progress.update("model-1.safetensors:  50%|█████     | 2.00G/4.00G [00:30<00:30]") == 50
        assert progress.update("model-2.safetensors:  10%|█         | 400M/4.00G [00:03<00:30]") == 50
        assert progress.update("model-2.safetensors: 100%|██████████| 4.00G/4.00G [00:30<00:00]") == 75

    def test_the_file_count_bar_is_not_bytes(self):
        assert DownloadProgress().update("Fetching 4 files:  25%|██▌       | 1/4 [00:10<00:30]") is None

    def test_other_lines_are_ignored(self):
        assert DownloadProgress().update("INFO Loading weights took 3.1 seconds") is None

    def test_the_status_shows_the_download(self, manager):
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        manager._add_log("model.safetensors:  45%|████▌     | 450M/1.00G [00:30<00:37, 73.4MB/s]")

        assert manager._status.message == "Downloading Model… 45%"

    def test_a_redrawn_bar_is_logged_once_per_percent(self, manager):
        for _ in range(5):
            manager._add_log("model.safetensors:  45%|████▌     | 450M/1.00G [00:30<00:37]")

        assert len(manager._log_history) == 1

    def test_loading_the_weights_takes_over_from_the_download(self, manager):
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "Org/Model"

        manager._add_log("(Worker pid=1) INFO 10-23 08:00:00 [loader.py:1] Loading weights took 3.1 seconds")
        manager._add_log("tokenizer.json:  50%|█████     | 1.00M/2.00M [00:00<00:00]")

        assert manager._status.message == "Loading Model…"


class TestTheEventQueue:
    def test_a_burst_of_logs_cannot_push_out_the_latest_status(self):
        async def scenario():
            broadcaster = StatusBroadcaster()
            queue = broadcaster.subscribe([])
            broadcaster.publish({"modality": "text", "type": "status", "data": {"state": "ready"}})
            for index in range(500):
                broadcaster.publish({"modality": "text", "type": "log", "line": str(index)})
            drained = []
            while not queue.empty():
                drained.append(queue.get_nowait())
            return drained

        drained = asyncio.run(scenario())

        assert {"modality": "text", "type": "status", "data": {"state": "ready"}} in drained
        assert drained[-1] == {"modality": "text", "type": "log", "line": "499"}

    def test_a_newer_status_replaces_a_queued_one(self):
        async def scenario():
            broadcaster = StatusBroadcaster()
            queue = broadcaster.subscribe([])
            broadcaster.publish({"modality": "text", "type": "status", "data": {"n": 1}})
            broadcaster.publish({"modality": "speech", "type": "status", "data": {"n": 1}})
            broadcaster.publish({"modality": "text", "type": "status", "data": {"n": 2}})
            drained = []
            while not queue.empty():
                drained.append(queue.get_nowait())
            return drained

        drained = asyncio.run(scenario())

        texts = [event for event in drained if event["modality"] == "text"]
        assert texts == [{"modality": "text", "type": "status", "data": {"n": 2}}]
        assert len(drained) == 2


class TestTheLauncherContract:
    def test_the_token_reaches_the_launcher_with_a_start_only(self, data_dir):
        metal.write_desired(running=True, model="mlx-community/M", request="t", hf_token="hf_x")
        payload = json.loads(metal.desired_path().read_text(encoding="utf-8"))
        assert payload["metal"]["hf_token"] == "hf_x"

        metal.write_desired(running=False, hf_token="hf_x")
        payload = json.loads(metal.desired_path().read_text(encoding="utf-8"))
        assert "hf_token" not in payload["metal"]

    def test_the_request_file_is_private(self, data_dir):
        metal.write_desired(running=True, model="mlx-community/M", hf_token="hf_x")

        mode = stat.S_IMODE(os.stat(metal.desired_path()).st_mode)

        assert mode == 0o600

    def test_a_gpu_start_sends_the_token(self, manager, monkeypatch):
        launcher_says(monkeypatch, state="stopped")
        monkeypatch.setattr(manager_module.settings, "HUGGINGFACE_API_KEY", "hf_secret")

        async def scenario():
            await manager.deploy_model(DeployRequest(model_id="mlx-community/M"))
            manager._metal_monitor_task.cancel()

        asyncio.run(scenario())

        payload = json.loads(metal.desired_path().read_text(encoding="utf-8"))
        assert payload["metal"]["hf_token"] == "hf_secret"

    @pytest.mark.parametrize(
        "content",
        ["", "{", "[]", '{"metal": 3}', '{"metal": {"running": "yes"}}'],
    )
    def test_an_unreadable_request_file_means_nothing_is_wanted(self, data_dir, content):
        metal.control_dir().mkdir(parents=True, exist_ok=True)
        metal.desired_path().write_text(content, encoding="utf-8")

        assert metal.read_desired().running is False

    def test_new_status_fields_are_read_and_odd_ones_tolerated(self, data_dir):
        metal.control_dir().mkdir(parents=True, exist_ok=True)
        metal.status_path().write_text(
            json.dumps(
                {
                    "metal": {
                        "supported": True,
                        "state": "error",
                        "model": "mlx-community/M",
                        "port": "not a port",
                        "error": "This model needs more memory than this Mac has free.",
                        "error_detail": "Traceback ...",
                        "request": None,
                    }
                }
            ),
            encoding="utf-8",
        )

        status = metal.read_status()

        assert status.error_detail == "Traceback ..."
        assert status.port == 0
        assert status.acknowledges_requests is True


class TestTheFinalLauncherContract:
    def test_a_new_launchers_error_is_shown_as_it_is(self, manager, monkeypatch, instant_sleep):
        """A launcher that sends error_detail writes `error` for a person, even when
        it happens to contain something the heuristic would call raw."""
        sentence = "Kayak couldn't download mlx-community/M [the connection dropped]. Try again."
        launcher_says(
            monkeypatch,
            state="error",
            model="mlx-community/M",
            installed=True,
            error=sentence,
            error_detail="ConnectionResetError: [Errno 54]",
            separates_error_detail=True,
        )
        manager._status.state = ServerState.LOADING
        manager._status.model_id = "mlx-community/M"

        asyncio.run(manager._run_metal_deployment("mlx-community/M", timeout_seconds=30))

        assert manager._status.error == sentence
        assert any("Errno 54" in line for line in manager._log_history)

    def test_the_launchers_port_is_used_only_once_ready(self, manager, monkeypatch, instant_sleep):
        # Before "ready" the port describes the server being replaced, or nothing.
        launcher_says(monkeypatch, state="ready", model="mlx-community/Old", port=8123)

        async def scenario():
            status = await manager.deploy_model(DeployRequest(model_id="mlx-community/New"))
            port_while_starting = status.port
            manager._metal_monitor_task.cancel()
            return port_while_starting

        port_while_starting = asyncio.run(scenario())

        assert port_while_starting == manager._runtime.default_port

        launcher_says(monkeypatch, state="ready", model="mlx-community/New", port=8142)
        manager._status.state = ServerState.LOADING
        asyncio.run(manager._run_metal_deployment("mlx-community/New", timeout_seconds=30))

        assert manager._status.state == ServerState.READY
        assert manager._status.port == 8142
        assert ":8142" in manager._status.endpoint

    def test_the_separation_is_read_from_the_status_file(self, data_dir):
        metal.control_dir().mkdir(parents=True, exist_ok=True)
        metal.status_path().write_text(
            json.dumps({"metal": {"supported": True, "state": "stopped", "error_detail": None}}),
            encoding="utf-8",
        )
        assert metal.read_status().separates_error_detail is True

        metal.status_path().write_text(
            json.dumps({"metal": {"supported": True, "state": "stopped"}}), encoding="utf-8"
        )
        assert metal.read_status().separates_error_detail is False


class TestDownloadProgressOnDisk:
    def test_partial_files_count_and_links_count_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)
        repo = tmp_path / "huggingface_cache" / "hub" / "models--Org--Model"
        (repo / "blobs").mkdir(parents=True)
        (repo / "snapshots" / "abc").mkdir(parents=True)
        blob = repo / "blobs" / "123.incomplete"
        blob.write_bytes(b"x" * 1000)
        (repo / "snapshots" / "abc" / "model.safetensors").symlink_to(blob)

        assert manager_module._downloaded_bytes("Org/Model") >= 1000
        assert manager_module._downloaded_bytes("Org/Model") < 2000

    def test_nothing_downloaded_yet_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(manager_module.settings, "DATA_DIR", tmp_path)

        assert manager_module._downloaded_bytes("Org/Model") == 0
        assert manager_module._downloaded_bytes(None) == 0

