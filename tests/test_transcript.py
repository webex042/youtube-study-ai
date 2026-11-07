"""
Tests for timestamp utilities and transcript processor.
"""

from app.utils.timestamps import seconds_to_timestamp, timestamp_to_seconds, youtube_timestamp_url
from app.services.transcript_processor import clean_transcript, chunk_transcript, merge_transcript_text


# ── Timestamp Tests ──────────────────────────────────────────────

def test_seconds_to_timestamp_minutes():
    assert seconds_to_timestamp(65) == "01:05"


def test_seconds_to_timestamp_hours():
    assert seconds_to_timestamp(3661) == "1:01:01"


def test_seconds_to_timestamp_zero():
    assert seconds_to_timestamp(0) == "00:00"


def test_seconds_to_timestamp_exact_minute():
    assert seconds_to_timestamp(120) == "02:00"


def test_timestamp_to_seconds_minutes():
    assert timestamp_to_seconds("01:05") == 65


def test_timestamp_to_seconds_hours():
    assert timestamp_to_seconds("1:01:01") == 3661


def test_youtube_timestamp_url():
    url = youtube_timestamp_url("abc123def45", 872)
    assert url == "https://www.youtube.com/watch?v=abc123def45&t=872s"


# ── Transcript Cleaning Tests ────────────────────────────────────

def test_clean_removes_artifacts():
    snippets = [
        {"text": "[Music] Hello world", "start": 0, "duration": 1},
        {"text": "This is a test", "start": 1, "duration": 1},
    ]
    cleaned = clean_transcript(snippets)
    assert cleaned[0]["text"] == "Hello world"


def test_clean_removes_duplicates():
    snippets = [
        {"text": "Hello", "start": 0, "duration": 1},
        {"text": "Hello", "start": 1, "duration": 1},
        {"text": "World", "start": 2, "duration": 1},
    ]
    cleaned = clean_transcript(snippets)
    assert len(cleaned) == 2


def test_clean_removes_empty():
    snippets = [
        {"text": "[Music]", "start": 0, "duration": 1},
        {"text": "Hello", "start": 1, "duration": 1},
    ]
    cleaned = clean_transcript(snippets)
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Hello"


def test_clean_preserves_timestamps():
    snippets = [
        {"text": "Hello", "start": 5.5, "duration": 2.0},
    ]
    cleaned = clean_transcript(snippets)
    assert cleaned[0]["start"] == 5.5
    assert cleaned[0]["duration"] == 2.0


# ── Transcript Merge Tests ───────────────────────────────────────

def test_merge_transcript_text():
    snippets = [
        {"text": "Hello", "start": 0, "duration": 1},
        {"text": "world", "start": 1, "duration": 1},
    ]
    assert merge_transcript_text(snippets) == "Hello world"


# ── Chunking Tests ───────────────────────────────────────────────

def test_chunk_single():
    """Short transcript should produce a single chunk."""
    snippets = [
        {"text": "Hello world", "start": 0, "duration": 2},
    ]
    chunks = chunk_transcript(snippets, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0]["start_time"] == 0


def test_chunk_splits():
    """Transcript exceeding max_chars should produce multiple chunks."""
    snippets = [
        {"text": "A" * 100, "start": i, "duration": 1}
        for i in range(20)
    ]
    chunks = chunk_transcript(snippets, max_chars=500)
    assert len(chunks) > 1

    # Check that all chunks have valid time ranges
    for chunk in chunks:
        assert chunk["start_time"] <= chunk["end_time"]


def test_chunk_empty():
    chunks = chunk_transcript([], max_chars=1000)
    assert chunks == []


def test_chunk_preserves_order():
    """Chunks should be in chronological order."""
    snippets = [
        {"text": f"Snippet {i}", "start": i * 10, "duration": 5}
        for i in range(50)
    ]
    chunks = chunk_transcript(snippets, max_chars=200)
    for i in range(len(chunks) - 1):
        assert chunks[i]["end_time"] <= chunks[i + 1]["start_time"] + 5  # allow slight overlap from duration
