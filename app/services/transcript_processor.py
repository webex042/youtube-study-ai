"""
Transcript Processor

Cleans raw caption text and splits it into timestamp-preserving chunks.

The size caps here are a cost control as much as a memory control: chunk
count directly determines how many LLM calls a single request makes, so an
unbounded chunker would let one long video run up an arbitrary bill.
"""

from __future__ import annotations

import re

from app.config import settings

# YouTube caption artefacts: [Music], [Applause], [inaudible].
_BRACKETED = re.compile(r"\[[^\]]{0,80}\]")
_WHITESPACE = re.compile(r"\s+")

# Control characters that should never reach a prompt or the DOM.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# A single caption cue is never legitimately longer than this.
_MAX_SNIPPET_CHARS = 2000


def clean_transcript(snippets: list[dict]) -> list[dict]:
    """
    Normalise transcript snippets.

    Strips caption artefacts and control characters, collapses whitespace,
    drops empty and consecutive-duplicate lines (common in auto-captions),
    and preserves timestamps. Enforces the configured overall size cap.
    """
    if not snippets:
        return []

    cleaned: list[dict] = []
    previous = ""
    total_chars = 0
    max_chars = settings.max_transcript_chars
    max_snippets = settings.max_transcript_snippets

    for item in snippets:
        if len(cleaned) >= max_snippets or total_chars >= max_chars:
            break

        raw = item.get("text")
        if not isinstance(raw, str):
            continue

        text = _CONTROL.sub("", raw).strip()
        text = _BRACKETED.sub("", text)
        text = _WHITESPACE.sub(" ", text).strip()

        if not text or text == previous:
            continue

        if len(text) > _MAX_SNIPPET_CHARS:
            text = text[:_MAX_SNIPPET_CHARS]

        try:
            start = max(0.0, float(item.get("start", 0.0)))
            duration = max(0.0, float(item.get("duration", 0.0)))
        except (TypeError, ValueError):
            continue

        cleaned.append({"text": text, "start": start, "duration": duration})
        previous = text
        total_chars += len(text)

    return cleaned


def merge_transcript_text(snippets: list[dict]) -> str:
    """Join snippet texts into a single string, capped at the configured limit."""
    if not snippets:
        return ""
    merged = " ".join(item["text"] for item in snippets if item.get("text"))
    limit = settings.max_transcript_chars
    return merged[:limit] if len(merged) > limit else merged


def chunk_transcript(
    snippets: list[dict],
    max_chars: int | None = None,
    max_chunks: int | None = None,
) -> list[dict]:
    """
    Split a transcript into chunks that fit an LLM context window.

    Each chunk carries `text`, `start_time`, `end_time` and its original
    `snippets`. Once `max_chunks` is reached the remainder is discarded rather
    than merged, so the number of LLM calls per request stays bounded. The
    truncation is logged upstream where the chunk count is recorded.
    """
    if not snippets:
        return []

    chunk_limit = max_chars or settings.chunk_max_chars
    count_limit = max_chunks or settings.max_chunks

    chunks: list[dict] = []
    current_texts: list[str] = []
    current_snippets: list[dict] = []
    current_length = 0
    chunk_start = float(snippets[0].get("start", 0.0))

    def flush() -> None:
        """Close the in-progress chunk."""
        if not current_texts:
            return
        last = current_snippets[-1]
        chunks.append({
            "text": " ".join(current_texts),
            "start_time": chunk_start,
            "end_time": float(last.get("start", 0.0)) + float(last.get("duration", 0.0)),
            "snippets": list(current_snippets),
        })

    for item in snippets:
        text = item.get("text") or ""
        if not text:
            continue

        if current_length + len(text) > chunk_limit and current_texts:
            flush()
            if len(chunks) >= count_limit:
                return chunks
            current_texts = []
            current_snippets = []
            current_length = 0
            chunk_start = float(item.get("start", 0.0))

        current_texts.append(text)
        current_snippets.append(item)
        current_length += len(text) + 1  # +1 for the joining space

    if current_texts and len(chunks) < count_limit:
        flush()

    return chunks
