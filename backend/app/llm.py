import json
import os
from typing import Any, AsyncGenerator, Dict, List, Optional
import litellm
from backend.app.config import settings

# Configure LiteLLM settings
litellm.drop_params = True  # Automatically drop unsupported parameters for providers


def get_llm_kwargs(model: str, temperature: float = 0.7) -> Dict[str, Any]:
    """Builds provider-specific keyword arguments for LiteLLM completions.

    Args:
        model: LiteLLM model identifier string.
        temperature: Sampling temperature between 0.0 and 1.0.

    Returns:
        Dict[str, Any]: Kwargs dictionary including API keys or custom endpoints.
    """
    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
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
    elif model.startswith("ollama/"):
        kwargs["api_base"] = settings.OLLAMA_API_BASE
    elif model.startswith("vllm/") or model.startswith("openai/vllm"):
        kwargs["api_base"] = settings.VLLM_API_BASE

    return kwargs


async def generate_completion_stream(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.7,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Streams completion chunks from LiteLLM, handling both text tokens and tool calls.

    Args:
        model: Model string identifier.
        messages: List of message dictionaries.
        tools: Optional OpenAI-compatible tool specifications.
        temperature: Sampling temperature.

    Yields:
        Dict[str, Any]: Token chunks, tool call deltas, or errors.
    """
    kwargs = get_llm_kwargs(model, temperature)
    kwargs["messages"] = messages
    kwargs["stream"] = True

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # Check for token content
            if hasattr(delta, "content") and delta.content:
                yield {"type": "token", "content": delta.content}

            # Check for tool call chunks
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
    except Exception as error:
        yield {"type": "error", "error": str(error)}


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
        Dict[str, Any]: Response content and parsed tool calls.
    """
    kwargs = get_llm_kwargs(model, temperature)
    kwargs["messages"] = messages

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    try:
        response = await litellm.acompletion(**kwargs)
        message = response.choices[0].message
        return {
            "content": message.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in (message.tool_calls or [])
            ]
            if hasattr(message, "tool_calls") and message.tool_calls
            else None,
        }
    except Exception as error:
        return {"content": None, "tool_calls": None, "error": str(error)}


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
