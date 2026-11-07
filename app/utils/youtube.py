"""
YouTube URL Utilities

Extracts and validates video IDs from user-supplied strings.

This module is a security boundary, not a convenience helper. Any string
that reaches it is untrusted, so parsing is strict: the scheme, host, path
shape and video ID must all match what we expect, and anything else is
rejected rather than guessed at.

Accepted forms:
  - https://www.youtube.com/watch?v=VIDEO_ID
  - https://youtu.be/VIDEO_ID
  - https://www.youtube.com/embed/VIDEO_ID
  - https://www.youtube.com/v/VIDEO_ID
  - https://www.youtube.com/shorts/VIDEO_ID
  - https://www.youtube.com/live/VIDEO_ID
  - a bare 11-character video ID
Extra query parameters (&list=, &t=, …) are ignored, not trusted.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.security.ssrf import is_blocked_hostname

# A YouTube video ID is exactly 11 chars from the URL-safe base64 alphabet.
VIDEO_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Hosts from which we will accept a video ID. Kept separate from the SSRF
# allowlist because that one also covers LLM providers and image CDNs.
_WATCH_HOSTS: frozenset[str] = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
})

_SHORT_HOSTS: frozenset[str] = frozenset({"youtu.be", "www.youtu.be"})

# Path prefixes on the main domain that carry the ID as a path segment.
_PATH_PREFIXES: tuple[str, ...] = ("/embed/", "/v/", "/shorts/", "/live/")

# Longest URL we will even attempt to parse.
_MAX_URL_LENGTH = 2048


def _has_control_chars(value: str) -> bool:
    """Detect control characters used for request smuggling or log injection."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def extract_video_id(url: str) -> str | None:
    """
    Extract an 11-character YouTube video ID from a URL or bare ID.

    Returns the ID, or None when the input is not a recognised, permitted
    YouTube reference. None is the only failure signal — this function never
    raises on malformed input and never reveals why parsing failed.

    Examples:
        >>> extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=42")
        'dQw4w9WgXcQ'
        >>> extract_video_id("http://localhost/watch?v=dQw4w9WgXcQ")
        None
        >>> extract_video_id("javascript:alert(1)")
        None
    """
    if not isinstance(url, str):
        return None

    candidate = url.strip()
    if not candidate or len(candidate) > _MAX_URL_LENGTH:
        return None
    if _has_control_chars(candidate):
        return None

    # A bare video ID pasted straight into the box.
    if VIDEO_ID_REGEX.match(candidate):
        return candidate

    # Tolerate a missing scheme for typed input, but only for hosts we allow.
    if "://" not in candidate:
        lowered = candidate.lower()
        if lowered.startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/",
                               "music.youtube.com/", "youtu.be/", "www.youtu.be/",
                               "youtube-nocookie.com/", "www.youtube-nocookie.com/")):
            candidate = f"https://{candidate}"
        else:
            return None

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    # Only http(s). Blocks javascript:, data:, file:, ftp:, gopher: and friends.
    if (parts.scheme or "").lower() not in {"http", "https"}:
        return None

    # Embedded credentials are never legitimate and confuse URL parsers.
    if parts.username or parts.password:
        return None
    netloc = parts.netloc or ""
    if "@" in netloc:
        return None

    hostname = (parts.hostname or "").lower().rstrip(".")
    if not hostname or is_blocked_hostname(hostname):
        return None

    # Non-standard ports are not used by YouTube.
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None and port not in (80, 443):
        return None

    path = parts.path or ""

    # youtube.com/watch?v=VIDEO_ID
    if hostname in _WATCH_HOSTS:
        if path in ("/watch", "/watch/"):
            values = parse_qs(parts.query, keep_blank_values=False).get("v", [])
            # Reject duplicated ?v= params rather than picking one, since
            # different parsers disagree on which value wins.
            if len(values) == 1 and VIDEO_ID_REGEX.match(values[0]):
                return values[0]
            return None

        for prefix in _PATH_PREFIXES:
            if path.startswith(prefix):
                segment = path[len(prefix):].split("/", 1)[0]
                if VIDEO_ID_REGEX.match(segment):
                    return segment
                return None
        return None

    # youtu.be/VIDEO_ID
    if hostname in _SHORT_HOSTS:
        segment = path.lstrip("/").split("/", 1)[0]
        if VIDEO_ID_REGEX.match(segment):
            return segment
        return None

    return None


def is_valid_video_id(video_id: str) -> bool:
    """True if the string is a well-formed YouTube video ID."""
    return isinstance(video_id, str) and bool(VIDEO_ID_REGEX.match(video_id))


def canonical_watch_url(video_id: str) -> str:
    """
    Build a canonical watch URL from an already-validated ID.

    Raises ValueError on an invalid ID so a malformed value can never be
    interpolated into a URL that is later fetched or rendered.
    """
    if not is_valid_video_id(video_id):
        raise ValueError("Invalid video ID.")
    return f"https://www.youtube.com/watch?v={video_id}"
