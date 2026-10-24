"""Why a local model did not start, said for the person who pressed Start.

A model server that dies leaves hundreds of lines behind it, and the one that names
the cause is usually a library's exception text: "huggingface_hub.errors.GatedRepoError:
401 Client Error", "ValueError: Available memory on node 0 ... is less than requested".
Accurate, and useless to someone who does not code. This module turns that output into
one sentence saying what happened and the one thing to do next.

It is a table rather than a chain of ``if`` statements, ordered so that the first row
that matches wins. The order is the design: a gated model's error also mentions the
tokenizer it could not download, and a model that ran out of memory also prints a
traceback through the code that was loading it, so the more fundamental cause has to
be asked about first.

Nothing is thrown away. The raw output stays in the model's logs -- the Details drawer
on its row -- and in the server log; only the headline is rewritten.
"""

from dataclasses import dataclass
import re
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

#: A log line's level and source, as vLLM writes them:
#: "(EngineCore pid=110) ERROR 08-28 07:39:28 [core.py:1346] ValueError: ...".
_VLLM_PREFIX = re.compile(
    r"^(?:\([^)]*\)\s*)?(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+"
    r"\d{2}-\d{2}\s+[\d:.]+\s+\[[^\]]*\]\s?"
)
#: The audio server's logging format: "2026-10-23 08:00:00,123 ERROR audio_server: ...".
_PYTHON_LOGGING_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T][\d:,.]+\s+(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+\S+:\s?"
)
#: Levels at which a server reports progress rather than failure. A line at one of
#: these levels routinely names things the rules look for -- vLLM's start-up banner
#: prints its whole configuration, ``tokenizer=`` and ``trust_remote_code=False``
#: included -- so matching them would explain every failure as the same one.
_QUIET_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "WARN"})

#: Hugging Face's progress bars ("model.safetensors:  45%|████▌ | 2.2G/4.9G").
_PROGRESS_BAR = re.compile(r"\d{1,3}%\|")

#: How Kayak marks the lines it writes into a model's log itself: notes about what it
#: decided, and steps that succeeded. They name the model being started, and a model
#: called "...-GGUF" or "...-tokenizer-fix" must not be explained by its own name.
KAYAK_NOTE = "ℹ"
KAYAK_SUCCESS = "✓"


def short_name(model_id: Optional[str]) -> str:
    """The part of a repository id a person recognises: its last segment.

    "Qwen/Qwen2.5-Coder-7B-Instruct" is shown as "Qwen2.5-Coder-7B-Instruct"; the
    organisation adds length without helping anyone tell two models apart.
    """
    if not model_id:
        return "The model"
    return model_id.rstrip("/").split("/")[-1] or model_id


def _message_of(line: str) -> Tuple[Optional[str], str]:
    """Splits a log line into its level, when it has one, and what it says."""
    for prefix in (_VLLM_PREFIX, _PYTHON_LOGGING_PREFIX):
        match = prefix.match(line)
        if match:
            return match.group("level"), line[match.end():]
    return None, line


def failure_lines(log_lines: Iterable[str]) -> List[str]:
    """The lines that could explain a failure, with their level prefix removed.

    Progress at INFO or WARNING level is dropped, and so are the "File ..." lines of
    a traceback: they name every module the failure passed through -- the tokenizer's
    among them, whatever actually went wrong -- and would match rules they have
    nothing to do with.
    """
    kept: List[str] = []
    for raw in log_lines:
        line = raw.strip()
        if not line or line.startswith((KAYAK_NOTE, KAYAK_SUCCESS)):
            continue
        level, message = _message_of(line)
        if level in _QUIET_LEVELS:
            continue
        message = message.strip()
        if (
            not message
            or message.startswith('File "')
            or message.startswith("Traceback (most recent call last)")
            or _PROGRESS_BAR.search(message)
        ):
            continue
        kept.append(message)
    return kept


@dataclass(frozen=True)
class FailureContext:
    """What the explanation may depend on besides the output itself."""
    #: The model's short name, for sentences that name it.
    name: str = "The model"
    #: The model was being served on the Mac's GPU rather than in Docker.
    gpu: bool = False
    #: The user chose a memory ceiling lower than everything Docker has.
    memory_limited: bool = False
    #: A Hugging Face token is configured in Platform Settings.
    has_token: bool = False
    #: The runtime has a context length to shorten. Speech models do not.
    offers_context: bool = True
    #: The model was already serving when it stopped, so it did start.
    was_ready: bool = False


def _memory(context: FailureContext) -> str:
    if context.gpu:
        return (
            "This model needs more memory than this Mac has free. Close other apps, "
            "or try a smaller model."
        )
    if context.memory_limited:
        return (
            "This model needs more memory than Kayak can give it. Give it more memory "
            "in the start options, or try a smaller model."
        )
    if context.offers_context:
        return (
            "This model needs more memory than Kayak can give it. Try a smaller model "
            "or a shorter context length, or give Docker Desktop more memory in its "
            "Settings, under Resources."
        )
    return (
        "This model needs more memory than Kayak can give it. Try a smaller model, or "
        "give Docker Desktop more memory in its Settings, under Resources."
    )


DISK_FULL = (
    "This Mac ran out of disk space while downloading the model. Free up some space, "
    "or delete models you no longer use under This machine, then try again."
)

OFFLINE = (
    "Kayak couldn't reach Hugging Face to download the model. Check your internet "
    "connection, then try again."
)

NOT_FOUND = (
    "Kayak couldn't find this model on Hugging Face. It may have been renamed, removed "
    "or made private. If it's private, add your Hugging Face token in Platform Settings."
)

TOKEN_REJECTED = (
    "Hugging Face didn't accept your token. Check it in Platform Settings, then try again."
)

GATED_NEEDS_TOKEN = (
    "The makers of this model ask you to accept their terms first. Open the model on "
    "Hugging Face, accept the terms, add your Hugging Face token in Platform Settings, "
    "then try again."
)

GATED_NO_ACCESS = (
    "Your Hugging Face account doesn't have access to this model yet. Open it on "
    "Hugging Face and request access, then try again."
)


def _gated(context: FailureContext) -> str:
    return GATED_NO_ACCESS if context.has_token else GATED_NEEDS_TOKEN


WRONG_FORMAT = (
    "This model isn't in a format Kayak can run. Look for a version of it without "
    "“GGUF” in the name."
)

UNSUPPORTED_ARCHITECTURE = "Kayak can't run this kind of model yet. Try a different one."

NEEDS_OWN_CODE = (
    "This model needs to run its own code to start, which Kayak blocks unless you allow "
    "it. If you trust who published it, turn on “Allow the model's own code” in the "
    "start options and try again."
)

BROKEN_FILES = (
    "Some of this model's files are missing or can't be read. Try another version of "
    "the model."
)

NO_TOOL_USE = "Kayak couldn't set up tool use for this model. Try a different model."

WRONG_PROCESSOR = (
    "This Mac's processor can't run this model engine. Try a model marked GPU, or a "
    "cloud model."
)

PORT_TAKEN = (
    "Another app is using what Kayak needs to start this model. Quit other AI apps you "
    "may have open, then try again."
)

DOCKER_STOPPED = (
    "Docker Desktop stopped while the model was starting. Open Docker Desktop, then "
    "try again."
)

TIMED_OUT = (
    "This model is taking too long to start. Try again, and if it keeps happening, try "
    "a smaller model."
)

LAUNCHER_GONE = (
    "Part of Kayak stopped responding, so this model can't start. Quit Kayak and open "
    "it again."
)

ENGINE_OFFLINE = (
    "Kayak couldn't download the software that runs models. Check your internet "
    "connection, then try again."
)

ENGINE_RATE_LIMITED = "Too many downloads right now. Wait a few minutes, then try again."

ENGINE_DISK_FULL = (
    "This Mac ran out of disk space while downloading the software that runs models. "
    "Free up some space, or delete models you no longer use under This machine, then "
    "try again."
)

GPU_SETUP_OFFLINE = (
    "Kayak couldn't download what it needs to use your Mac's GPU. Check your internet "
    "connection, then try again."
)

GPU_SETUP_FAILED = (
    "Kayak couldn't set up GPU support on this Mac. Try again, or use a model without "
    "“mlx-community” in its name."
)

SAVE_FAILED = (
    "Kayak couldn't save a file it needs to start this model. Make sure your Mac has "
    "free disk space, then try again."
)


def interrupted(model_id: Optional[str]) -> str:
    """For a start that nothing is carrying on with any more."""
    return f"Something interrupted the start of {short_name(model_id)}. Try again."


def stopped_unexpectedly(model_id: Optional[str]) -> str:
    """For a model that was serving and is no longer there."""
    return f"{short_name(model_id)} stopped unexpectedly. Start it again from Local Models."


def couldnt_start(model_id: Optional[str]) -> str:
    """The headline over a failed start; the reason goes underneath."""
    return f"{short_name(model_id)} couldn't start."


def crashed(model_id: Optional[str]) -> str:
    """The headline over a model that failed after it was ready."""
    return f"{short_name(model_id)} stopped unexpectedly."


@dataclass(frozen=True)
class _Rule:
    """One row of the table: what to look for, and what to say when it is found."""
    key: str
    #: Fragments matched case-insensitively against the failure lines.
    markers: Tuple[str, ...]
    message: Callable[[FailureContext], str]
    #: Also look at progress lines. For warnings that are themselves the failure,
    #: like Hugging Face announcing there is no room for the download.
    anywhere: bool = False
    #: Lines that must match exactly, such as the shell's bare "Killed".
    exact: Tuple[str, ...] = ()
    #: Exit codes that settle the question on their own.
    exit_codes: Tuple[int, ...] = ()
    #: Only consulted when a token is configured; without one the line cannot be
    #: about the token.
    needs_token: bool = False


_RULES: Tuple[_Rule, ...] = (
    _Rule(
        "memory",
        (
            "oomkilled",
            "': -9",
            "cannot allocate memory",
            "less than requested",
            "out of memory",
            # vLLM's "KV cache is needed, which is larger than the available KV cache
            # memory". When a shorter context would fit, the runtime retries on its
            # own before anyone reads this.
            "larger than the available kv cache memory",
            "no available memory for the cache blocks",
        ),
        _memory,
        exact=("killed",),
        exit_codes=(137,),
    ),
    _Rule(
        "disk",
        ("no space left on device", "errno 28", "not enough free disk space"),
        lambda _context: DISK_FULL,
        anywhere=True,
    ),
    _Rule(
        "offline",
        (
            "connectionerror",
            "max retries exceeded",
            "couldn't connect to 'https://huggingface.co'",
            "name resolution",
            "could not resolve host",
            "localentrynotfounderror",
            "connection reset",
        ),
        lambda _context: OFFLINE,
    ),
    # Ahead of "gated" and "not found" on purpose: a refused token makes Hugging Face
    # answer with a 401 that huggingface_hub reports as a missing or gated repository,
    # and the remedy is the token, not the model.
    _Rule(
        "token",
        ("invalid credentials", "invalid user token"),
        lambda _context: TOKEN_REJECTED,
        needs_token=True,
    ),
    _Rule(
        "gated",
        ("gatedrepoerror", "gated repo", "is restricted", "awaiting a review"),
        _gated,
    ),
    _Rule(
        "not_found",
        ("repositorynotfounderror", "repository not found", "not a valid model identifier"),
        lambda _context: NOT_FOUND,
    ),
    _Rule(
        "format",
        ("does not appear to have a file named config.json", "gguf", "unrecognized model"),
        lambda _context: WRONG_FORMAT,
    ),
    _Rule(
        "architecture",
        ("are not supported for now", "model architectures", "unsupportedmodelerror"),
        lambda _context: UNSUPPORTED_ARCHITECTURE,
    ),
    _Rule(
        "remote_code",
        ("trust_remote_code", "contains custom code"),
        lambda _context: NEEDS_OWN_CODE,
    ),
    _Rule(
        "tokenizer",
        ("tokenizer", "sentencepiece", "tiktoken"),
        lambda _context: BROKEN_FILES,
    ),
    _Rule("tool_parser", ("invalid tool call parser",), lambda _context: NO_TOOL_USE),
    _Rule(
        "processor",
        ("illegal instruction",),
        lambda _context: WRONG_PROCESSOR,
        exit_codes=(132,),
    ),
    _Rule("port", ("no free port",), lambda _context: PORT_TAKEN),
)

#: Every key the table can produce, for callers that act on a classification.
RULE_KEYS = tuple(rule.key for rule in _RULES)


def classify(
    log_lines: Sequence[str],
    exit_code: Optional[int] = None,
    oom_killed: bool = False,
    *,
    has_token: bool = False,
) -> Optional[str]:
    """Which row of the table explains a failure, or None.

    Args:
        log_lines: The server's output, oldest first, Kayak's own lines included.
        exit_code: The container's exit code, when it stopped on its own.
        oom_killed: Whether Docker reports the kernel killed it for memory.
        has_token: Whether a Hugging Face token is configured.

    Returns:
        Optional[str]: The key of the first matching row.
    """
    if oom_killed:
        return "memory"

    raw = [line.strip() for line in log_lines if line and line.strip()]
    candidates = [line.lower() for line in failure_lines(raw)]
    everything = [line.lower() for line in raw]
    bare = {line.lower() for line in raw}

    for rule in _RULES:
        if rule.needs_token and not has_token:
            continue
        if exit_code is not None and exit_code in rule.exit_codes:
            return rule.key
        if any(line in bare for line in rule.exact):
            return rule.key
        haystack = everything if rule.anywhere else candidates
        if any(marker in line for line in haystack for marker in rule.markers):
            return rule.key
    return None


def explain(
    log_lines: Sequence[str],
    exit_code: Optional[int] = None,
    oom_killed: bool = False,
    *,
    gpu: bool = False,
    memory_limited: bool = False,
    has_token: bool = False,
    model_id: Optional[str] = None,
    offers_context: bool = True,
    was_ready: bool = False,
) -> str:
    """The sentence to show for a model that failed to start, or stopped.

    Args:
        log_lines: The server's output, oldest first.
        exit_code: The container's exit code, when it stopped on its own.
        oom_killed: Whether Docker reports the kernel killed it for memory.
        gpu: Whether it ran on the Mac's GPU rather than in Docker.
        memory_limited: Whether the user gave it less memory than Docker has.
        has_token: Whether a Hugging Face token is configured.
        model_id: The repository, for the sentence that names it.
        offers_context: Whether a shorter context length is something to suggest.
        was_ready: Whether it had been serving before it stopped.

    Returns:
        str: What happened, and what to do next.
    """
    context = FailureContext(
        name=short_name(model_id),
        gpu=gpu,
        memory_limited=memory_limited,
        has_token=has_token,
        offers_context=offers_context,
        was_ready=was_ready,
    )
    key = classify(log_lines, exit_code, oom_killed, has_token=has_token)
    if key is not None:
        rule = next(rule for rule in _RULES if rule.key == key)
        return rule.message(context)
    if was_ready:
        return (
            f"{context.name} stopped unexpectedly. Open Details below to see what "
            "happened, or start it again."
        )
    return (
        f"{context.name} couldn't start. Open Details below to see what happened, or "
        "try a different model."
    )


def explain_engine_download(text: str) -> str:
    """The sentence for a failed download of the image that runs models.

    Args:
        text: Docker's error, from the pull stream or the exception it raised.
    """
    lowered = (text or "").lower()
    if "toomanyrequests" in lowered or "too many requests" in lowered or "rate limit" in lowered:
        return ENGINE_RATE_LIMITED
    if any(marker in lowered for marker in ("no space left on device", "errno 28", "disk quota")):
        return ENGINE_DISK_FULL
    return ENGINE_OFFLINE


#: What a failed GPU set-up says when the Mac could not download it.
_SETUP_OFFLINE_MARKERS = (
    "could not resolve",
    "name resolution",
    "network is unreachable",
    "connection refused",
    "connection reset",
    "connectionerror",
    "max retries exceeded",
    "timed out",
    "failed to fetch",
    "failed to download",
    "no internet",
    "offline",
)


def explain_gpu_setup(text: Optional[str]) -> str:
    """The sentence for a GPU environment that could not be installed.

    Args:
        text: Whatever the launcher reported about the failed set-up.
    """
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _SETUP_OFFLINE_MARKERS):
        return GPU_SETUP_OFFLINE
    return GPU_SETUP_FAILED


#: Shapes that only appear in text written for programmers.
_RAW_SHAPES = re.compile(
    r"Traceback|\bexit (?:code|status)\b|\berrno\b|(?:^|\s)~?/[\w.-]+/|"
    r"\.py\b|https?://|\bpip\b|\bstderr\b|\bstdout\b|\bpid\b|[{}<>\[\]]",
    re.IGNORECASE,
)
#: An exception's class name. Case matters: "ValueError" is code, while "an error"
#: is something a friendly sentence may well say.
_EXCEPTION_NAME = re.compile(r"\b[A-Za-z_]*(?:Error|Exception)\b")


def looks_raw(text: Optional[str]) -> bool:
    """Whether a message was written for a programmer rather than a person.

    The launcher's newer releases report a friendly sentence and keep the raw output
    separately; older ones put the raw output in the message. Only the latter needs
    explaining again.
    """
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if "\n" in stripped or len(stripped) > 400:
        return True
    if _RAW_SHAPES.search(stripped) or _EXCEPTION_NAME.search(stripped):
        return True
    # A sentence written for someone starts with a capital and ends like one.
    return not (stripped[0].isupper() and stripped[-1] in ".!?")


class StartFailure(Exception):
    """A start that failed for a reason already put into words.

    Raised inside a deployment so the handler that reports it does not have to work
    the reason out again from the exception text.
    """

    def __init__(self, message: str, detail: Optional[str] = None) -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail
