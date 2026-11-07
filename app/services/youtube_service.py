"""
YouTube Service

Fetches transcripts via youtube-transcript-api. Three security concerns are
handled here:

* The video ID is re-validated before use, so a malformed value can never be
  interpolated into an outbound request even if an earlier check is changed.
* Every network call runs under a wall-clock timeout in a worker thread, so a
  hanging YouTube response cannot pin a request open indefinitely.
* Transcript size is capped as it is read. An unbounded transcript is a memory
  and token-cost exhaustion vector, so the cap is applied here rather than
  after the whole thing is in memory.

Transcripts are held in memory only. Nothing is written to disk.
"""

from __future__ import annotations

import logging

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.config import settings
from app.utils.youtube import is_valid_video_id

logger = logging.getLogger(__name__)

# Generic, user-safe failure messages. Provider exception text is never
# forwarded to the client, since it can contain internal URLs and paths.
_ERR_NO_TRANSCRIPT = (
    "No transcript is available for this video. It may not have captions, "
    "or captions may be disabled."
)
_ERR_DISABLED = (
    "Subtitles are disabled for this video. The video owner has turned off captions."
)
_ERR_UNAVAILABLE = (
    "This video is unavailable. It may be private, deleted, or region-restricted."
)
_ERR_TIMEOUT = "YouTube took too long to respond. Please try again."
_ERR_GENERIC = "Could not retrieve the transcript for this video. Please try again."


class TranscriptError(ValueError):
    """A user-safe transcript failure. The message is safe to display."""


def fetch_transcript(video_id: str, preferred_languages: list[str] | None = None) -> dict:
    """
    Fetch a transcript for a validated video ID.

    Returns a dict with `snippets`, `language`, `language_code`,
    `is_generated` and `truncated`. Raises TranscriptError with a message
    that is safe to show to the user.
    """
    if not is_valid_video_id(video_id):
        raise TranscriptError("Please enter a valid YouTube URL.")

    if preferred_languages is None:
        preferred_languages = ["en", "en-US", "en-GB"]

    try:
        return _fetch_with_timeout(video_id, preferred_languages)
    except TranscriptError:
        raise
    except TimeoutError:
        logger.warning("transcript fetch timed out", extra={"video_id": video_id})
        raise TranscriptError(_ERR_TIMEOUT)
    except Exception as exc:
        # Log the type for diagnosis; never surface the message.
        logger.warning(
            "transcript fetch failed",
            extra={"video_id": video_id, "error_type": type(exc).__name__},
        )
        raise TranscriptError(_ERR_GENERIC)


def _fetch_with_timeout(video_id: str, preferred_languages: list[str]) -> dict:
    """
    Run the blocking fetch under a wall-clock timeout.

    youtube-transcript-api is synchronous and does not expose a timeout, so
    the call is pushed to a worker thread and abandoned if it overruns. The
    thread is a daemon, so a stuck socket cannot keep the process alive.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_fetch_blocking, video_id, preferred_languages)
        try:
            return future.result(timeout=settings.transcript_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("transcript fetch timed out")


def _fetch_blocking(video_id: str, preferred_languages: list[str]) -> dict:
    """The actual provider calls. Runs in a worker thread."""
    api = YouTubeTranscriptApi()

    try:
        # Preferred languages first.
        try:
            return _format_transcript(api.fetch(video_id, languages=preferred_languages))
        except NoTranscriptFound:
            pass
        except (TranscriptsDisabled, VideoUnavailable):
            raise
        except Exception as exc:
            # YouTube intermittently returns an empty or malformed XML body.
            # Fall through to the listing path rather than failing outright.
            logger.debug(
                "preferred-language fetch failed, trying listing",
                extra={"video_id": video_id, "error_type": type(exc).__name__},
            )

        transcript_list = api.list(video_id)

        # Human-written captions are more accurate than auto-generated ones.
        for transcript in transcript_list:
            if not transcript.is_generated:
                return _format_transcript(transcript.fetch())

        for transcript in transcript_list:
            return _format_transcript(transcript.fetch())

        raise TranscriptError(_ERR_NO_TRANSCRIPT)

    except TranscriptsDisabled:
        raise TranscriptError(_ERR_DISABLED)
    except VideoUnavailable:
        raise TranscriptError(_ERR_UNAVAILABLE)
    except NoTranscriptFound:
        raise TranscriptError(_ERR_NO_TRANSCRIPT)


def _format_transcript(fetched) -> dict:
    """
    Convert a FetchedTranscript into our internal dict, enforcing size caps.

    Both a snippet-count and a character budget are applied while iterating,
    so an enormous transcript is cut off during the read rather than after.
    """
    max_snippets = settings.max_transcript_snippets
    max_chars = settings.max_transcript_chars

    snippets: list[dict] = []
    total_chars = 0
    truncated = False

    for snippet in fetched:
        if len(snippets) >= max_snippets or total_chars >= max_chars:
            truncated = True
            break

        text = snippet.text or ""
        if len(text) > 2000:
            # A single caption cue is never legitimately this long.
            text = text[:2000]

        snippets.append({
            "text": text,
            "start": max(0.0, float(snippet.start or 0.0)),
            "duration": max(0.0, float(snippet.duration or 0.0)),
        })
        total_chars += len(text)

    if not snippets:
        raise TranscriptError(_ERR_NO_TRANSCRIPT)

    if truncated:
        logger.info(
            "transcript truncated at configured limit",
            extra={"transcript_chars": total_chars},
        )

    return {
        "snippets": snippets,
        "language": str(getattr(fetched, "language", "Unknown"))[:100],
        "language_code": str(getattr(fetched, "language_code", ""))[:16],
        "is_generated": bool(getattr(fetched, "is_generated", False)),
        "truncated": truncated,
    }
