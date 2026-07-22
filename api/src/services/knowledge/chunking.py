"""
Content chunking for the knowledge store.

Splits long documents into ~target_chars windows with overlap, preferring
natural boundaries (paragraph → sentence → word → hard cut) so that
embeddings are computed over coherent text instead of mid-sentence fragments.

Why character-based and not token-based:
- We don't want a tokenizer dependency in this layer (the embedding client
  owns that). ~4 chars/token is a stable approximation across English text
  and current OpenAI/Cohere/Anthropic models.
- The exact chunk size doesn't matter — embeddings degrade smoothly with
  size. Anywhere from 400-800 tokens is fine. We aim for ~500 (2000 chars).
"""
from __future__ import annotations

DEFAULT_TARGET_CHARS = 2000   # ~500 tokens
DEFAULT_OVERLAP_CHARS = 200   # ~50 tokens of trailing context repeated


def split_into_chunks(
    text: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """
    Split `text` into chunks of at most `target_chars`, preferring natural
    boundaries. Returns at least one chunk (an empty list is never valid —
    an empty doc returns `[""]`).

    ``reassemble_chunks`` is the exact inverse. That round-trip relies on
    every non-final chunk being longer than ``overlap_chars`` (guaranteed
    by the ``_find_boundary`` floor of half the window), so the params are
    validated here rather than trusting the caller.
    """
    if overlap_chars >= target_chars // 2:
        raise ValueError(
            "overlap_chars must be < target_chars // 2 so consecutive chunks "
            "overlap by exactly overlap_chars (required by reassemble_chunks)"
        )
    if len(text) <= target_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        if end < len(text):
            end = _find_boundary(text, start, end)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        # Step forward by (chunk_length - overlap) so the next window
        # repeats the last `overlap_chars` of this one.
        start = max(end - overlap_chars, start + 1)
    return chunks


def reassemble_chunks(
    chunks: list[str],
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> str:
    """
    Reconstruct the original text from chunks produced by
    ``split_into_chunks`` (in chunk order, with the same ``overlap_chars``).

    Exact inverse, not a heuristic: ``split_into_chunks`` always steps the
    next window to ``end - overlap_chars`` (the ``start + 1`` clamp can never
    win because ``_find_boundary`` keeps every non-final chunk at least half
    the window, and the param guard keeps that above ``overlap_chars``), so
    each chunk after the first repeats exactly its first ``overlap_chars``
    characters. Like the embedding column (see ``KnowledgeStore.embedding``),
    this ties read-time config to store-time config: changing
    ``DEFAULT_OVERLAP_CHARS`` requires re-storing existing chunked documents.
    """
    if not chunks:
        return ""
    return chunks[0] + "".join(chunk[overlap_chars:] for chunk in chunks[1:])


def _find_boundary(text: str, start: int, end: int) -> int:
    """
    Walk backward from `end` looking for a natural boundary. The search
    window is bounded by `start + (end-start)//2` so we never produce a
    chunk smaller than half the target (avoids pathological short chunks
    when boundaries are sparse).
    """
    min_acceptable = start + (end - start) // 2
    for boundary in ("\n\n", ". ", "? ", "! ", "\n", " "):
        idx = text.rfind(boundary, min_acceptable, end)
        if idx != -1:
            return idx + len(boundary)
    return end  # Hard cut — no boundary found in the search window.
