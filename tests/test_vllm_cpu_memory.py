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

    def test_the_default_scales_with_the_machine_uncapped(self):
        # The recommendation is a share of the machine, not a fixed ceiling: a big
        # machine should be offered a big cache, with the user free to override.
        assert resolve_cpu_kvcache_gib(64 * GIB) == 16
        assert resolve_cpu_kvcache_gib(256 * GIB) == 64

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
        # A falsy request falls through to the derived default.
        assert resolve_cpu_kvcache_gib(64 * GIB, requested_gib=0) == 16
        assert resolve_cpu_kvcache_gib(64 * GIB, requested_gib=-3) == 1

    def test_a_big_machine_can_ask_for_a_big_cache(self):
        # The automatic default stays modest, but an explicit request may use most of
        # the machine: flexibility is the point of exposing the setting at all.
        assert resolve_cpu_kvcache_gib(128 * GIB, requested_gib=100) == 100

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


#: The actual failure observed on 2026-08-28: Qwen3-0.6B's native 40960-token context
#: needs a 4.38 GiB KV cache on a machine that only has 1 GiB to give. The oneDNN
#: warning above it names an "Exception" but was recovered from with a fallback.
CONTEXT_TOO_LARGE_LOG = [
    "(Worker pid=166) WARNING 08-28 07:38:13 [utils.py:368] Failed to create oneDNN"
    " linear, fallback to torch linear. Exception: could not create a primitive"
    " descriptor for the matmul primitive.",
    "(Worker pid=166) INFO 08-28 07:39:28 [cpu_worker.py:255] Explicitly set (1.0/7.75)"
    " GiB for KV cache on node 0.",
    "(EngineCore pid=110) ERROR 08-28 07:39:28 [core.py:1346] ValueError: To serve at"
    " least one request with the model's max seq len (40960), (4.38 GiB KV cache is"
    " needed, which is larger than the available KV cache memory (1.0 GiB). Based on"
    " the available memory, the estimated maximum model length is 9344. Try increasing"
    " `gpu_memory_utilization` ... or decreasing `max_model_len` when initializing the"
    " engine.",
    "(APIServer pid=1) RuntimeError: Engine core initialization failed. See root cause above.",
]


class TestFailureReasonSkipsRecoveredWarnings:
    def test_a_warning_with_a_fallback_is_not_the_crash_reason(self):
        # The oneDNN warning names an "Exception" but vLLM recovered from it; quoting
        # it as the failure sent the user chasing a matmul primitive that was fine.
        reason = extract_failure_reason(CONTEXT_TOO_LARGE_LOG)

        assert "oneDNN" not in (reason or "")
        assert reason is not None
        assert reason.startswith("ValueError: To serve at least one request")


class TestExtractFittingContext:
    def test_reads_the_length_vllm_says_would_fit(self):
        from backend.app.vllm.manager import extract_fitting_context

        assert extract_fitting_context(CONTEXT_TOO_LARGE_LOG) == 9344

    def test_reports_nothing_for_other_failures(self):
        from backend.app.vllm.manager import extract_fitting_context

        assert extract_fitting_context(QWEN_FAILURE_LOG) is None
        assert extract_fitting_context([]) is None

    def test_a_context_too_small_to_be_useful_is_not_offered(self):
        from backend.app.vllm.manager import extract_fitting_context

        assert extract_fitting_context(
            ["... the estimated maximum model length is 512. Try ..."]
        ) is None
