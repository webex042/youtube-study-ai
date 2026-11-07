"""
Source management and the ingestion pipeline.

    add (URL / upload / text)  →  queued row  →  background job:
        extract text + timing (captions, Gemini video understanding, PDF pages…)
        → passages with timestamps/pages → source guide → notebook guide

Every failure is recorded on the source with a specific, honest message.
Nothing is fabricated: if a video's content can't be retrieved, the source is
marked as failed instead of producing notes from nothing.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import struct
from pathlib import Path
from typing import Optional

from app import db
from app.config import settings
from app.models.schemas import SourceGuide
from app.services import guide as guide_service
from app.services import jobs, retrieval, video_understanding
from app.services.documents import (
    Extracted,
    ExtractionError,
    Segment,
    decode_text,
    extract_docx,
    extract_pdf,
    extract_pptx,
    extract_text,
    pdf_looks_scanned,
)
from app.services.llm import AIError, MediaRef, get_provider
from app.services.prompts import SOURCE_GUIDE_PROMPT, SOURCE_GUIDE_SYSTEM
from app.services.retrieval import fmt_time
from app.services.web_fetch import FetchError, decode_body, domain_of, fetch, html_to_text, youtube_metadata
from app.services.youtube_service import TranscriptError, fetch_transcript
from app.services.transcript_processor import clean_transcript
from app.utils.youtube import canonical_watch_url, extract_video_id

logger = logging.getLogger(__name__)


class SourceError(ValueError):
    """A user-safe ingestion failure."""


# ── File type detection ──────────────────────────────────────────

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".mpeg", ".mpg", ".m4v", ".3gp", ".wmv", ".flv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".opus", ".weba", ".aiff"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".gif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".rst", ".tex", ".log", ".py", ".js", ".ts",
            ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".html", ".htm", ".xml",
            ".yaml", ".yml", ".ipynb"}
SUB_EXT = {".srt", ".vtt"}
MIME_OVERRIDES = {
    ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".opus": "audio/ogg", ".weba": "audio/webm",
    ".heic": "image/heic", ".heif": "image/heif", ".md": "text/markdown", ".webm": "video/webm",
    ".flv": "video/x-flv", ".3gp": "video/3gpp", ".aiff": "audio/aiff",
}


def kind_for_filename(filename: str) -> tuple[str, str]:
    ext = Path(filename or "").suffix.lower()
    mime = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    if ext == ".pdf":
        return "pdf", "application/pdf"
    if ext == ".docx":
        return "docx", mime
    if ext == ".pptx":
        return "pptx", mime
    if ext in VIDEO_EXT:
        return "video", mime
    if ext in AUDIO_EXT:
        return "audio", mime
    if ext in IMAGE_EXT:
        return "image", mime
    if ext in SUB_EXT:
        return "subtitles", "text/plain"
    if ext in TEXT_EXT:
        return "text", "text/plain"
    if ext in (".doc", ".ppt"):
        raise SourceError("Old Office formats (.doc/.ppt) aren't supported. Save the file as .docx/.pptx or PDF.")
    raise SourceError(f"Unsupported file type '{ext or filename}'. Try PDF, DOCX, PPTX, TXT/MD, MP4 video, "
                      "MP3/WAV audio or an image.")


URL_RE = re.compile(r"https?://[^\s<>\"'`]+|(?:www\.|youtu\.be/|youtube\.com/)[^\s<>\"'`]+", re.I)


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(".,);:!?]}'\"")
        if not url.lower().startswith("http"):
            url = "https://" + url
        if url not in urls:
            urls.append(url)
    return urls[:10]


# ── Row helpers ──────────────────────────────────────────────────

def _next_position(notebook_id: str) -> int:
    row = db.query_one("sources", "SELECT MAX(position) AS p FROM sources WHERE notebook_id = ?", (notebook_id,))
    return int((row or {}).get("p") or 0) + 1


def create_source(notebook_id: str, *, kind: str, title: str, url: str = "", file_path: str = "",
                  mime: str = "", meta: Optional[dict] = None, content: str = "",
                  source_id: Optional[str] = None) -> dict:
    sid = source_id or db.new_id("s")
    ts = db.now()
    db.insert("sources", {
        "id": sid, "notebook_id": notebook_id, "kind": kind, "title": title[:300], "url": url[:2048],
        "file_path": file_path, "mime": mime, "status": "queued", "status_message": "Waiting to start…",
        "meta": meta or {}, "content": content, "position": _next_position(notebook_id),
        "created_at": ts, "updated_at": ts,
    })
    db.execute("UPDATE notebooks SET updated_at = ? WHERE id = ?", (ts, notebook_id))
    return db.get("sources", sid)


def start_processing(source_id: str) -> None:
    db.update("sources", source_id, {"status": "queued", "status_message": "Waiting to start…", "error": ""})
    jobs.submit(f"source:{source_id}", lambda: process_source(source_id))


def add_url(notebook_id: str, raw_url: str) -> dict:
    url = raw_url.strip()
    if len(url) > settings.max_url_length:
        raise SourceError("That URL is too long.")
    video_id = extract_video_id(url)
    if video_id:
        watch = canonical_watch_url(video_id)
        existing = db.query_one("sources", "SELECT id FROM sources WHERE notebook_id = ? AND url = ?",
                                (notebook_id, watch))
        if existing:
            raise SourceError("That video is already in this notebook.")
        src = create_source(notebook_id, kind="youtube", title=f"YouTube video ({video_id})", url=watch,
                            meta={"video_id": video_id, "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"})
    else:
        lowered = url.lower()
        if "youtube.com" in lowered or "youtu.be" in lowered:
            raise SourceError("That doesn't look like a YouTube video link. Use a link like "
                              "https://www.youtube.com/watch?v=… or https://youtu.be/…")
        if not settings.allow_web_sources:
            raise SourceError("Adding web pages is disabled (ALLOW_WEB_SOURCES=false).")
        if not lowered.startswith(("http://", "https://")):
            url = "https://" + url
        src = create_source(notebook_id, kind="website", title=domain_of(url) or url[:120], url=url)
    start_processing(src["id"])
    return src


# ── Status updates ───────────────────────────────────────────────

def _status(source_id: str, message: str, status: str = "processing") -> None:
    db.update("sources", source_id, {"status": status, "status_message": message[:200], "updated_at": db.now()})


async def _astatus(source_id: str, message: str) -> None:
    _status(source_id, message)


# ── The pipeline ─────────────────────────────────────────────────

async def process_source(source_id: str) -> None:
    src = db.get("sources", source_id)
    if not src:
        return
    _status(source_id, "Reading the source…")
    try:
        handler = {
            "youtube": _ingest_youtube,
            "website": _ingest_website,
            "pdf": _ingest_pdf,
            "docx": _ingest_office,
            "pptx": _ingest_office,
            "text": _ingest_text_file,
            "subtitles": _ingest_subtitles,
            "paste": _ingest_paste,
            "note": _ingest_paste,
            "video": _ingest_media,
            "audio": _ingest_media,
            "image": _ingest_image,
        }.get(src["kind"])
        if handler is None:
            raise SourceError("Unsupported source type.")
        extracted: Extracted = await handler(src)

        _status(source_id, "Building the study index…")
        chunks = retrieval.build_chunks(extracted.segments)
        if not chunks:
            raise SourceError("No readable content was found in this source.")
        content = "\n\n".join(c["text"] for c in chunks)
        if len(content) > settings.max_source_chars:
            extracted.warnings.append("This source is very long; only the first part was imported.")
            keep, total = [], 0
            for c in chunks:
                total += len(c["text"])
                if total > settings.max_source_chars:
                    break
                keep.append(c)
            chunks = keep
            content = "\n\n".join(c["text"] for c in chunks)

        retrieval.save_chunks(source_id, src["notebook_id"], chunks)

        guide = extracted.meta.pop("guide", None) or {}
        provider = get_provider()
        if provider is not None and not guide.get("summary"):
            _status(source_id, "Reviewing the source…")
            try:
                guide = {**guide, **(await make_source_guide(source_id, src["notebook_id"]))}
            except AIError as err:
                extracted.warnings.append(f"Couldn't generate a source guide: {err.message}")

        current = db.get("sources", source_id) or src
        title = current["title"]
        if not (current.get("meta") or {}).get("renamed") and extracted.title:
            title = extracted.title
        meta = {**(current.get("meta") or {}), **extracted.meta}
        db.update("sources", source_id, {
            "title": title[:300], "content": content, "char_count": len(content), "meta": meta,
            "guide": guide, "warnings": extracted.warnings[:6], "status": "ready", "status_message": "",
            "error": "", "updated_at": db.now(),
        })
        db.log_activity(src["notebook_id"], "source")
        guide_service.schedule(src["notebook_id"])
    except asyncio.CancelledError:
        _fail(source_id, "Processing was cancelled.")
        raise
    except (SourceError, ExtractionError, FetchError, TranscriptError) as exc:
        _fail(source_id, str(exc))
    except AIError as exc:
        _fail(source_id, exc.message)
    except Exception:  # noqa: BLE001
        logger.exception("source processing failed", extra={"source_kind": src.get("kind")})
        _fail(source_id, "Something went wrong while processing this source. Click retry.")


def _fail(source_id: str, message: str) -> None:
    db.update("sources", source_id, {"status": "error", "error": message[:600], "status_message": "",
                                     "updated_at": db.now()})


async def make_source_guide(source_id: str, notebook_id: str) -> dict:
    provider = get_provider()
    if provider is None:
        return {}
    # The source is still marked "processing" at this point; its passages are saved.
    ctx = retrieval.build_context(notebook_id, [source_id], budget=120_000, any_status=True)
    if ctx.empty:
        return {}
    result = await provider.json(system=SOURCE_GUIDE_SYSTEM, prompt=SOURCE_GUIDE_PROMPT.format(context=ctx.text),
                                 schema=SourceGuide, tier="lite", max_tokens=4000)
    return {
        "summary": result.summary.strip()[:2000],
        "key_topics": [t.strip()[:120] for t in result.key_topics if t.strip()][:10],
        "suggested_questions": [q.strip()[:200] for q in result.suggested_questions if q.strip()][:4],
    }


# ── YouTube ──────────────────────────────────────────────────────

def _transcript_languages() -> list[str]:
    return ["en", "en-US", "en-GB", "en-IN", "hi", "hi-IN"]


async def _ingest_youtube(src: dict) -> Extracted:
    meta = dict(src.get("meta") or {})
    video_id = meta.get("video_id") or extract_video_id(src["url"])
    if not video_id:
        raise SourceError("That isn't a valid YouTube video link.")
    sid = src["id"]

    await _astatus(sid, "Fetching video details…")
    info = await youtube_metadata(video_id)
    meta.update({k: v for k, v in info.items() if v not in (None, "")})

    await _astatus(sid, "Fetching captions…")
    transcript = None
    transcript_error = ""
    try:
        transcript = await asyncio.to_thread(fetch_transcript, video_id, _transcript_languages())
    except TranscriptError as exc:
        transcript_error = str(exc)

    snippets = clean_transcript(transcript["snippets"]) if transcript else []
    if snippets:
        last = snippets[-1]
        meta.setdefault("duration", int(last["start"] + last.get("duration", 0)))
        meta["captions"] = "auto-generated" if transcript.get("is_generated") else "manual"
        meta["caption_language"] = transcript.get("language", "")

    provider = get_provider()
    analysis = None
    analysis_error = ""
    warnings: list[str] = []
    if provider is not None and provider.supports_media and settings.video_analysis_enabled:
        await _astatus(sid, "Watching the video (frames + audio)…")
        try:
            analysis = await video_understanding.analyze(
                provider, MediaRef(uri=canonical_watch_url(video_id)), duration=meta.get("duration"),
                need_transcript=not snippets, kind="youtube",
                progress=lambda m: _astatus(sid, m),
            )
        except AIError as exc:
            analysis_error = exc.message
    elif provider is None:
        analysis_error = "AI is not configured"
    elif not settings.video_analysis_enabled:
        analysis_error = "video analysis is turned off (VIDEO_ANALYSIS=false)"
    else:
        analysis_error = "the configured AI provider can't watch videos (needs Gemini)"

    if not snippets and (analysis is None or not analysis.transcript):
        reasons = []
        if meta.get("unavailable") or meta.get("restricted"):
            reasons.append("the video appears to be private, age-restricted or unavailable")
        if transcript_error:
            reasons.append(f"captions: {transcript_error.rstrip('.')}")
        if analysis_error:
            reasons.append(f"video analysis: {analysis_error.rstrip('.')}")
        raise SourceError(
            "Couldn't get this video's content, so nothing was imported"
            + (" — " + "; ".join(reasons) if reasons else "")
            + ". Public videos with captions work best; you can also upload the video file or paste a transcript."
        )

    if analysis is None:
        warnings.append(f"Only the spoken transcript was imported (visual analysis unavailable: {analysis_error}). "
                        "On-screen code, slides and diagrams aren't included.")
    elif not snippets:
        warnings.append("This video has no captions; the transcript was generated by AI from the audio.")
    if analysis is not None and not analysis.is_educational:
        warnings.append("This video doesn't look like instructional content, so study material may be thin.")
    if analysis is not None and analysis.failed_ranges:
        gaps = ", ".join(f"{fmt_time(r['start'] or 0)}–{fmt_time(r['end']) if r['end'] else 'end'}" for r in analysis.failed_ranges)
        warnings.append(f"Part of the video couldn't be analysed ({gaps}): {analysis.failed_ranges[0]['reason']} "
                        + ("That part uses the captions only." if snippets else "That part is missing — click retry later."))

    segments = _video_segments(snippets, analysis)
    title = meta.get("title") or (analysis.title if analysis else "") or f"YouTube video {video_id}"
    if analysis is not None:
        meta["visual_analysis"] = True
        meta["analysis_segments"] = analysis.segments
        if analysis.low_resolution:
            meta["analysis_resolution"] = "low"
        if not snippets:
            meta["captions"] = "ai-transcribed"
        meta["guide"] = _guide_from_analysis(analysis)
        meta["language"] = analysis.language
    return Extracted(title=title, segments=segments, meta=meta, warnings=warnings)


def _guide_from_analysis(analysis) -> dict:
    return {
        "summary": analysis.summary,
        "main_topic": analysis.main_topic,
        "key_topics": [t["title"] for t in analysis.topics][:12],
        "chapters": [
            {"title": t["title"], "start": t["start"], "summary": t["summary"]} for t in analysis.topics
        ][:60],
        "key_terms": analysis.key_terms[:60],
        "formulas": analysis.formulas[:30],
    }


def _video_segments(snippets: list[dict], analysis) -> list[Segment]:
    """Interleave transcript paragraphs and on-screen notes by time."""
    timeline: list[Segment] = []
    lines = snippets or [
        {"text": line["text"], "start": line["start"] or 0.0, "duration": 0.0}
        for line in (analysis.transcript if analysis else [])
    ]
    # Group caption cues into ~45-second paragraphs that end on a sentence.
    buf: list[str] = []
    start = None
    end = 0.0
    for cue in lines:
        if start is None:
            start = cue["start"]
        buf.append(cue["text"])
        end = cue["start"] + (cue.get("duration") or 0.0)
        joined = " ".join(buf)
        long_enough = (end - start) >= 45 or len(joined) >= 700
        if long_enough and re.search(r"[.?!।]\s*$", joined) or (end - start) >= 75 or len(joined) >= 1100:
            timeline.append(Segment(text=joined, start=start, end=end, kind="speech"))
            buf, start = [], None
    if buf:
        timeline.append(Segment(text=" ".join(buf), start=start, end=end, kind="speech"))

    if analysis is not None:
        for vis in analysis.visuals:
            text = f"[On screen · {vis['kind']}] {vis['description']}"
            if vis["content"]:
                fence = "```" if vis["kind"] in ("code", "terminal", "ui") else ""
                text += f"\n{fence}\n{vis['content']}\n{fence}" if fence else f"\n{vis['content']}"
            timeline.append(Segment(text=text, start=vis["time"], end=vis["time"], kind="visual"))
    timeline.sort(key=lambda s: (s.start is None, s.start or 0.0, 0 if s.kind == "speech" else 1))
    return timeline


# ── Uploaded media ───────────────────────────────────────────────

def mp4_duration(path: str) -> Optional[float]:
    """Read the duration from an MP4/MOV 'mvhd' box without external tools."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8 * 1024 * 1024)
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8 * 1024 * 1024))
            tail = fh.read()
        for blob in (head, tail):
            idx = blob.find(b"mvhd")
            if idx == -1:
                continue
            version = blob[idx + 4]
            if version == 1:
                timescale, duration = struct.unpack(">IQ", blob[idx + 24: idx + 36])
            else:
                timescale, duration = struct.unpack(">II", blob[idx + 16: idx + 24])
            if timescale:
                return duration / timescale
    except (OSError, struct.error, IndexError):
        return None
    return None


async def _ingest_media(src: dict) -> Extracted:
    provider = get_provider()
    if provider is None:
        raise SourceError("Video and audio files are understood by AI, which isn't configured yet. Add "
                          "GEMINI_API_KEY to .env and restart, or add the YouTube link / a transcript instead.")
    if not provider.supports_media:
        raise SourceError("Video and audio analysis needs a Gemini API key (GEMINI_API_KEY).")
    path = src["file_path"]
    if not path or not Path(path).exists():
        raise SourceError("The uploaded file is missing. Please upload it again.")
    kind = src["kind"]
    sid = src["id"]
    duration = mp4_duration(path) if Path(path).suffix.lower() in (".mp4", ".mov", ".m4v", ".m4a", ".3gp") else None

    await _astatus(sid, "Uploading to Gemini for analysis…")
    ref = await provider.upload_media(path, src["mime"] or "video/mp4", src["title"])
    await _astatus(sid, "Watching the video (frames + audio)…" if kind == "video" else "Listening to the audio…")
    analysis = await video_understanding.analyze(provider, ref, duration=duration, need_transcript=True, kind=kind,
                                                 progress=lambda m: _astatus(sid, m))
    if not analysis.transcript and not analysis.visuals:
        raise SourceError("The AI couldn't find any speech or teachable content in this file.")
    meta = {"captions": "ai-transcribed", "visual_analysis": kind == "video", "guide": _guide_from_analysis(analysis),
            "language": analysis.language}
    if duration:
        meta["duration"] = int(duration)
    elif analysis.transcript:
        last = [line["start"] for line in analysis.transcript if line["start"] is not None]
        if last:
            meta["duration"] = int(max(last))
    title = src["title"]
    if analysis.title and (title.lower().startswith(("vid", "img", "rec", "screen", "audio", "untitled"))
                           or re.match(r"^[\d _-]+$", title)):
        title = analysis.title
    return Extracted(title=title, segments=_video_segments([], analysis), meta=meta)


async def _ingest_image(src: dict) -> Extracted:
    provider = get_provider()
    if provider is None or not provider.supports_media:
        raise SourceError("Reading images needs a Gemini API key (GEMINI_API_KEY in .env).")
    await _astatus(src["id"], "Reading the image…")
    ref = await provider.upload_media(src["file_path"], src["mime"] or "image/png", src["title"])
    analysis = await video_understanding.analyze_image(provider, ref)
    segments = []
    for vis in analysis.visuals:
        text = vis["description"] + (f"\n{vis['content']}" if vis["content"] else "")
        segments.append(Segment(text=text.strip()))
    if not segments and analysis.summary:
        segments.append(Segment(text=analysis.summary))
    if not segments:
        raise SourceError("No readable content was found in this image.")
    meta = {"guide": {"summary": analysis.summary, "key_topics": [t["title"] for t in analysis.topics][:8],
                      "key_terms": analysis.key_terms[:30]}}
    return Extracted(title=analysis.title or src["title"], segments=segments, meta=meta)


# ── Documents ────────────────────────────────────────────────────

def _read_file(src: dict) -> bytes:
    path = src.get("file_path")
    if not path or not Path(path).exists():
        raise SourceError("The uploaded file is missing. Please upload it again.")
    return Path(path).read_bytes()


async def _ingest_pdf(src: dict) -> Extracted:
    data = await asyncio.to_thread(_read_file, src)
    await _astatus(src["id"], "Reading the PDF…")
    extracted = await asyncio.to_thread(extract_pdf, data, src["title"])
    if pdf_looks_scanned(extracted):
        provider = get_provider()
        if provider is None or not provider.supports_media:
            if not extracted.segments:
                raise SourceError("This PDF looks scanned (no text layer). Reading scanned PDFs needs a Gemini "
                                  "API key, or upload a PDF with selectable text.")
            extracted.warnings.append("This PDF has very little selectable text; it may be scanned.")
        else:
            await _astatus(src["id"], "Reading scanned pages with AI…")
            ref = await provider.upload_media(src["file_path"], "application/pdf", src["title"])
            text = await _transcribe_pdf(provider, ref)
            pages = re.split(r"^=== Page (\d+) ===\s*$", text, flags=re.M)
            segments = []
            for i in range(1, len(pages) - 1, 2):
                body = pages[i + 1].strip()
                if body:
                    segments.append(Segment(text=body, page=int(pages[i])))
            if not segments and text.strip():
                segments = [Segment(text=text.strip(), page=1)]
            extracted.segments = segments
            extracted.warnings.append("This PDF was scanned; its text was read by AI and may contain small errors.")
    if not extracted.segments:
        raise SourceError("This PDF has no readable text.")
    return extracted


async def _transcribe_pdf(provider, ref: MediaRef) -> str:
    from app.services.llm import GeminiProvider

    if not isinstance(provider, GeminiProvider):
        raise SourceError("Reading scanned PDFs needs a Gemini API key.")
    t = provider.types
    prompt = ("Transcribe this PDF completely and faithfully in reading order. Start each page with a line "
              "'=== Page N ==='. Keep headings, lists and tables (as Markdown), code and formulas (LaTeX). "
              "Do not summarise or skip anything.")

    async def run(model):
        cfg = provider._config(system="You transcribe documents faithfully.", max_tokens=65000,
                               timeout=settings.video_analysis_timeout)
        contents = [t.Content(role="user", parts=[provider._media_part(ref), t.Part(text=prompt)])]
        response = await provider.client.aio.models.generate_content(model=model, contents=contents, config=cfg)
        return response.text or ""

    return await provider._call("default", run)


async def _ingest_office(src: dict) -> Extracted:
    data = await asyncio.to_thread(_read_file, src)
    await _astatus(src["id"], "Reading the document…")
    if src["kind"] == "pptx":
        return await asyncio.to_thread(extract_pptx, data, src["title"])
    return await asyncio.to_thread(extract_docx, data, src["title"])


async def _ingest_text_file(src: dict) -> Extracted:
    data = await asyncio.to_thread(_read_file, src)
    text = decode_text(data)
    if Path(src["file_path"]).suffix.lower() in (".html", ".htm"):
        title, body, _ = html_to_text(text)
        return extract_text(body, title or src["title"])
    if Path(src["file_path"]).suffix.lower() == ".ipynb":
        text = _notebook_text(text)
    return extract_text(text, src["title"])


def _notebook_text(raw: str) -> str:
    import json

    try:
        nb = json.loads(raw)
    except ValueError:
        return raw
    out = []
    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") == "code":
            out.append(f"```python\n{source}\n```")
        else:
            out.append(source)
    return "\n\n".join(out)


_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


async def _ingest_subtitles(src: dict) -> Extracted:
    text = decode_text(await asyncio.to_thread(_read_file, src))
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [line for line in block.strip().split("\n") if line.strip()]
        timing = next((line for line in lines if "-->" in line), None)
        if not timing:
            continue
        match = _SRT_TIME.search(timing)
        if not match:
            continue
        h, m, s, ms = (int(x) for x in match.groups())
        start = h * 3600 + m * 60 + s + ms / 1000
        body = " ".join(line for line in lines[lines.index(timing) + 1:])
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body:
            cues.append({"text": body, "start": start, "duration": 0.0})
    if not cues:
        raise SourceError("No subtitle cues were found in this file.")
    cues = clean_transcript(cues)
    return Extracted(title=src["title"], segments=_video_segments(cues, None),
                     meta={"duration": int(cues[-1]["start"]) if cues else 0, "captions": "subtitles"})


async def _ingest_paste(src: dict) -> Extracted:
    return extract_text(src.get("content") or "", src["title"])


async def _ingest_website(src: dict) -> Extracted:
    await _astatus(src["id"], "Fetching the page…")
    result = await fetch(src["url"], public=True)
    ctype = result.content_type
    if "pdf" in ctype or result.url.lower().endswith(".pdf"):
        return await asyncio.to_thread(extract_pdf, result.body, src["title"])
    if not any(t in ctype for t in ("html", "text", "xml", "json")) and ctype:
        raise SourceError(f"That link points to a file type that can't be imported ({ctype.split(';')[0]}).")
    text = decode_body(result.body, ctype)
    if "html" in ctype or "<html" in text[:2000].lower():
        title, body, description = html_to_text(text)
    else:
        title, body, description = "", text, ""
    if len(body) < 200:
        raise SourceError("That page has almost no readable text (it may need JavaScript or a login). "
                          "Try copying the text and using 'Paste text' instead.")
    meta = {"domain": domain_of(result.url), "final_url": result.url}
    if description:
        meta["description"] = description
    return Extracted(title=title or domain_of(result.url), segments=extract_text(body, title).segments, meta=meta)
