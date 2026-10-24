import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
import httpx
import litellm
from backend.app.config import local_api_base, settings
from backend.app.providers import API_KEY_SETTINGS, get_provider
from backend.app.inference import metal
from backend.app.inference.models import ServerState
from backend.app.user_errors import describe_llm_error

logger = logging.getLogger(__name__)

# Configure LiteLLM settings
litellm.drop_params = True  # Automatically drop unsupported parameters for providers

# Settings attribute holding the API key for each LiteLLM provider prefix. Sourced
# from the shared registry so a new provider does not need registering here too;
# "hf" is an alias LiteLLM accepts for Hugging Face.
_PROVIDER_KEY_SETTINGS: Dict[str, str] = {
    **API_KEY_SETTINGS,
    "hf": API_KEY_SETTINGS["huggingface"],
}

# LiteLLM prefix -> id in the provider registry, for the aliases that differ.
_PROVIDER_REGISTRY_IDS: Dict[str, str] = {"hf": "huggingface"}

#: How long a model may go quiet before the call is abandoned. LiteLLM's own
#: default is 6000 seconds, so a provider that stopped answering left the chat
#: "thinking" for an hour and a half. `read` bounds the gap between two streamed
#: chunks, not the whole answer, so a long reply is never cut off by it.
_CLOUD_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
#: A model on this Mac's processor can take minutes to read a long conversation
#: before its first word.
_LOCAL_TIMEOUT = httpx.Timeout(connect=15.0, read=900.0, write=30.0, pool=15.0)

# Substrings that identify a provider genuinely refusing tool calling, as opposed to
# any other 400. Matching loosely here turns unrelated failures (a malformed history,
# an exhausted context window, a bad key) into a silent downgrade to a tool-less agent.
_TOOL_UNSUPPORTED_MARKERS: Tuple[str, ...] = (
    "does not support tool",
    "does not support function",
    "tools are not supported",
    "tool use is not supported",
    "tool calling is not supported",
    "function calling is not supported",
    "unsupported parameter: 'tools'",
    "unsupported value: 'tools'",
    "unsupported parameter: 'tool_choice'",
    "no endpoints found that support tool use",
    "tools is not supported",
)


class ThinkingStreamParser:
    """Stateful streaming parser that separates <think>...</think> reasoning blocks from standard response text."""

    def __init__(self):
        self.in_think: bool = False
        self.buffer: str = ""

    def feed(self, text: str) -> List[Dict[str, str]]:
        """Processes an incoming text chunk and emits typed token events ('token' or 'thinking')."""
        events: List[Dict[str, str]] = []
        self.buffer += text

        while self.buffer:
            tag = "<think>" if not self.in_think else "</think>"
            emit_type = "token" if not self.in_think else "thinking"
            idx = self.buffer.find(tag)

            if idx != -1:
                prefix = self.buffer[:idx]
                if prefix:
                    events.append({"type": emit_type, "content": prefix})
                self.in_think = not self.in_think
                self.buffer = self.buffer[idx + len(tag):]
                # Strip leading newlines after </think>
                if not self.in_think:
                    if self.buffer.startswith("\n\n"):
                        self.buffer = self.buffer[2:]
                    elif self.buffer.startswith("\n"):
                        self.buffer = self.buffer[1:]
                continue
            else:
                # Check for partial tag match at tail
                partial_len = 0
                for i in range(1, min(len(tag), len(self.buffer) + 1)):
                    if tag.startswith(self.buffer[-i:]):
                        partial_len = i
                        break
                if partial_len > 0:
                    emit_text = self.buffer[:-partial_len]
                    if emit_text:
                        events.append({"type": emit_type, "content": emit_text})
                    self.buffer = self.buffer[-partial_len:]
                else:
                    events.append({"type": emit_type, "content": self.buffer})
                    self.buffer = ""
                break

        return events

    def flush(self) -> List[Dict[str, str]]:
        """Flushes any remaining text held in buffer."""
        events: List[Dict[str, str]] = []
        if self.buffer:
            event_type = "thinking" if self.in_think else "token"
            events.append({"type": event_type, "content": self.buffer})
            self.buffer = ""
        return events


def extract_thinking_and_content(raw_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extracts thinking and cleaned text content from a complete response string."""
    if not raw_text:
        return None, None
    pattern = r"<think>(.*?)</think>"
    match = re.search(pattern, raw_text, flags=re.DOTALL)
    if match:
        thinking = match.group(1).strip()
        content = re.sub(pattern, "", raw_text, flags=re.DOTALL).strip()
        return thinking or None, content or None
    return None, raw_text


def resolve_provider(model: str) -> Optional[str]:
    """Identifies the provider for a model string.

    An explicit ``provider/model`` prefix always wins. Matching on prefixes rather
    than substrings keeps a locally served model whose name merely contains another
    provider's name (``vllm/openai/gpt-oss-20b``) routed to the local endpoint.

    Args:
        model: LiteLLM model identifier.

    Returns:
        Optional[str]: Provider key, ``"vllm"`` for the local server, or None if unknown.
    """
    lowered = model.lower()

    if lowered.startswith("vllm/") or lowered.startswith("openai/vllm/"):
        return "vllm"

    if "/" in model:
        prefix = model.split("/", 1)[0].lower()
        if prefix in _PROVIDER_KEY_SETTINGS:
            return prefix
        return None

    # Bare model names without a provider prefix.
    if lowered.startswith(("gpt-", "gpt3", "gpt4", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith("gemini"):
        return "gemini"
    return None


def missing_key_error(model: str) -> Optional[str]:
    """Explains that a model cannot run because its provider has no stored credential.

    Worth checking before the call rather than letting the provider reject it. LiteLLM
    reads keys from the process environment when none is passed, so without this a key
    that happens to be exported in the shell would authenticate calls the Settings page
    reports as unconfigured -- and on a machine without one, the user saw a raw
    provider authentication error instead of being told where to put a key.

    Args:
        model: LiteLLM model identifier.

    Returns:
        Optional[str]: A message naming the provider, or None if the model can run.
    """
    provider = resolve_provider(model)
    # The local server needs no credential, and an unrecognised model string is left to
    # LiteLLM to accept or reject on its own terms.
    if not provider or provider == "vllm":
        return None

    if getattr(settings, _PROVIDER_KEY_SETTINGS[provider], ""):
        return None

    name = provider_display_name(provider)
    return (
        f"This agent uses {name}, and no {name} key has been added yet. Add one in "
        "Platform Settings, then send your message again."
    )


def provider_display_name(provider: Optional[str]) -> Optional[str]:
    """The name a person knows a provider by ("OpenAI"), from its LiteLLM prefix."""
    if not provider:
        return None
    if provider == "vllm":
        return "This Mac"
    info = get_provider(_PROVIDER_REGISTRY_IDS.get(provider, provider))
    return info.name if info else provider


def _local_model_id(model: str) -> str:
    """The Hugging Face repository a ``vllm/`` model string names."""
    for prefix in ("openai/vllm/", "vllm/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def turn_blocker(model: str) -> Optional[str]:
    """Why a turn on this model cannot start right now, or None if it can.

    Checked before a message is stored, for the same reason as a missing key: the
    composer puts a refused message back, while a turn that fails at once is often
    over before anyone is listening. A local model that is not running is the
    common case -- the composer waits for it, but a sub-agent or a second tab
    does not.
    """
    missing = missing_key_error(model)
    if missing:
        return missing
    if resolve_provider(model) != "vllm":
        return None

    from backend.app.inference import registry as inference_registry

    wanted = _local_model_id(model)
    status = inference_registry.text_manager.get_status()
    if status.state == ServerState.READY and status.model_id == wanted:
        return None
    name = wanted.rsplit("/", 1)[-1]
    return (
        f"{name} isn't running on this Mac. Start it from Local Models, then send "
        "your message again."
    )


def _local_api_base(model_id: str) -> str:
    """Where the local server serving this model answers right now.

    The manager's status is asked first: it believes the launcher only when the
    launcher's status answers the run Kayak asked for, so it is never fooled by a
    file still describing the GPU model that was just stopped -- which sent
    messages to a port nothing was serving. Metal is asked directly as a fallback,
    for a model the launcher has just brought up before the next status sync, but
    only when it names this model. A container may be published on a neighbouring
    port when the configured one was taken, and the manager's status says where.
    """
    from backend.app.inference import registry as inference_registry

    status = inference_registry.text_manager.get_status()
    if status.state == ServerState.READY and status.endpoint and status.model_id == model_id:
        return status.endpoint

    metal_status = metal.read_status()
    if (
        metal_status.supported
        and metal_status.state == "ready"
        and metal_status.port
        and metal_status.model == model_id
    ):
        return local_api_base(metal_status.port)
    return local_api_base(settings.VLLM_PORT)


def _build_llm_kwargs(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.7,
    stream: bool = False,
) -> Dict[str, Any]:
    """Builds provider-specific keyword arguments for LiteLLM completions.

    Args:
        model: LiteLLM model identifier string.
        messages: List of message dictionaries.
        tools: Optional OpenAI-compatible tool specifications.
        temperature: Sampling temperature between 0.0 and 1.0.
        stream: Whether to stream the response.

    Returns:
        Dict[str, Any]: Kwargs dictionary including API keys or custom endpoints.
    """
    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "stream": stream,
    }

    provider = resolve_provider(model)

    if provider == "vllm":
        kwargs["model"] = f"openai/{_local_model_id(model)}"
        kwargs["api_base"] = _local_api_base(_local_model_id(model))
        kwargs["api_key"] = "EMPTY"
        kwargs["timeout"] = _LOCAL_TIMEOUT
    else:
        kwargs["timeout"] = _CLOUD_TIMEOUT
        if provider:
            api_key = getattr(settings, _PROVIDER_KEY_SETTINGS[provider], "")
            if api_key:
                kwargs["api_key"] = api_key

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return kwargs


def _strip_tools(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of kwargs with tool-related keys removed for fallback retry."""
    fallback = dict(kwargs)
    fallback.pop("tools", None)
    fallback.pop("tool_choice", None)
    return fallback


def _is_tool_unsupported_error(error: Exception) -> bool:
    """Reports whether an API error specifically means the model cannot use tools.

    Only a provider explicitly rejecting tool calling justifies retrying without
    tools. Every other failure is surfaced, so that a broken request fails loudly
    instead of degrading into an agent that silently has no tools.
    """
    msg = str(error).lower()
    return any(marker in msg for marker in _TOOL_UNSUPPORTED_MARKERS)


async def _stream_response(kwargs: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
    """Streams completion chunks from a single LiteLLM call, parsing thinking tokens."""
    parser = ThinkingStreamParser()
    response = await litellm.acompletion(**kwargs)

    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            continue

        # Native reasoning_content field (e.g. from vLLM/OpenAI o-series/DeepSeek)
        reasoning_content = getattr(delta, "reasoning_content", None)
        if reasoning_content:
            yield {"type": "thinking", "content": reasoning_content}

        # Standard content tokens (may contain <think>...</think> tags)
        if hasattr(delta, "content") and delta.content:
            for event in parser.feed(delta.content):
                yield event

        # Tool call chunks
        if hasattr(delta, "tool_calls") and delta.tool_calls:
            for tool_call in delta.tool_calls:
                yield {
                    "type": "tool_call_delta",
                    "index": tool_call.index,
                    "id": getattr(tool_call, "id", None),
                    "name": getattr(tool_call.function, "name", None)
                    if hasattr(tool_call, "function")
                    else None,
                    "arguments": getattr(tool_call.function, "arguments", "")
                    if hasattr(tool_call, "function")
                    else "",
                }

    for event in parser.flush():
        yield event


async def generate_completion_stream(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.7,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Streams completion chunks from LiteLLM, handling thinking tokens, content tokens, and tool calls.

    Args:
        model: Model string identifier.
        messages: List of message dictionaries.
        tools: Optional OpenAI-compatible tool specifications.
        temperature: Sampling temperature.

    Yields:
        Dict[str, Any]: Token chunks, thinking chunks, tool call deltas, or errors.
    """
    blocker = turn_blocker(model)
    if blocker:
        yield {"type": "error", "error": blocker}
        return

    kwargs = _build_llm_kwargs(model, messages, tools, temperature, stream=True)
    provider = resolve_provider(model)
    # Whether the answer had begun, which changes what a local failure means: before
    # the first word the model was not there, after it the model stopped.
    answering = False

    def explain(error: BaseException) -> Dict[str, Any]:
        return {
            "type": "error",
            "error": describe_llm_error(
                error,
                provider_display_name(provider) if provider != "vllm" else None,
                local=provider == "vllm",
                answering=answering,
            ),
        }

    try:
        async for event in _stream_response(kwargs):
            answering = True
            yield event
    except Exception as error:
        if tools and not answering and _is_tool_unsupported_error(error):
            logger.warning(
                "Model '%s' rejected tool calling; retrying without tools: %s",
                model,
                error,
            )
            yield {
                "type": "warning",
                "warning": (
                    "This model can't use the agent's tools, so this answer is text "
                    "only. Pick a different model in Agent Profiles to let the agent "
                    "work with files and run things."
                ),
            }
            try:
                async for event in _stream_response(_strip_tools(kwargs)):
                    answering = True
                    yield event
                return
            except Exception as fallback_error:
                yield explain(fallback_error)
                return
        yield explain(error)


async def generate_text(model: str, messages: List[Dict[str, Any]], temperature: float) -> str:
    """Generates one complete, tool-free answer, with any reasoning stripped.

    Raises:
        Exception: Whatever the provider raised, or a missing-key error.
    """
    missing_key = missing_key_error(model)
    if missing_key:
        raise RuntimeError(missing_key)

    response = await litellm.acompletion(
        **_build_llm_kwargs(model, messages, temperature=temperature, stream=False)
    )
    _thinking, content = extract_thinking_and_content(response.choices[0].message.content)
    return content or ""


def excerpt_title(prompt: Optional[str]) -> Optional[str]:
    """A provisional title made of the first words of a prompt."""
    clean = " ".join((prompt or "").split())
    if not clean:
        return None
    return clean[:36].rsplit(" ", 1)[0] + "..." if len(clean) > 36 else clean


async def generate_title(prompt: str, model: str) -> str:
    """Generates a concise, relevant 3-6 word title for a conversation using an LLM call.

    The model is required: a title is written by the same model the conversation runs
    on, so it costs nothing extra to configure and cannot quietly call a provider the
    user never set up.

    Args:
        prompt: Initial user message text.
        model: Model the conversation runs on.

    Returns:
        str: Generated conversation title, or a sanitized excerpt of the prompt if the
        call fails.
    """
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You generate short, concise, natural 3 to 6 word titles for conversations based on the user's initial prompt. "
                    "Return ONLY the title text. Do not wrap in quotes or add trailing punctuation."
                ),
            },
            {"role": "user", "content": f"User prompt:\n{prompt[:350]}"},
        ]
        content = (await generate_text(model, messages, temperature=0.3)).strip()
        content = content.strip("\"'#-* \t\r\n")
        if content and len(content) <= 60:
            return content
    except Exception:
        pass

    return excerpt_title(prompt) or "New Conversation"
