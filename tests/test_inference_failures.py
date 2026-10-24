"""Tests for turning a model server's dying words into one sentence a person can use.

Each row of the table gets a test built from the line the real failure prints, because
the table is only as good as its markers: a marker that never matches real output is a
failure explained as "couldn't start", and one that matches too much is every failure
explained the same wrong way.
"""

import pytest

from backend.app.inference import failures
from backend.app.inference.failures import classify, explain


#: vLLM's start-up banner, printed at INFO on every start. It names the tokenizer and
#: trust_remote_code, so a classifier that read it would blame both for everything.
VLLM_BANNER = (
    "(APIServer pid=1) INFO 10-23 08:00:01 [core.py:74] Initializing a V1 LLM engine "
    "with config: model='Org/Model', tokenizer='Org/Model', skip_tokenizer_init=False, "
    "tokenizer_mode=auto, trust_remote_code=False, dtype=torch.bfloat16"
)


def error_line(message: str) -> str:
    return f"(EngineCore pid=110) ERROR 10-23 08:01:02 [core.py:1346] {message}"


class TestEachRowOfTheTable:
    def test_memory_from_vllms_own_refusal(self):
        lines = [
            VLLM_BANNER,
            error_line(
                "ValueError: Available memory on node 0 (2.5/7.77 GiB) on kv cache "
                "allocation is less than requested memory for kv (12.0 GiB)."
            ),
        ]
        assert classify(lines) == "memory"

    def test_memory_from_a_kernel_kill(self):
        assert classify([], oom_killed=True) == "memory"
        assert classify(["Loading weights"], exit_code=137) == "memory"
        assert classify(["Killed"]) == "memory"

    def test_memory_from_a_worker_killed_with_sigkill(self):
        line = (
            "(APIServer pid=1) RuntimeError: Engine core initialization failed. See root "
            "cause above. Failed core proc(s): {'EngineCore_DP0': -9}"
        )
        assert classify([line]) == "memory"

    def test_memory_from_a_cache_too_small_for_the_context(self):
        line = error_line(
            "ValueError: To serve at least one request with the model's max seq len "
            "(40960), (4.38 GiB KV cache is needed, which is larger than the available "
            "KV cache memory (1.0 GiB)."
        )
        assert classify([line]) == "memory"

    def test_disk_full(self):
        assert classify(["OSError: [Errno 28] No space left on device"]) == "disk"

    def test_a_disk_warning_is_enough_on_its_own(self):
        # Hugging Face announces the shortfall as a warning, which the classifier
        # otherwise skips; it is the failure here, not noise.
        line = (
            "/usr/lib/python3/site-packages/huggingface_hub/file_download.py:812: "
            "UserWarning: Not enough free disk space to download the file. The expected "
            "file size is: 4970.00 MB. The target location only has 120.00 MB free."
        )
        assert classify([line]) == "disk"

    def test_offline(self):
        line = (
            "OSError: We couldn't connect to 'https://huggingface.co' to load this file, "
            "couldn't find it in the cached files."
        )
        assert classify([line]) == "offline"
        assert classify(["requests.exceptions.ConnectionError: Max retries exceeded"]) == "offline"

    def test_gated(self):
        line = (
            "huggingface_hub.errors.GatedRepoError: 401 Client Error. Cannot access gated "
            "repo for url https://huggingface.co/meta-llama/Llama-3.1-8B/resolve/main/"
            "config.json. Access to model meta-llama/Llama-3.1-8B is restricted."
        )
        assert classify([line]) == "gated"

    def test_not_found(self):
        line = (
            "OSError: Org/Missing is not a local folder and is not a valid model "
            "identifier listed on 'https://huggingface.co/models'"
        )
        assert classify([line]) == "not_found"

    def test_a_refused_token(self):
        line = (
            "huggingface_hub.errors.RepositoryNotFoundError: 401 Client Error. Repository "
            "Not Found for url: https://huggingface.co/api/models/Org/M. Invalid "
            "credentials in Authorization header"
        )
        # The token is the remedy, so it wins over "not found"...
        assert classify([line], has_token=True) == "token"
        # ...but only when there is a token to blame.
        assert classify([line], has_token=False) == "not_found"

    def test_wrong_format(self):
        line = (
            "OSError: TheBloke/Llama-2-7B-GGUF does not appear to have a file named "
            "config.json."
        )
        assert classify([line]) == "format"

    def test_unsupported_architecture(self):
        line = error_line(
            "ValueError: Model architectures ['FancyForCausalLM'] are not supported for now."
        )
        assert classify([line]) == "architecture"
        assert classify(["UnsupportedModelError: no backend loads this"]) == "architecture"

    def test_needs_its_own_code(self):
        line = (
            "ValueError: The repository for Org/M contains custom code which must be "
            "executed to correctly load the model. Please pass the argument "
            "`trust_remote_code=True` to allow custom code to be run."
        )
        assert classify([VLLM_BANNER, line]) == "remote_code"

    def test_broken_tokenizer(self):
        line = "OSError: Can't load tokenizer for 'Org/M'."
        assert classify([VLLM_BANNER, line]) == "tokenizer"

    def test_unknown_tool_parser(self):
        line = "KeyError: 'invalid tool call parser: kimi_k3 (chose from { hermes, ... })'"
        assert classify([line]) == "tool_parser"

    def test_wrong_processor(self):
        assert classify([], exit_code=132) == "processor"
        assert classify(["Illegal instruction (core dumped)"]) == "processor"

    def test_no_free_port(self):
        line = "No free port between 8001 and 8010 to publish the server on."
        assert classify([line]) == "port"


class TestWhatIsNotAFailure:
    def test_the_start_up_banner_explains_nothing(self):
        # Without level filtering, every failure was "needs its own code".
        assert classify([VLLM_BANNER]) is None

    def test_traceback_frames_are_not_causes(self):
        lines = [
            error_line("Traceback (most recent call last):"),
            error_line(
                '  File "/usr/lib/vllm/transformers_utils/tokenizer.py", line 217, in get'
            ),
            error_line("RuntimeError: something nobody anticipated"),
        ]
        assert classify(lines) is None

    def test_kayaks_own_notes_do_not_count(self):
        # A model whose name contains a marker must not be explained by its name.
        assert classify(["ℹ Loading Org/Some-Tokenizer-GGUF on this Mac's processor..."]) is None

    def test_progress_bars_are_not_failures(self):
        assert classify(["tokenizer.json: 100%|██████████| 7.03M/7.03M [00:00<00:00]"]) is None

    def test_the_first_row_wins(self):
        # Ran out of memory while loading the tokenizer: memory is the cause.
        lines = [error_line("OSError: tokenizer load failed: Cannot allocate memory")]
        assert classify(lines) == "memory"


class TestTheSentences:
    def test_memory_depends_on_where_and_how(self):
        cpu = explain([], oom_killed=True)
        limited = explain([], oom_killed=True, memory_limited=True)
        gpu = explain([], oom_killed=True, gpu=True)
        speech = explain([], oom_killed=True, offers_context=False)

        assert "Docker Desktop more memory" in cpu and "shorter context length" in cpu
        assert "start options" in limited
        assert gpu.startswith("This model needs more memory than this Mac has free.")
        assert "context length" not in speech

    def test_gated_depends_on_the_token(self):
        line = "GatedRepoError: Access to model Org/M is restricted."
        assert explain([line], has_token=False) == failures.GATED_NEEDS_TOKEN
        assert explain([line], has_token=True) == failures.GATED_NO_ACCESS

    def test_nothing_matched_names_the_model_and_where_to_look(self):
        message = explain(["RuntimeError: ?"], model_id="Qwen/Qwen2.5-Coder-7B-Instruct")
        assert message.startswith("Qwen2.5-Coder-7B-Instruct couldn't start.")
        assert "Details" in message

    def test_a_crash_after_ready_is_not_called_a_failed_start(self):
        message = explain(["RuntimeError: ?"], model_id="Org/M", was_ready=True)
        assert message.startswith("M stopped unexpectedly.")

    @pytest.mark.parametrize(
        "line",
        [
            "OSError: [Errno 28] No space left on device",
            "GatedRepoError: restricted",
            "OSError: Org/M is not a valid model identifier",
            "KeyError: 'invalid tool call parser: x'",
        ],
    )
    def test_no_sentence_leaks_jargon(self, line):
        message = explain([line], model_id="Org/M")
        for word in ("Error", "vLLM", "container", "port", "exit code", "CUDA", "OOM"):
            assert word not in message


class TestOtherFailures:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("toomanyrequests: You have reached your pull rate limit", failures.ENGINE_RATE_LIMITED),
            ("write /var/lib/docker/tmp: no space left on device", failures.ENGINE_DISK_FULL),
            ("Get https://registry-1.docker.io/v2/: dial tcp: lookup failed", failures.ENGINE_OFFLINE),
            ("", failures.ENGINE_OFFLINE),
        ],
    )
    def test_engine_download(self, text, expected):
        assert failures.explain_engine_download(text) == expected

    def test_gpu_setup(self):
        assert failures.explain_gpu_setup("pip: Could not resolve host pypi.org") == failures.GPU_SETUP_OFFLINE
        assert failures.explain_gpu_setup("uv exited with status 2") == failures.GPU_SETUP_FAILED
        assert failures.explain_gpu_setup(None) == failures.GPU_SETUP_FAILED

    def test_sentences_name_the_model_by_its_short_name(self):
        assert failures.couldnt_start("Org/Model-7B") == "Model-7B couldn't start."
        assert failures.crashed("Org/Model-7B") == "Model-7B stopped unexpectedly."
        assert failures.interrupted("Org/Model-7B") == (
            "Something interrupted the start of Model-7B. Try again."
        )
        assert failures.short_name(None) == "The model"


class TestLooksRaw:
    @pytest.mark.parametrize(
        "text",
        [
            "This model needs more memory than this Mac has free. Close other apps.",
            "Running under Rosetta.",
            "The model stopped because of an error. Try again.",
        ],
    )
    def test_sentences_written_for_people(self, text):
        assert failures.looks_raw(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "the GPU ran out of memory",
            "RuntimeError: [metal] failed",
            "Traceback (most recent call last):\n  File x",
            "mlx_lm.server exited with code 1.",
            "See /Users/me/.cache/kayak/metal.log.",
        ],
    )
    def test_text_written_for_programmers(self, text):
        assert failures.looks_raw(text) is True
