"""Notes, bookmarks, search, learning progress, export, settings and system status."""

from __future__ import annotations

import re
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app import db
from app.config import settings
from app.models.schemas import BookmarkCreate, NoteCreate, NoteUpdate, SettingsUpdate
from app.routes.common import UserError, ai_limit, global_limit, note_summary, notebook_or_404, source_summary
from app.services import sources as source_service
from app.services.llm import AIError, capabilities, require_provider
from app.services.retrieval import fmt_time

router = APIRouter(prefix="/api", dependencies=[Depends(global_limit)])


# ── Notes ────────────────────────────────────────────────────────

@router.post("/notebooks/{notebook_id}/notes")
async def create_note(notebook_id: str, payload: NoteCreate):
    notebook_or_404(notebook_id)
    nid = db.new_id("t")
    ts = db.now()
    kind = payload.kind if payload.kind in ("manual", "saved_response") else "manual"
    title = payload.title.strip() or "New Note"
    if kind == "saved_response" and title == "New Note":
        title = _title_from(payload.content)
    db.insert("notes", {"id": nid, "notebook_id": notebook_id, "title": title[:300], "content": payload.content,
                        "kind": kind, "citations": payload.citations or {}, "created_at": ts, "updated_at": ts})
    db.log_activity(notebook_id, "note")
    return {"note": db.get("notes", nid)}


def _title_from(markdown: str) -> str:
    """A short title for a saved answer: its first heading, else its first sentence."""
    lines = [ln.strip() for ln in (markdown or "").splitlines() if ln.strip()]
    for line in lines:
        if line.startswith("#"):
            text = re.sub(r"\s+", " ", re.sub(r"[#*_`]+", " ", line)).strip()
            if len(text) >= 2:
                return (text[:70] + "…") if len(text) > 70 else text
    for line in lines:
        text = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", line)
        text = re.sub(r"\s+", " ", re.sub(r"[#*_`>]+", " ", text)).strip()
        if len(text) > 3:
            sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
            return (sentence[:70] + "…") if len(sentence) > 70 else sentence
    return "Saved response"


@router.get("/notes/{note_id}")
async def get_note(note_id: str):
    note = db.get("notes", note_id)
    if not note:
        raise UserError("Note not found.", 404)
    return {"note": note}


@router.patch("/notes/{note_id}")
async def update_note(note_id: str, payload: NoteUpdate):
    note = db.get("notes", note_id)
    if not note:
        raise UserError("Note not found.", 404)
    values = {}
    if payload.title is not None:
        values["title"] = payload.title.strip()[:300] or "Untitled note"
    if payload.content is not None:
        values["content"] = payload.content
    if values:
        values["updated_at"] = db.now()
        db.update("notes", note_id, values)
    return {"note": db.get("notes", note_id)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    db.delete("notes", note_id)
    return {"ok": True}


@router.post("/notes/{note_id}/to-source", dependencies=[Depends(ai_limit)])
async def note_to_source(note_id: str):
    note = db.get("notes", note_id)
    if not note:
        raise UserError("Note not found.", 404)
    if len((note["content"] or "").strip()) < 20:
        raise UserError("This note is too short to use as a source.")
    src = source_service.create_source(note["notebook_id"], kind="note", title=note["title"], content=note["content"])
    source_service.start_processing(src["id"])
    return {"source": source_summary(db.get("sources", src["id"]))}


# ── Bookmarks ────────────────────────────────────────────────────

@router.get("/notebooks/{notebook_id}/bookmarks")
async def list_bookmarks(notebook_id: str):
    notebook_or_404(notebook_id)
    rows = db.query("bookmarks", "SELECT * FROM bookmarks WHERE notebook_id = ? ORDER BY created_at DESC",
                    (notebook_id,))
    return {"bookmarks": rows}


@router.post("/notebooks/{notebook_id}/bookmarks")
async def add_bookmark(notebook_id: str, payload: BookmarkCreate):
    notebook_or_404(notebook_id)
    bid = db.new_id("b")
    db.insert("bookmarks", {"id": bid, "notebook_id": notebook_id, "text": payload.text.strip(),
                            "origin_type": payload.origin_type, "origin_id": payload.origin_id,
                            "origin_title": payload.origin_title, "comment": payload.comment,
                            "created_at": db.now()})
    return {"bookmark": db.get("bookmarks", bid)}


@router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str):
    db.delete("bookmarks", bookmark_id)
    return {"ok": True}


# ── Search ───────────────────────────────────────────────────────

def _snippet(text: str, q: str, width: int = 90) -> str:
    low = text.lower()
    idx = low.find(q.lower())
    if idx == -1:
        return " ".join(text.split())[: width * 2]
    start = max(0, idx - width)
    end = min(len(text), idx + len(q) + width)
    snippet = " ".join(text[start:end].split())
    return ("…" if start else "") + snippet + ("…" if end < len(text) else "")


@router.get("/notebooks/{notebook_id}/search")
async def search(notebook_id: str, q: str = Query(min_length=2, max_length=200)):
    notebook_or_404(notebook_id)
    like = f"%{q.replace('%', '').replace('_', '')}%"
    results = []
    for row in db.query("notes", "SELECT id, title, content FROM notes WHERE notebook_id = ? AND "
                        "(title LIKE ? OR content LIKE ?) LIMIT 20", (notebook_id, like, like)):
        results.append({"type": "note", "id": row["id"], "title": row["title"],
                        "snippet": _snippet(row["content"] or row["title"], q)})
    for row in db.query("artifacts", "SELECT id, type, title, content FROM artifacts WHERE notebook_id = ? AND "
                        "status = 'ready' AND (title LIKE ? OR content LIKE ?) LIMIT 20", (notebook_id, like, like)):
        content = row["content"] or {}
        text = content.get("markdown") if isinstance(content, dict) and content.get("markdown") else str(content)
        results.append({"type": "artifact", "artifact_type": row["type"], "id": row["id"], "title": row["title"],
                        "snippet": _snippet(re.sub(r"[{}\[\]\"]", " ", text), q)})
    for row in db.query("chunks", "SELECT c.id, c.source_id, c.text, c.label, c.start_sec, s.title AS source_title "
                        "FROM chunks c JOIN sources s ON s.id = c.source_id WHERE c.notebook_id = ? AND "
                        "c.text LIKE ? ORDER BY s.position, c.ord LIMIT 30", (notebook_id, like)):
        results.append({"type": "source", "id": row["source_id"], "chunk_id": row["id"],
                        "title": row["source_title"], "label": row["label"], "start": row["start_sec"],
                        "snippet": _snippet(row["text"], q)})
    for row in db.query("messages", "SELECT id, role, content FROM messages WHERE notebook_id = ? AND "
                        "content LIKE ? ORDER BY created_at DESC LIMIT 10", (notebook_id, like)):
        results.append({"type": "message", "id": row["id"], "title": "You" if row["role"] == "user" else "Chat answer",
                        "snippet": _snippet(row["content"], q)})
    return {"results": results}


# ── Progress ─────────────────────────────────────────────────────

def _progress_for(notebook_ids: list[str]) -> dict:
    if not notebook_ids:
        return {}
    marks = ",".join("?" for _ in notebook_ids)
    ready = db.query_one("sources", f"SELECT COUNT(*) AS n FROM sources WHERE status = 'ready' AND notebook_id IN ({marks})",
                         notebook_ids)["n"]
    notes = db.query_one("notes", f"SELECT COUNT(*) AS n FROM notes WHERE notebook_id IN ({marks})", notebook_ids)["n"]
    artifacts = db.query("artifacts", f"SELECT id, type, title, content FROM artifacts WHERE status = 'ready' AND "
                         f"notebook_id IN ({marks})", notebook_ids)
    by_type: dict[str, int] = {}
    total_cards = 0
    for art in artifacts:
        by_type[art["type"]] = by_type.get(art["type"], 0) + 1
        if art["type"] == "flashcards":
            total_cards += len((art.get("content") or {}).get("cards") or [])
    reviews = db.query("flashcard_reviews", f"SELECT r.status, COUNT(*) AS n FROM flashcard_reviews r JOIN artifacts a "
                       f"ON a.id = r.artifact_id WHERE a.notebook_id IN ({marks}) GROUP BY r.status", notebook_ids)
    review_counts = {r["status"]: r["n"] for r in reviews}
    attempts = db.query("quiz_attempts", f"SELECT q.artifact_id, q.score, q.total, q.created_at, a.title FROM quiz_attempts q "
                        f"JOIN artifacts a ON a.id = q.artifact_id WHERE q.notebook_id IN ({marks}) "
                        f"ORDER BY q.created_at", notebook_ids)
    quizzes: dict[str, dict] = {}
    for att in attempts:
        entry = quizzes.setdefault(att["artifact_id"], {"title": att["title"], "attempts": 0, "best": 0, "last": 0,
                                                        "history": []})
        pct = round(100 * att["score"] / max(1, att["total"]))
        entry["attempts"] += 1
        entry["best"] = max(entry["best"], pct)
        entry["last"] = pct
        entry["history"].append(pct)
    activity = db.query("activity", f"SELECT day, SUM(count) AS n FROM activity WHERE notebook_id IN ({marks}) "
                        f"GROUP BY day ORDER BY day", notebook_ids)
    days = {a["day"]: a["n"] for a in activity}
    streak = 0
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    last30 = [{"day": (date.today() - timedelta(days=i)).isoformat(),
               "count": days.get((date.today() - timedelta(days=i)).isoformat(), 0)} for i in range(29, -1, -1)]
    avg = round(sum(q["last"] for q in quizzes.values()) / len(quizzes)) if quizzes else None
    return {
        "sources": ready, "notes": notes, "artifacts": by_type,
        "flashcards": {"total": total_cards, "understood": review_counts.get("understood", 0),
                       "review": review_counts.get("review", 0)},
        "quizzes": list(quizzes.values()), "quiz_average": avg,
        "streak": streak, "active_days": len(days), "activity": last30,
    }


@router.get("/notebooks/{notebook_id}/progress")
async def notebook_progress(notebook_id: str):
    notebook_or_404(notebook_id)
    return {"progress": _progress_for([notebook_id])}


@router.get("/progress")
async def all_progress():
    ids = [r["id"] for r in db.query("notebooks", "SELECT id FROM notebooks")]
    return {"progress": _progress_for(ids)}


# ── Export ───────────────────────────────────────────────────────

def _artifact_markdown(art: dict) -> str:
    c = art.get("content") or {}
    t = art["type"]
    lines = [f"## {art['title']}", ""]
    if t in ("notes", "report"):
        body = c.get("markdown", "")
        body = re.sub(r"^#\s+.+\n", "", body, count=1)
        lines.append(body)
    elif t == "flashcards":
        for i, card in enumerate(c.get("cards", []), 1):
            lines += [f"**{i}. {card['front']}**", f"> {card['back']}", ""]
    elif t == "quiz":
        for i, q in enumerate(c.get("questions", []), 1):
            lines.append(f"**{i}. {q['question']}**")
            for j, opt in enumerate(q["options"]):
                mark = "✅" if j == q["answer"] else "▫️"
                lines.append(f"- {mark} {opt}")
            lines += [f"> {q['explanation']}", ""]
    elif t == "datatable":
        cols = c.get("columns", [])
        if c.get("description"):
            lines += [c["description"], ""]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for row in c.get("rows", []):
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    elif t == "mindmap":
        def walk(node, depth=0):
            lines.append("  " * depth + f"- **{node['label']}**" + (f" — {node['note']}" if node.get("note") else ""))
            for child in node.get("children", []):
                walk(child, depth + 1)
        if c.get("root"):
            walk(c["root"])
    elif t == "slides":
        for i, s in enumerate(c.get("slides", []), 1):
            lines.append(f"### Slide {i}: {s['title']}")
            for b in s.get("bullets", []) + s.get("left", []) + s.get("right", []):
                lines.append(f"- {b}")
            if s.get("code"):
                lines += [f"```{s.get('code_language', '')}", s["code"], "```"]
            if s.get("notes"):
                lines.append(f"> Speaker notes: {s['notes']}")
            lines.append("")
    elif t == "infographic":
        lines.append(f"_{c.get('subtitle', '')}_")
        for s in c.get("stats", []):
            lines.append(f"- **{s['value']}** {s['label']}")
        for sec in c.get("sections", []):
            lines.append(f"### {sec['heading']}")
            lines += [f"- {p}" for p in sec["points"]]
        if c.get("steps"):
            lines.append("### " + (c.get("steps_title") or "Steps"))
            lines += [f"{i}. {s}" for i, s in enumerate(c["steps"], 1)]
        lines.append(f"\n**Takeaway:** {c.get('takeaway', '')}")
    elif t == "audio":
        for turn in c.get("turns", []):
            lines.append(f"**{turn['speaker']}** ({fmt_time(turn.get('start'))}): {turn['text']}")
    elif t == "video":
        for s in c.get("scenes", []):
            lines.append(f"### {s['title']}")
            lines += [f"- {b}" for b in s.get("bullets", [])]
            lines.append(f"> {s['narration']}")
    return "\n".join(lines).strip() + "\n"


@router.get("/notebooks/{notebook_id}/export")
async def export_notebook(notebook_id: str, include_chat: bool = True):
    nb = notebook_or_404(notebook_id)
    parts = [f"# {nb['emoji']} {nb['title']}".strip(), "",
             f"_Exported from AI Study Lab on {time.strftime('%d %b %Y')}_", ""]
    if nb["summary"]:
        parts += ["## Overview", nb["summary"], ""]
    sources = db.query("sources", "SELECT title, url, kind FROM sources WHERE notebook_id = ? ORDER BY position",
                       (notebook_id,))
    if sources:
        parts.append("## Sources")
        parts += [f"- {s['title']}" + (f" — {s['url']}" if s["url"] else "") for s in sources]
        parts.append("")
    for note in db.query("notes", "SELECT * FROM notes WHERE notebook_id = ? ORDER BY created_at", (notebook_id,)):
        parts += [f"## 📝 {note['title']}", "", note["content"], ""]
    for art in db.query("artifacts", "SELECT * FROM artifacts WHERE notebook_id = ? AND status = 'ready' "
                        "ORDER BY created_at", (notebook_id,)):
        parts += [_artifact_markdown(art), ""]
    if include_chat:
        messages = db.query("messages", "SELECT role, content FROM messages WHERE notebook_id = ? ORDER BY created_at",
                            (notebook_id,))
        if messages:
            parts.append("## Chat")
            for m in messages:
                who = "**You:**" if m["role"] == "user" else "**AI Study Lab:**"
                parts += [who, m["content"], ""]
    text = "\n".join(parts)
    filename = re.sub(r"[^A-Za-z0-9 _-]+", "", nb["title"]).strip().replace(" ", "_")[:80] or "notebook"
    return Response(text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.md"'})


# ── Settings & system ────────────────────────────────────────────

def _settings_payload() -> dict:
    return {
        "display_name": db.get_setting("display_name", ""),
        "pro_mode": bool(db.get_setting("pro_mode", False)),
        "explain_level": db.get_setting("explain_level", "intermediate"),
    }


@router.get("/settings")
async def get_settings():
    return {"settings": _settings_payload()}


@router.put("/settings")
async def put_settings(payload: SettingsUpdate):
    if payload.display_name is not None:
        db.set_setting("display_name", payload.display_name.strip()[:60])
    if payload.pro_mode is not None:
        db.set_setting("pro_mode", bool(payload.pro_mode))
    if payload.explain_level is not None and payload.explain_level in ("beginner", "intermediate", "expert"):
        db.set_setting("explain_level", payload.explain_level)
    return {"settings": _settings_payload()}


@router.get("/system/status")
async def system_status():
    caps = capabilities()
    caps.update({
        "models": {
            "default": list(dict.fromkeys(settings.gemini_models))[:4],
            "pro": list(dict.fromkeys(settings.gemini_pro_models))[:4],
            "tts": list(dict.fromkeys(settings.gemini_tts_models))[:3],
        },
        "video_analysis": settings.video_analysis_enabled,
        "max_upload_mb": settings.max_upload_mb,
    })
    return {"status": caps}


@router.post("/system/test-ai", dependencies=[Depends(ai_limit)])
async def test_ai():
    try:
        provider = require_provider()
        result = await provider.ping()
    except AIError as err:
        return {"ok": False, "message": err.message}
    return {"ok": bool(result.get("ok")), "model": result.get("model", ""),
            "message": f"Connected — using {result.get('model') or provider.name}."}
