"""Notebooks, the polling state endpoint, chat and chat history."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app import db
from app.models.schemas import ChatRequest, FeedbackRequest, NotebookCreate, NotebookUpdate
from app.routes.common import (
    UserError,
    ai_limit,
    artifact_summary,
    global_limit,
    note_summary,
    notebook_or_404,
    notebook_summary,
    source_summary,
)
from app.services import chat as chat_service
from app.services import guide as guide_service
from app.services import jobs
from app.services.llm import capabilities

router = APIRouter(prefix="/api", dependencies=[Depends(global_limit)])


@router.get("/notebooks")
async def list_notebooks():
    rows = db.query("notebooks", "SELECT * FROM notebooks ORDER BY opened_at DESC")
    out = []
    for row in rows:
        item = notebook_summary(row)
        stats = db.query_one(
            "artifacts", "SELECT COUNT(*) AS n FROM artifacts WHERE notebook_id = ? AND status = 'ready'",
            (row["id"],),
        )
        item["artifact_count"] = (stats or {}).get("n", 0)
        thumb = db.query_one(
            "sources", "SELECT meta FROM sources WHERE notebook_id = ? AND kind = 'youtube' ORDER BY position LIMIT 1",
            (row["id"],),
        )
        item["thumbnail"] = ((thumb or {}).get("meta") or {}).get("thumbnail", "")
        out.append(item)
    return {"notebooks": out}


@router.post("/notebooks")
async def create_notebook(payload: NotebookCreate):
    nid = db.new_id("n")
    ts = db.now()
    title = payload.title.strip() or guide_service.DEFAULT_TITLE
    db.insert("notebooks", {"id": nid, "title": title, "title_auto": int(title == guide_service.DEFAULT_TITLE),
                            "created_at": ts, "updated_at": ts, "opened_at": ts})
    return {"notebook": notebook_summary(db.get("notebooks", nid))}


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: str):
    notebook_or_404(notebook_id)
    db.update("notebooks", notebook_id, {"opened_at": db.now()})
    return _state(notebook_id)


@router.get("/notebooks/{notebook_id}/state")
async def notebook_state(notebook_id: str):
    notebook_or_404(notebook_id)
    return _state(notebook_id)


def _state(notebook_id: str) -> dict:
    nb = db.get("notebooks", notebook_id)
    sources = db.query("sources", "SELECT * FROM sources WHERE notebook_id = ? ORDER BY position, created_at",
                       (notebook_id,))
    artifacts = db.query("artifacts", "SELECT * FROM artifacts WHERE notebook_id = ? ORDER BY created_at DESC",
                         (notebook_id,))
    notes = db.query("notes", "SELECT * FROM notes WHERE notebook_id = ? ORDER BY updated_at DESC", (notebook_id,))
    return {
        "notebook": notebook_summary(nb),
        "sources": [source_summary(s) for s in sources],
        "artifacts": [artifact_summary(a) for a in artifacts],
        "notes": [note_summary(n) for n in notes],
        "ai": capabilities(),
    }


@router.patch("/notebooks/{notebook_id}")
async def update_notebook(notebook_id: str, payload: NotebookUpdate):
    notebook_or_404(notebook_id)
    values = {}
    if payload.title is not None:
        values["title"] = payload.title.strip()[:200] or guide_service.DEFAULT_TITLE
        values["title_auto"] = 0
    if payload.emoji is not None:
        values["emoji"] = payload.emoji.strip()[:8]
    if payload.chat_config is not None:
        allowed = {"style", "custom", "length", "level", "language"}
        values["chat_config"] = {k: str(v)[:1500] for k, v in payload.chat_config.items() if k in allowed}
    if values:
        values["updated_at"] = db.now()
        db.update("notebooks", notebook_id, values)
    return {"notebook": notebook_summary(db.get("notebooks", notebook_id))}


@router.delete("/notebooks/{notebook_id}")
async def delete_notebook(notebook_id: str):
    notebook_or_404(notebook_id)
    from app.routes.sources import remove_source_files

    for src in db.query("sources", "SELECT id, file_path FROM sources WHERE notebook_id = ?", (notebook_id,)):
        jobs.cancel(f"source:{src['id']}")
        remove_source_files(src)
    for art in db.query("artifacts", "SELECT id, file_path FROM artifacts WHERE notebook_id = ?", (notebook_id,)):
        jobs.cancel(f"artifact:{art['id']}")
        _remove_file(art.get("file_path"))
    jobs.cancel(guide_service.guide_key(notebook_id))
    db.execute("DELETE FROM chunks WHERE notebook_id = ?", (notebook_id,))
    db.delete("notebooks", notebook_id)
    return {"ok": True}


def _remove_file(path: str | None) -> None:
    from pathlib import Path

    from app.config import settings

    if not path:
        return
    try:
        p = Path(path).resolve()
        if settings.data_dir.resolve() in p.parents and p.exists():
            p.unlink()
    except OSError:
        pass


@router.post("/notebooks/{notebook_id}/guide", dependencies=[Depends(ai_limit)])
async def regenerate_guide(notebook_id: str):
    notebook_or_404(notebook_id)
    db.update("notebooks", notebook_id, {"guide_status": "processing"})
    guide_service.schedule(notebook_id, delay=0)
    return {"ok": True}


# ── Chat ─────────────────────────────────────────────────────────

@router.get("/notebooks/{notebook_id}/messages")
async def list_messages(notebook_id: str):
    notebook_or_404(notebook_id)
    rows = db.query("messages", "SELECT * FROM messages WHERE notebook_id = ? ORDER BY created_at", (notebook_id,))
    return {"messages": rows}


@router.delete("/notebooks/{notebook_id}/messages")
async def clear_messages(notebook_id: str):
    notebook_or_404(notebook_id)
    db.execute("DELETE FROM messages WHERE notebook_id = ?", (notebook_id,))
    return {"ok": True}


@router.post("/notebooks/{notebook_id}/chat", dependencies=[Depends(ai_limit)])
async def chat(notebook_id: str, payload: ChatRequest, request: Request):
    notebook_or_404(notebook_id)
    message = payload.message.strip()
    if not message:
        raise UserError("Type a question first.")

    async def events():
        stream = chat_service.stream_chat(notebook_id, message, payload.source_ids, payload.focus)
        try:
            async for chunk in stream:
                if await request.is_disconnected():
                    break
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("chat stream failed")
            yield chat_service.sse({"type": "error", "message": "Something went wrong. Please try again."})
        finally:
            await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/feedback")
async def message_feedback(message_id: str, payload: FeedbackRequest):
    msg = db.get("messages", message_id)
    if not msg:
        raise UserError("Message not found.", 404)
    db.update("messages", message_id, {"feedback": payload.value})
    return {"ok": True}


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str):
    db.delete("messages", message_id)
    return {"ok": True}
