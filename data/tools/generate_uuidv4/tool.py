import uuid

def generate_uuidv4(count: int = 1, uppercase: bool = False, remove_hyphens: bool = False, prefix: str = "") -> str:
    """Generates one or more Version-4 (random) Universally Unique Identifiers (UUIDv4).

    Args:
        count: Number of UUIDs to generate (between 1 and 100). Defaults to 1.
        uppercase: If True, returns UUIDs in uppercase letters. Defaults to False.
        remove_hyphens: If True, removes hyphens from the UUID string. Defaults to False.
        prefix: Optional prefix string to prepend to each generated UUID. Defaults to "".

    Returns:
        A string containing the generated UUID(s), separated by newlines if count > 1.
    """
    if not isinstance(count, int) or count < 1 or count > 100:
        raise ValueError("Count must be an integer between 1 and 100.")

    uuids = []
    for _ in range(count):
        val = str(uuid.uuid4())
        if remove_hyphens:
            val = val.replace("-", "")
        if uppercase:
            val = val.upper()
        if prefix:
            val = f"{prefix}{val}"
        uuids.append(val)

    return "\n".join(uuids)
