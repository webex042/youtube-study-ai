"""Shared route helpers: user-facing errors, rate limits and serializers."""

from __future__ import annotations

from fastapi import Request

from app import db
from app.config import settings
from app.security.rate_limit import RateLimitExceeded, enforce_global_limit, get_client_key, limiter


class UserError(Exception):
    """An error whose message is written for the user and safe to display."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def global_limit(request: Request) -> None:
    enforce_global_limit(request)


def ai_limit(request: Request) -> None:
    """Ceiling for endpoints that spend AI tokens."""
    if not settings.rate_limit_enabled:
        return
    key = get_client_key(request)
    allowed, _, retry = limiter.check(key, "ai:burst", settings.ai_rate_limit, settings.ai_rate_window)
    if not allowed:
        raise RateLimitExceeded(retry, "ai")
    allowed, _, retry = limiter.check(key, "ai:daily", settings.ai_daily_limit, settings.ai_daily_window)
    if not allowed:
        raise RateLimitExceeded(retry, "ai-daily")


def notebook_or_404(notebook_id: str) -> dict:
    nb = db.get("notebooks", notebook_id)
    if not nb:
        raise UserError("Notebook not found.", 404)
    return nb


def source_or_404(source_id: str) -> dict:
    src = db.get("sources", source_id)
    if not src:
        raise UserError("Source not found.", 404)
    return src


def artifact_or_404(artifact_id: str) -> dict:
    art = db.get("artifacts", artifact_id)
    if not art:
        raise UserError("That item no longer exists.", 404)
    return art


# ── Serializers ──────────────────────────────────────────────────

_SOURCE_META_KEYS = ("video_id", "thumbnail", "duration", "channel", "captions", "caption_language",
                     "visual_analysis", "page_count", "slide_count", "domain", "final_url", "language",
                     "analysis_resolution", "size", "filename", "renamed")


def source_summary(row: dict) -> dict:
    meta = row.get("meta") or {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "url": row["url"],
        "status": row["status"],
        "status_message": row["status_message"],
        "error": row["error"],
        "warnings": row.get("warnings") or [],
        "selected": bool(row["selected"]),
        "char_count": row["char_count"],
        "meta": {k: meta[k] for k in _SOURCE_META_KEYS if k in meta},
        "created_at": row["created_at"],
    }


def artifact_summary(row: dict) -> dict:
    content = row.get("content") or {}
    summary = {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "status_message": row["status_message"],
        "error": row["error"],
        "options": row.get("options") or {},
        "source_count": len(row.get("source_ids") or []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if row["type"] in ("notes", "report"):
        summary["label"] = content.get("label", "")
    return summary


def note_summary(row: dict) -> dict:
    text = (row.get("content") or "").strip()
    return {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "preview": " ".join(text.split())[:180],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def notebook_summary(row: dict) -> dict:
    counts = db.query_one(
        "sources", "SELECT COUNT(*) AS n FROM sources WHERE notebook_id = ?", (row["id"],)
    ) or {"n": 0}
    return {
        "id": row["id"],
        "title": row["title"],
        "emoji": row["emoji"],
        "summary": row["summary"],
        "questions": row.get("questions") or [],
        "guide_status": row["guide_status"],
        "guide_error": row["guide_error"],
        "chat_config": row.get("chat_config") or {},
        "title_auto": bool(row["title_auto"]),
        "source_count": counts["n"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "opened_at": row["opened_at"],
    }
