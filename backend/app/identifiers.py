import re


def clean_identifier(raw: str) -> str:
    """Turns a user-supplied name into a safe file and URL identifier."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw.lower().strip())
