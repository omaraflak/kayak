from backend.app.inference.hardware import parse_nvidia_smi_output


class TestParseNvidiaSmiOutput:
    def test_reads_name_and_memory_for_each_device(self):
        raw = "NVIDIA GeForce RTX 4090, 24564\nNVIDIA A100-SXM4-80GB, 81920\n"

        devices = parse_nvidia_smi_output(raw)

        assert [device.name for device in devices] == [
            "NVIDIA GeForce RTX 4090",
            "NVIDIA A100-SXM4-80GB",
        ]
        assert [device.total_memory_mb for device in devices] == [24564, 81920]

    def test_no_output_means_no_accelerators(self):
        assert parse_nvidia_smi_output("") == []
        assert parse_nvidia_smi_output("\n  \n") == []

    def test_a_malformed_row_does_not_discard_readable_ones(self):
        # A partially readable GPU list beats reporting a CPU-only machine.
        raw = "NVIDIA L4, 23034\ngarbage-without-a-comma\nNVIDIA T4, not-a-number\n"

        devices = parse_nvidia_smi_output(raw)

        assert len(devices) == 1
        assert devices[0].name == "NVIDIA L4"

    def test_accepts_fractional_memory_values(self):
        devices = parse_nvidia_smi_output("NVIDIA L4, 23034.0")

        assert devices[0].total_memory_mb == 23034


class TestGpuDetectionAsksTheDaemon:
    """Which question decides whether a model gets a GPU.

    Kayak normally runs inside a container of its own, which has no NVIDIA tools
    and no device access. Asking what *this process* can see therefore answered
    "no GPU" on every machine that ships this way, and a workstation with a card
    quietly served models on its CPU.
    """

    def test_the_nvidia_runtime_means_a_container_can_have_a_gpu(self):
        from backend.app.inference.hardware import daemon_offers_gpu

        assert daemon_offers_gpu({"Runtimes": {"runc": {}, "nvidia": {}}}) is True

    def test_a_daemon_without_it_cannot(self):
        from backend.app.inference.hardware import daemon_offers_gpu

        assert daemon_offers_gpu({"Runtimes": {"runc": {}, "io.containerd.runc.v2": {}}}) is False

    def test_an_unreachable_daemon_is_not_a_gpu(self):
        from backend.app.inference.hardware import daemon_offers_gpu

        assert daemon_offers_gpu(None) is False
        assert daemon_offers_gpu({}) is False

    def test_the_accelerator_is_reported_even_when_the_cards_cannot_be_counted(self):
        import asyncio
        from backend.app.inference.hardware import probe_host_capability

        capability = asyncio.run(
            probe_host_capability(
                docker_available=True,
                image_present=True,
                docker_info={"Runtimes": {"nvidia": {}}},
            )
        )

        # No inventory is readable from inside a container, and that is normal
        # rather than a contradiction: the page must still say "cuda", because
        # that is the image the deployment will pick.
        assert capability.accelerator == "cuda"
        assert capability.total_vram_mb == 0

    def test_no_daemon_gpu_and_no_local_tools_is_cpu(self):
        import asyncio
        from backend.app.inference.hardware import probe_host_capability

        capability = asyncio.run(
            probe_host_capability(docker_available=True, docker_info={"Runtimes": {"runc": {}}})
        )

        assert capability.accelerator == "cpu"
