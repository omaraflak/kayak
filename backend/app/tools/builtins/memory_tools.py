from backend.app.memories.store import memory_store
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata


@tool_metadata(category=ToolCategory.KNOWLEDGE, risk=ToolRisk.LOW)
async def remember(content: str) -> str:
    """Saves something the user taught you, so you still know it in future conversations.

    Use this when the user corrects you, states a preference, or tells you a fact
    about their setup that will still be true tomorrow. Do not use it for details of
    the task you are currently doing.

    Every agent shares these memories, so write it as a fact about the user rather
    than a note to yourself.

    Args:
        content: One sentence stating what you learned.
    """
    try:
        memories = memory_store.add(content)
    except ValueError as error:
        return f"Error: {error}"

    return (
        f"Saved. Kayak now holds {len(memories)} "
        f"{'memory' if len(memories) == 1 else 'memories'}, and this one will be in"
        " every agent's prompt from the next turn onwards."
    )
