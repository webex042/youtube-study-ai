"""Studio endpoints: generate, list, open, edit, delete artifacts; audio; flashcard + quiz progress."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from app import db
from app.config import settings
from app.models.schemas import ArtifactUpdate, FlashcardReview, QuizAttempt, StudioRequest
from app.routes.common import (
    UserError,
    ai_limit,
    artifact_or_404,
    artifact_summary,
    global_limit,
    notebook_or_404,
)
from app.services import jobs
from app.services import studio as studio_service
from app.services.llm import AIError

router = APIRouter(prefix="/api", dependencies=[Depends(global_limit)])


@router.post("/notebooks/{notebook_id}/studio", dependencies=[Depends(ai_limit)])
async def create_artifact(notebook_id: str, payload: StudioRequest):
    notebook_or_404(notebook_id)
    options = {k: v for k, v in (payload.options or {}).items() if isinstance(k, str) and len(k) < 40}
    for key, value in list(options.items()):
        if isinstance(value, str):
            options[key] = value[:2000]
        elif not isinstance(value, (int, float, bool)) and value is not None:
            options.pop(key)
    try:
        art = studio_service.start(notebook_id, payload.type, options, payload.source_ids)
    except studio_service.StudioError as exc:
        raise UserError(str(exc)) from exc
    except AIError as err:
        raise UserError(err.message, 503) from err
    return {"artifact": artifact_summary(art)}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    art = artifact_or_404(artifact_id)
    data = artifact_summary(art)
    data["content"] = art.get("content") or {}
    data["citations"] = art.get("citations") or {}
    data["has_media"] = bool(art.get("file_path"))
    if art["type"] == "flashcards":
        rows = db.query("flashcard_reviews", "SELECT card_index, status FROM flashcard_reviews WHERE artifact_id = ?",
                        (artifact_id,))
        data["reviews"] = {str(r["card_index"]): r["status"] for r in rows}
    if art["type"] == "quiz":
        data["attempts"] = db.query(
            "quiz_attempts", "SELECT score, total, created_at FROM quiz_attempts WHERE artifact_id = ? "
            "ORDER BY created_at DESC LIMIT 10", (artifact_id,))
    return {"artifact": data}


@router.patch("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: str, payload: ArtifactUpdate):
    art = artifact_or_404(artifact_id)
    values = {}
    if payload.title is not None:
        values["title"] = payload.title.strip()[:200]
    if payload.content is not None:
        if art["type"] not in ("notes", "report", "datatable", "flashcards", "quiz", "slides"):
            raise UserError("This item can't be edited.")
        content = dict(art.get("content") or {})
        if art["type"] in ("notes", "report"):
            markdown = payload.content.get("markdown")
            if not isinstance(markdown, str):
                raise UserError("Nothing to save.")
            content["markdown"] = markdown[:2_000_000]
            content.pop("partial", None)
        else:
            content.update(payload.content)
        values["content"] = content
    if values:
        values["updated_at"] = db.now()
        db.update("artifacts", artifact_id, values)
    return {"artifact": artifact_summary(db.get("artifacts", artifact_id))}


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str):
    art = artifact_or_404(artifact_id)
    jobs.cancel(f"artifact:{artifact_id}")
    path = art.get("file_path")
    if path:
        try:
            p = Path(path).resolve()
            if settings.media_dir.resolve() in p.parents and p.exists():
                p.unlink()
        except OSError:
            pass
    db.delete("artifacts", artifact_id)
    return {"ok": True}


@router.post("/artifacts/{artifact_id}/retry", dependencies=[Depends(ai_limit)])
async def retry_artifact(artifact_id: str):
    art = artifact_or_404(artifact_id)
    if jobs.is_running(f"artifact:{artifact_id}"):
        raise UserError("It's already being generated.")
    db.update("artifacts", artifact_id, {"status": "processing", "status_message": "Reading your sources…",
                                         "error": "", "updated_at": db.now()})
    jobs.submit(f"artifact:{artifact_id}", lambda: studio_service.run(artifact_id))
    return {"artifact": artifact_summary(db.get("artifacts", artifact_id))}


@router.get("/artifacts/{artifact_id}/media")
async def artifact_media(artifact_id: str, request: Request):
    art = artifact_or_404(artifact_id)
    path = art.get("file_path")
    if not path:
        raise UserError("This item has no audio.", 404)
    p = Path(path).resolve()
    if settings.media_dir.resolve() not in p.parents or not p.exists():
        raise UserError("The audio file is missing. Generate it again.", 404)
    size = p.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Content-Type": "audio/wav",
               "Content-Disposition": f'inline; filename="{_safe_name(art["title"])}.wav"'}
    range_header = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if range_header and range_header.startswith("bytes="):
        spec = range_header[6:].split(",")[0].strip()
        first, _, last = spec.partition("-")
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                start = max(0, size - int(last))
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        except ValueError:
            start, end = 0, size - 1
    headers["Content-Length"] = str(end - start + 1)

    def iterate():
        with p.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(iterate(), status_code=status, headers=headers, media_type="audio/wav")


def _safe_name(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title)[:80].strip()
    return cleaned or "audio"


@router.post("/artifacts/{artifact_id}/cards/{index}")
async def review_card(artifact_id: str, index: int, payload: FlashcardReview):
    art = artifact_or_404(artifact_id)
    if art["type"] != "flashcards":
        raise UserError("Not a flashcard deck.")
    if payload.status == "clear":
        db.execute("DELETE FROM flashcard_reviews WHERE artifact_id = ? AND card_index = ?", (artifact_id, index))
    elif payload.status in ("understood", "review"):
        db.execute(
            "INSERT INTO flashcard_reviews(artifact_id, card_index, status, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(artifact_id, card_index) DO UPDATE SET status = excluded.status, "
            "updated_at = excluded.updated_at",
            (artifact_id, index, payload.status, db.now()),
        )
        db.log_activity(art["notebook_id"], "flashcards")
    else:
        raise UserError("Unknown status.")
    return {"ok": True}


@router.post("/artifacts/{artifact_id}/attempts")
async def quiz_attempt(artifact_id: str, payload: QuizAttempt):
    art = artifact_or_404(artifact_id)
    if art["type"] != "quiz":
        raise UserError("Not a quiz.")
    db.insert("quiz_attempts", {"id": db.new_id("q"), "artifact_id": artifact_id, "notebook_id": art["notebook_id"],
                                "score": min(payload.score, payload.total), "total": payload.total,
                                "answers": payload.answers[:500], "created_at": db.now()})
    db.log_activity(art["notebook_id"], "quiz")
    return {"ok": True}


@router.get("/studio/types")
async def studio_types():
    return {"types": studio_service.TYPES, "languages": studio_service.LANGUAGES}
