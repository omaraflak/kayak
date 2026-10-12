import asyncio
from typing import Optional
from bs4 import BeautifulSoup
from ddgs import DDGS
import httpx
from backend.app.models import ToolCategory, ToolRisk
from backend.app.tools.metadata import tool_metadata


def _clean_html_text(html_content: str, max_length: int = 4000) -> str:
    """Strips non-content tags and normalizes whitespace from raw HTML."""
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())

    if len(text) > max_length:
        text = text[:max_length] + "\n\n... [Content truncated due to length]"
    return text


@tool_metadata(category=ToolCategory.WEB, risk=ToolRisk.LOW, read_only=True)
async def web_search(query: str, max_results: Optional[int] = 5) -> str:
    """Performs an open-source web search using DuckDuckGo and returns search results.

    Args:
        query: Search query string.
        max_results: Maximum number of search results to return (default 5).
    """
    def _search() -> list:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results or 5))

    try:
        # The search client is synchronous; run inline, it would stall every other
        # conversation's stream for as long as the search takes.
        results = [
            f"Title: {r.get('title', 'N/A')}\nURL: {r.get('href', 'N/A')}\n"
            f"Snippet: {r.get('body', 'N/A')}\n"
            for r in await asyncio.to_thread(_search)
        ]

        if not results:
            return f"No search results found for query: '{query}'"

        return "\n---\n".join(results)
    except Exception as e:
        message = str(e).lower()
        if "ratelimit" in message or "429" in message or "202" in message:
            return (
                "Error: The web search provider is rate limiting this host. Wait before"
                " searching again, or use fetch_url with a known URL instead."
            )
        return f"Error performing web search: {str(e)}"


@tool_metadata(category=ToolCategory.WEB, risk=ToolRisk.MODERATE, read_only=True)
async def fetch_url(url: str, max_length: Optional[int] = 4000) -> str:
    """Fetches the text content of a public web page, stripping HTML and scripts.

    Args:
        url: Full web URL to fetch (must include http:// or https://).
        max_length: Maximum number of characters of text content to return (default 4000).
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
        }
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15.0, headers=headers
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return f"Error: Received HTTP {response.status_code} from '{url}'"

            cleaned = _clean_html_text(response.text, max_length or 4000)
            return f"=== Content from {url} ===\n\n{cleaned}"
    except Exception as e:
        return f"Error fetching URL '{url}': {str(e)}"
