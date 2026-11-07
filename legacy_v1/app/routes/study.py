"""
Study Routes

    GET  /api/health  → liveness probe
    POST /api/study   → transcript → AI → study material

/api/study is the expensive endpoint, so it carries the strictest controls:
per-client rate limiting on two windows, a process-wide concurrency cap, a
total wall-clock timeout, and strict validation on both the way in and the
way out. Error responses are deliberately generic — the request ID is the
only handle a user gets, and the detail stays in the server log.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.models.schemas import StudyRequest
from app.security.rate_limit import (
    RateLimitExceeded,
    enforce_global_limit,
    enforce_study_limit,
    get_study_guard,
)
from app.services.flowchart_generator import validate_flowchart
from app.services.study_generator import generate_study_material
from app.services.transcript_processor import clean_transcript
from app.services.youtube_service import TranscriptError, fetch_transcript
from app.utils.youtube import extract_video_id

router = APIRouter(prefix="/api", tags=["study"])
logger = logging.getLogger(__name__)

_GENERIC_ERROR = (
    "Something went wrong while generating your study material. Please try again."
)
_BUSY_ERROR = "The service is busy right now. Please try again in a moment."
_TIMEOUT_ERROR = (
    "Generating your study material took too long. Try a shorter video."
)


def _error(status: int, message: str, request: Request, **headers: str) -> JSONResponse:
    """
    Build an error response.

    The body carries a safe message plus the request ID, never an exception,
    path, or internal identifier. Support can correlate the ID with the log.
    """
    request_id = getattr(request.state, "request_id", None)
    payload: dict[str, str] = {"error": message}
    if request_id:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status, content=payload, headers=headers or None)


@router.get("/health")
async def health_check(request: Request):
    """
    Liveness probe.

    Reports only that the process is up and whether an AI provider is
    configured — never which one, and never any key material.
    """
    try:
        enforce_global_limit(request)
    except RateLimitExceeded as exc:
        return _error(
            429, "Too many requests. Please slow down.", request,
            **{"Retry-After": str(exc.retry_after)},
        )

    return {
        "status": "ok",
        "ai_enabled": settings.llm_provider is not None,
    }


@router.post("/study")
async def analyze_video(payload: StudyRequest, request: Request):
    """
    Turn a YouTube URL into structured study material.

    Pipeline: rate limit → validate URL → fetch transcript → clean →
    generate → validate output → return. Every stage can only fail into a
    generic message.
    """
    # ── Rate limiting, before any expensive work ─────────────────
    try:
        enforce_study_limit(request)
    except RateLimitExceeded as exc:
        logger.info(
            "rate limit exceeded",
            extra={
                "endpoint": "/api/study",
                "rate_limit_scope": exc.scope,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _error(
            429,
            "You've made too many requests. Please wait a moment before trying again.",
            request,
            **{"Retry-After": str(exc.retry_after)},
        )

    # ── URL validation ───────────────────────────────────────────
    # extract_video_id enforces the scheme, host allowlist and ID shape, so a
    # non-YouTube or internal URL never reaches the fetch stage.
    if len(payload.url) > settings.max_url_length:
        return _error(400, "That URL is too long.", request)

    video_id = extract_video_id(payload.url)
    if not video_id:
        return _error(
            400,
            "Please enter a valid YouTube video URL "
            "(for example https://www.youtube.com/watch?v=…).",
            request,
        )

    # ── Concurrency cap ──────────────────────────────────────────
    # Reject rather than queue: making a user wait for a slot and *then* run
    # a multi-minute pipeline is worse than telling them to retry.
    guard = get_study_guard()
    if not await guard.acquire():
        logger.info(
            "study rejected at concurrency cap",
            extra={
                "endpoint": "/api/study",
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _error(503, _BUSY_ERROR, request, **{"Retry-After": "30"})

    try:
        async with asyncio.timeout(settings.total_study_timeout):
            return await _run_pipeline(video_id, request)

    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "study pipeline timed out",
            extra={
                "video_id": video_id,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _error(504, _TIMEOUT_ERROR, request)

    except TranscriptError as exc:
        # These messages are curated in youtube_service and safe to show.
        return _error(400, str(exc), request)

    except Exception as exc:
        # Full detail to the log, nothing to the client.
        logger.exception(
            "study generation failed",
            extra={
                "video_id": video_id,
                "error_type": type(exc).__name__,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _error(500, _GENERIC_ERROR, request)

    finally:
        # Always give the slot back, including on timeout and cancellation.
        await guard.release()


async def _run_pipeline(video_id: str, request: Request):
    """
    Fetch, clean and generate.

    The blocking transcript fetch and the LLM calls both run in a worker
    thread so the event loop stays responsive under concurrent requests.
    """
    transcript = await asyncio.to_thread(fetch_transcript, video_id)

    cleaned = clean_transcript(transcript["snippets"])
    if not cleaned:
        return _error(
            422,
            "This video's transcript had no usable text after cleaning. "
            "Try a video with spoken content.",
            request,
        )

    result = await asyncio.to_thread(
        generate_study_material,
        cleaned,
        video_id,
        transcript.get("language", "Unknown"),
        transcript.get("language_code", ""),
        transcript.get("is_generated", False),
    )

    # Belt-and-braces: re-validate the flowchart graph after assembly.
    result.flowchart = validate_flowchart(result.flowchart)

    # ── Data minimisation ────────────────────────────────────────
    # The transcript is only returned so the client can search within the
    # page. It is capped, and can be withheld entirely by configuration.
    if not settings.include_transcript_in_response:
        result.transcript_text = ""
    elif len(result.transcript_text) > settings.max_transcript_response_chars:
        result.transcript_text = result.transcript_text[
            : settings.max_transcript_response_chars
        ]

    logger.info(
        "study material generated",
        extra={
            "video_id": video_id,
            "endpoint": "/api/study",
            "transcript_chars": len(result.transcript_text),
            "request_id": getattr(request.state, "request_id", None),
        },
    )

    return result.model_dump()
