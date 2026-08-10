import json
from typing import Optional
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
import httpx


async def web_search(query: str, max_results: Optional[int] = 5) -> str:
    """Performs an open-source web search using DuckDuckGo and returns search results.

    Args:
        query: Search query string.
        max_results: Maximum number of search results to return (default 5).
    """
    try:
        results = []
        with DDGS() as ddgs:
            raw_results = list(
                ddgs.text(query, max_results=max_results or 5)
            )
            for r in raw_results:
                results.append(
                    f"Title: {r.get('title', 'N/A')}\nURL:"
                    f" {r.get('href', 'N/A')}\nSnippet:"
                    f" {r.get('body', 'N/A')}\n"
                )

        if not results:
            return f"No search results found for query: '{query}'"

        return "\n---\n".join(results)
    except Exception as e:
        return f"Error performing web search: {str(e)}"


async def fetch_url(url: str, max_length: Optional[int] = 4000) -> str:
    """Fetches the text content of a public web page, stripping HTML and scripts.

    Args:
        url: Full web URL to fetch (must include http:// or https://).
        max_length: Maximum number of characters of text content to return (default 4000).
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        }
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15.0, headers=headers
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return (
                    f"Error: Received HTTP {response.status_code} from '{url}'"
                )

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove scripts, styles, and navigational elements
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "noscript"]
            ):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            text = " ".join(text.split())  # normalize whitespace

            if len(text) > (max_length or 4000):
                text = (
                    text[: max_length or 4000]
                    + "\n\n... [Content truncated due to length]"
                )

            return f"=== Content from {url} ===\n\n{text}"
    except Exception as e:
        return f"Error fetching URL '{url}': {str(e)}"
