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
