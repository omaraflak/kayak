import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
import litellm
from backend.app.config import settings

# Configure LiteLLM settings
litellm.drop_params = True  # Automatically drop unsupported parameters for providers


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

    # Pass API keys or custom endpoints based on model prefix
    if settings.OPENAI_API_KEY and ("gpt" in model.lower() or "o1" in model.lower() or "o3" in model.lower()):
        kwargs["api_key"] = settings.OPENAI_API_KEY
    elif "gemini" in model.lower() and settings.GEMINI_API_KEY:
        kwargs["api_key"] = settings.GEMINI_API_KEY
    elif "claude" in model.lower() and settings.ANTHROPIC_API_KEY:
        kwargs["api_key"] = settings.ANTHROPIC_API_KEY
    elif ("huggingface" in model.lower() or model.startswith("hf/")) and settings.HUGGINGFACE_API_KEY:
        kwargs["api_key"] = settings.HUGGINGFACE_API_KEY
    elif model.startswith("vllm/") or model.startswith("openai/vllm"):
        raw_model = model.replace("openai/vllm/", "").replace("vllm/", "")
        kwargs["model"] = f"openai/{raw_model}"
        kwargs["api_base"] = settings.VLLM_API_BASE
        kwargs["api_key"] = "EMPTY"

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


def _is_tool_error(error: Exception) -> bool:
    """Checks whether an API error is likely caused by unsupported tool calling."""
    msg = str(error).lower()
    return "tool" in msg or "400" in msg


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
    kwargs = _build_llm_kwargs(model, messages, tools, temperature, stream=True)

    try:
        async for event in _stream_response(kwargs):
            yield event
    except Exception as error:
        if tools and _is_tool_error(error):
            try:
                async for event in _stream_response(_strip_tools(kwargs)):
                    yield event
                return
            except Exception as fallback_error:
                yield {"type": "error", "error": str(fallback_error)}
                return
        yield {"type": "error", "error": str(error)}


def _parse_completion_response(message: Any) -> Dict[str, Any]:
    """Extracts content, thinking, and tool calls from a non-streamed completion response."""
    raw_content = getattr(message, "content", None)
    native_reasoning = getattr(message, "reasoning_content", None)
    extracted_thinking, clean_content = extract_thinking_and_content(raw_content)

    result: Dict[str, Any] = {
        "content": clean_content,
        "thinking": native_reasoning or extracted_thinking,
        "tool_calls": None,
    }

    if hasattr(message, "tool_calls") and message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]

    return result


async def generate_completion(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.7,
) -> Dict[str, Any]:
    """Generates a synchronous (non-streamed) completion from LiteLLM.

    Args:
        model: Model string identifier.
        messages: List of message dictionaries.
        tools: Optional tool schemas.
        temperature: Sampling temperature.

    Returns:
        Dict[str, Any]: Response content, thinking, and parsed tool calls.
    """
    kwargs = _build_llm_kwargs(model, messages, tools, temperature, stream=False)

    try:
        response = await litellm.acompletion(**kwargs)
        return _parse_completion_response(response.choices[0].message)
    except Exception as error:
        if tools and _is_tool_error(error):
            try:
                response = await litellm.acompletion(**_strip_tools(kwargs))
                result = _parse_completion_response(response.choices[0].message)
                result["tool_calls"] = None
                return result
            except Exception as fallback_error:
                return {"content": None, "thinking": None, "tool_calls": None, "error": str(fallback_error)}

        return {"content": None, "thinking": None, "tool_calls": None, "error": str(error)}


async def generate_title(prompt: str, model: Optional[str] = None) -> str:
    """Generates a concise, relevant 3-6 word title for a conversation using an LLM call.

    Args:
        prompt: Initial user message text.
        model: Optional model override (defaults to settings.DEFAULT_MODEL).

    Returns:
        str: Generated conversation title or sanitized fallback.
    """
    chosen_model = model or settings.DEFAULT_MODEL
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
        result = await generate_completion(
            model=chosen_model,
            messages=messages,
            temperature=0.3,
        )
        content = (result.get("content") or "").strip().strip("\"'#-* \t\r\n")
        if content and len(content) <= 60:
            return content
    except Exception:
        pass

    # Clean fallback based on first words of the prompt
    clean = " ".join(prompt.strip().split())
    if len(clean) > 36:
        return clean[:36].rsplit(" ", 1)[0] + "..."
    return clean or "New Conversation"
