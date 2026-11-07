"""Source endpoints: add (links, files, text), inspect, select, rename, retry, remove, web search."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app import db
from app.config import settings
from app.models.schemas import (
    AddTextRequest,
    AddUrlRequest,
    ImportResultsRequest,
    SelectSourcesRequest,
    SourceUpdate,
    WebSearchRequest,
)
from app.routes.common import UserError, ai_limit, global_limit, notebook_or_404, source_or_404, source_summary
from app.services import guide as guide_service
from app.services import jobs
from app.services import sources as source_service
from app.services.llm import AIError, require_provider
from app.services.retrieval import fmt_time
from app.services.web_fetch import domain_of, resolve_redirect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(global_limit)])

MAX_SOURCES_PER_NOTEBOOK = 300


def _check_capacity(notebook_id: str, adding: int = 1) -> None:
    row = db.query_one("sources", "SELECT COUNT(*) AS n FROM sources WHERE notebook_id = ?", (notebook_id,))
    if (row or {}).get("n", 0) + adding > MAX_SOURCES_PER_NOTEBOOK:
        raise UserError(f"A notebook can hold up to {MAX_SOURCES_PER_NOTEBOOK} sources.")


@router.post("/notebooks/{notebook_id}/sources/url", dependencies=[Depends(ai_limit)])
async def add_urls(notebook_id: str, payload: AddUrlRequest):
    notebook_or_404(notebook_id)
    urls = []
    for raw in payload.urls:
        urls += source_service.extract_urls(raw) or ([raw.strip()] if raw.strip() else [])
    if not urls:
        raise UserError("Paste at least one link.")
    _check_capacity(notebook_id, len(urls))
    added, errors = [], []
    for url in urls[:20]:
        try:
            added.append(source_summary(source_service.add_url(notebook_id, url)))
        except source_service.SourceError as exc:
            errors.append({"url": url, "error": str(exc)})
    if not added and errors:
        raise UserError(errors[0]["error"])
    return {"sources": added, "errors": errors}


@router.post("/notebooks/{notebook_id}/sources/text", dependencies=[Depends(ai_limit)])
async def add_text(notebook_id: str, payload: AddTextRequest):
    notebook_or_404(notebook_id)
    _check_capacity(notebook_id)
    text = payload.text.strip()
    if len(text) < 20:
        raise UserError("Paste a bit more text (at least a sentence or two).")
    if len(text) > settings.max_pasted_chars:
        raise UserError("That text is too long to paste. Save it as a .txt file and upload it instead.")
    title = payload.title.strip() or "Pasted text"
    src = source_service.create_source(notebook_id, kind="paste", title=title, content=text)
    source_service.start_processing(src["id"])
    return {"source": source_summary(db.get("sources", src["id"]))}


@router.post("/notebooks/{notebook_id}/sources/upload", dependencies=[Depends(ai_limit)])
async def upload_source(notebook_id: str, file: UploadFile = File(...)):
    notebook_or_404(notebook_id)
    _check_capacity(notebook_id)
    filename = Path(file.filename or "upload").name[:200]
    try:
        kind, mime = source_service.kind_for_filename(filename)
    except source_service.SourceError as exc:
        raise UserError(str(exc)) from exc

    sid = db.new_id("s")
    settings.ensure_dirs()
    suffix = re.sub(r"[^a-z0-9.]", "", Path(filename).suffix.lower())[:10]
    dest = settings.upload_dir / f"{sid}{suffix}"
    limit = settings.max_upload_bytes
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UserError(f"That file is too large (limit {settings.max_upload_mb} MB).", 413)
                await asyncio.to_thread(out.write, chunk)
    except UserError:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if size == 0:
        dest.unlink(missing_ok=True)
        raise UserError("That file is empty.")

    title = Path(filename).stem.replace("_", " ").strip() or filename
    src = source_service.create_source(notebook_id, kind=kind, title=title, file_path=str(dest), mime=mime,
                                       meta={"filename": filename, "size": size}, source_id=sid)
    source_service.start_processing(sid)
    return {"source": source_summary(db.get("sources", src["id"]))}


@router.get("/sources/{source_id}")
async def get_source(source_id: str):
    src = source_or_404(source_id)
    chunks = db.query("chunks", "SELECT id, ord, text, start_sec, end_sec, page, label FROM chunks "
                      "WHERE source_id = ? ORDER BY ord", (source_id,))
    data = source_summary(src)
    data["guide"] = src.get("guide") or {}
    meta = src.get("meta") or {}
    data["meta"]["description"] = (meta.get("description") or "")[:1500]
    data["chunks"] = chunks
    return {"source": data}


@router.patch("/sources/{source_id}")
async def update_source(source_id: str, payload: SourceUpdate):
    src = source_or_404(source_id)
    values = {}
    if payload.title is not None:
        values["title"] = payload.title.strip()[:300]
        values["meta"] = {**(src.get("meta") or {}), "renamed": True}
    if payload.selected is not None:
        values["selected"] = int(payload.selected)
    if values:
        values["updated_at"] = db.now()
        db.update("sources", source_id, values)
    return {"source": source_summary(db.get("sources", source_id))}


@router.post("/notebooks/{notebook_id}/sources/select")
async def select_sources(notebook_id: str, payload: SelectSourcesRequest):
    notebook_or_404(notebook_id)
    if payload.source_ids is None:
        db.execute("UPDATE sources SET selected = ? WHERE notebook_id = ?", (int(payload.selected), notebook_id))
    else:
        for sid in payload.source_ids:
            db.execute("UPDATE sources SET selected = ? WHERE id = ? AND notebook_id = ?",
                       (int(payload.selected), sid, notebook_id))
    return {"ok": True}


def remove_source_files(src: dict) -> None:
    path = src.get("file_path")
    if not path:
        return
    try:
        p = Path(path).resolve()
        if settings.upload_dir.resolve() in p.parents and p.exists():
            p.unlink()
    except OSError:
        pass


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    src = source_or_404(source_id)
    jobs.cancel(f"source:{source_id}")
    remove_source_files(src)
    db.delete("sources", source_id)
    guide_service.schedule(src["notebook_id"])
    return {"ok": True}


@router.post("/sources/{source_id}/retry", dependencies=[Depends(ai_limit)])
async def retry_source(source_id: str):
    src = source_or_404(source_id)
    if src["status"] in ("queued", "processing") and jobs.is_running(f"source:{source_id}"):
        raise UserError("This source is already being processed.")
    source_service.start_processing(source_id)
    return {"source": source_summary(db.get("sources", source_id))}


# ── Web search for new sources ───────────────────────────────────

@router.post("/notebooks/{notebook_id}/web-search", dependencies=[Depends(ai_limit)])
async def web_search(notebook_id: str, payload: WebSearchRequest):
    notebook_or_404(notebook_id)
    try:
        provider = require_provider()
        result = await provider.web_search(payload.query.strip(), youtube_only=payload.scope == "youtube",
                                           deep=payload.mode == "deep")
    except AIError as err:
        raise UserError(err.message, 503 if err.code in ("not_configured", "unsupported") else 502) from err

    # Grounding links are redirects; resolve them so the student sees (and we import) the real page.
    async def resolve(item):
        url = await resolve_redirect(item["url"])
        return {**item, "url": url, "domain": domain_of(url) or item.get("domain", "")}

    results = await asyncio.gather(*(resolve(r) for r in result["results"]))
    if payload.scope == "youtube":
        results = [r for r in results if "youtube.com" in r["url"] or "youtu.be" in r["url"]] or results
    return {"summary": result["summary"], "results": results}


@router.post("/notebooks/{notebook_id}/web-search/import", dependencies=[Depends(ai_limit)])
async def import_results(notebook_id: str, payload: ImportResultsRequest):
    return await add_urls(notebook_id, AddUrlRequest(urls=payload.urls))


def describe_duration(seconds) -> str:
    return fmt_time(seconds) if seconds else ""
