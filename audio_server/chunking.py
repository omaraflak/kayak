"""Splitting text into pieces a speech model will accept.

Most speech models have a hard input limit -- Kokoro's is a few hundred characters --
and simply truncate or fail beyond it. Since the point of the Audio page is to paste
something long and get a file back, the splitting happens here rather than being the
caller's problem.

Splitting on sentences rather than at a fixed offset matters audibly: a cut in the
middle of a clause produces a clipped word and a wrong intonation on both sides.
"""

import re
from typing import List

#: Conservative default: below every backend's limit, and short enough that one bad
#: chunk is a short retry rather than a long one.
DEFAULT_CHUNK_CHARS = 400

#: End-of-sentence punctuation followed by whitespace. Kept with the sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")

#: Clause boundaries, used only when a single sentence is itself over the limit.
_CLAUSE_END = re.compile(r"(?<=[,;:，；：])\s+")


def split_for_synthesis(text: str, limit: int = DEFAULT_CHUNK_CHARS) -> List[str]:
    """Splits text into chunks no longer than ``limit`` where the text allows.

    Sentences are packed greedily so that short ones share a chunk -- one request per
    sentence would multiply model overhead for no benefit. A sentence longer than the
    limit is split at clause boundaries, and failing that at whitespace; a single
    unbroken word longer than the limit is passed through intact rather than cut,
    since cutting it would produce nonsense.

    Args:
        text: The text to speak.
        limit: Maximum characters per chunk.

    Returns:
        Non-empty chunks, in order. An empty or whitespace-only input yields [].
    """
    stripped = text.strip()
    if not stripped:
        return []
    if limit <= 0:
        return [stripped]

    chunks: List[str] = []
    current = ""

    for sentence in _SENTENCE_END.split(stripped):
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_sentence(sentence, limit))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, limit: int) -> List[str]:
    """Breaks one over-long sentence at the least damaging boundary available."""
    pieces: List[str] = []
    current = ""

    for clause in _CLAUSE_END.split(sentence):
        clause = clause.strip()
        if not clause:
            continue
        candidate = f"{current} {clause}".strip() if current else clause
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(clause) > limit:
            pieces.extend(_split_on_whitespace(clause, limit))
            current = ""
        else:
            current = clause

    if current:
        pieces.append(current)
    return pieces


def _split_on_whitespace(text: str, limit: int) -> List[str]:
    """Last resort: pack words up to the limit, never breaking one apart."""
    pieces: List[str] = []
    current = ""

    for word in text.split():
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            pieces.append(current)
            current = word

    if current:
        pieces.append(current)
    return pieces
