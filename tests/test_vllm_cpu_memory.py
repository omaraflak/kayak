"""Tests for CPU deployment sizing and failure reporting.

Both are driven by a real failure: launching Qwen3-0.6B (1.4 GiB of weights) on an
8 GiB Docker Desktop VM died because the KV cache request was hardcoded to 12 GiB. The
model size was irrelevant -- nothing could have started -- and the error surfaced to the
user quoted trailing interpreter warnings instead of the sentence explaining why.
"""

import pytest

from backend.app.vllm.manager import extract_failure_reason, resolve_cpu_kvcache_gib

GIB = 1024 ** 3

#: Trimmed from the container output of the failure described above, in order.
QWEN_FAILURE_LOG = [
    "(APIServer pid=1) INFO 08-14 08:19:35 [model.py:1883] Using max model len 4096",
    "(Worker pid=114) INFO 08-14 08:30:20 [model_runner.py:329] Model loading took 2.33 GiB",
    "(Worker pid=114) ERROR 08-14 08:34:03 [multiproc_executor.py:1018] WorkerProc hit an exception.",
    "(Worker pid=114) ERROR 08-14 08:34:03 [multiproc_executor.py:1018] Traceback (most recent call last):",
    "(Worker pid=114) ERROR 08-14 08:34:03 [multiproc_executor.py:1018] ValueError: Available memory on"
    " node 0 (2.5/7.77 GiB) on kv cache allocation is less than requested memory for kv (12.0 GiB)."
    " Decrease --kv-cache-memory-bytes, VLLM_CPU_KVCACHE_SPACE, or reduce CPU memory used by other"
    " processes.",
    "(EngineCore pid=82) ERROR 08-14 08:34:03 [core.py:1349] EngineCore failed to start.",
    "(APIServer pid=1) RuntimeError: Engine core initialization failed. See root cause above.",
    "/opt/uv/python/cpython-3.12.13-linux-x86_64-gnu/lib/python3.12/multiprocessing/resource_tracker.py:279:"
    " UserWarning: resource_tracker: There appear to be 1 leaked shared_memory objects to clean up at shutdown",
    "warnings.warn('resource_tracker: There appear to be %d '",
]


class TestResolveCpuKvcacheGib:
    def test_an_eight_gigabyte_machine_gets_a_cache_that_fits(self):
        # The exact machine from the report: 7.77 GiB total, of which only 2.5 GiB was
        # still free once a small model had loaded. The choice has to fit inside that,
        # not merely inside the total.
        chosen = resolve_cpu_kvcache_gib(int(7.77 * GIB))

        assert chosen >= 1
        assert chosen <= 2, "would not fit alongside the model and the vLLM runtime"

    def test_a_large_machine_gets_more_but_stays_bounded(self):
        assert resolve_cpu_kvcache_gib(64 * GIB) == 8

    def test_a_tiny_machine_still_gets_a_usable_cache(self):
        # Below the headroom the arithmetic goes negative; it must not return 0 or less,
        # which vLLM rejects outright.
        assert resolve_cpu_kvcache_gib(2 * GIB) == 1

    def test_an_explicit_choice_is_honoured_when_it_fits(self):
        assert resolve_cpu_kvcache_gib(32 * GIB, requested_gib=6) == 6

    def test_an_explicit_choice_is_clamped_to_what_the_machine_has(self):
        # Asking for 6 GiB on an 8 GiB host cannot work, and vLLM only says so minutes
        # in, once the weights have already downloaded.
        assert resolve_cpu_kvcache_gib(int(7.77 * GIB), requested_gib=6) <= 2

    def test_an_absurd_request_cannot_break_a_launch(self):
        assert resolve_cpu_kvcache_gib(int(7.77 * GIB), requested_gib=999) <= 2

    def test_an_explicit_choice_is_still_floored(self):
        assert resolve_cpu_kvcache_gib(64 * GIB, requested_gib=0) == 8
        assert resolve_cpu_kvcache_gib(64 * GIB, requested_gib=-3) == 1

    @pytest.mark.parametrize("unknown", [None, 0])
    def test_an_unknown_machine_gets_a_conservative_default(self, unknown):
        chosen = resolve_cpu_kvcache_gib(unknown)

        assert 1 <= chosen <= 4

    def test_scales_with_the_memory_available(self):
        small = resolve_cpu_kvcache_gib(8 * GIB)
        large = resolve_cpu_kvcache_gib(32 * GIB)
        assert large > small


class TestExtractFailureReason:
    def test_finds_the_sentence_that_explains_the_crash(self):
        reason = extract_failure_reason(QWEN_FAILURE_LOG)

        assert reason is not None
        assert reason.startswith("ValueError: Available memory on node 0")
        assert "VLLM_CPU_KVCACHE_SPACE" in reason

    def test_ignores_the_shutdown_noise_that_follows(self):
        # The last two lines of the real log are a resource_tracker warning; quoting
        # the tail is what made the original message useless.
        reason = extract_failure_reason(QWEN_FAILURE_LOG)

        assert "resource_tracker" not in reason
        assert "leaked shared_memory" not in reason

    def test_prefers_the_root_cause_over_the_wrapper(self):
        # "Engine core initialization failed" is true but says nothing actionable.
        reason = extract_failure_reason(QWEN_FAILURE_LOG)

        assert "Engine core initialization failed" not in reason

    def test_reports_nothing_when_the_log_holds_no_error(self):
        assert extract_failure_reason(["INFO starting", "INFO ready"]) is None

    def test_handles_an_empty_log(self):
        assert extract_failure_reason([]) is None

    def test_skips_a_bare_exception_name_with_no_message(self):
        assert extract_failure_reason(["ValueError:", "RuntimeError: the real problem"]) == (
            "RuntimeError: the real problem"
        )
