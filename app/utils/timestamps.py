"""
Timestamp Utilities

Converts between seconds (float) and human-readable timestamps.
Also builds YouTube deep-link URLs with timestamp offsets.
"""


def seconds_to_timestamp(seconds: float) -> str:
    """
    Convert seconds to MM:SS or HH:MM:SS format.

    Examples:
        >>> seconds_to_timestamp(65.3)
        '01:05'
        >>> seconds_to_timestamp(3661)
        '1:01:01'
    """
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def timestamp_to_seconds(timestamp: str) -> int:
    """
    Convert MM:SS or HH:MM:SS string back to total seconds.

    Examples:
        >>> timestamp_to_seconds("01:05")
        65
        >>> timestamp_to_seconds("1:01:01")
        3661
    """
    parts = timestamp.split(':')
    parts = [int(p) for p in parts]

    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def youtube_timestamp_url(video_id: str, seconds: float) -> str:
    """
    Build a YouTube URL that starts playback at a specific timestamp.

    Example:
        >>> youtube_timestamp_url("abc123def45", 872)
        'https://www.youtube.com/watch?v=abc123def45&t=872s'
    """
    return f"https://www.youtube.com/watch?v={video_id}&t={int(seconds)}s"
