"""
AI tutor chat.

One request = one Server-Sent-Events stream:

    status   → the live status line ("Searching your sources…", or the model's
               own thought headings such as "Analyzing Authentication…")
    thought  → thought-summary text for the collapsible "Thoughts" panel
    delta    → answer text as it is written
    sources  → sources created from links pasted into the chat
    done     → the saved message (content, citations, follow-ups)
    error    → a user-safe error

Links pasted into the chat are added as sources (like the reference app):
the stream reports real ingestion progress, then confirms what was added.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import aclosing
from typing import AsyncIterator, Optional

from app import db
from app.models.schemas import ChatSourceReply
from app.services import guide as guide_service
from app.services import jobs, retrieval, sources as source_service
from app.services.llm import AIError, NOT_CONFIGURED_MESSAGE, get_provider
from app.services.prompts import (
    LEARN_TOPIC_PROMPT,
    SOURCE_ADDED_PROMPT,
    SOURCE_ADDED_SYSTEM,
    tutor_system,
)

logger = logging.getLogger(__name__)

HISTORY_TURNS = 10


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Streaming helpers ────────────────────────────────────────────

class FollowupFilter:
    """Pass answer text through, holding back the trailing <followups> block."""

    TAG = "<followups>"

    def __init__(self) -> None:
        self.full = ""
        self.emitted = 0
        self.closed = False

    def feed(self, delta: str) -> str:
        self.full += delta
        if self.closed:
            return ""
        idx = self.full.find(self.TAG)
        if idx != -1:
            self.closed = True
            out = self.full[self.emitted:idx]
            self.emitted = idx
            return out
        # Hold back a possible partial tag at the end.
        safe_end = len(self.full)
        tail = self.full[-len(self.TAG):]
        for i in range(1, len(self.TAG)):
            if tail.endswith(self.TAG[:i]):
                safe_end = len(self.full) - i
                break
        out = self.full[self.emitted:safe_end]
        self.emitted = safe_end
        return out

    def finish(self) -> tuple[str, list[str]]:
        idx = self.full.find(self.TAG)
        answer = self.full if idx == -1 else self.full[:idx]
        followups: list[str] = []
        if idx != -1:
            block = self.full[idx + len(self.TAG):]
            block = block.split("</followups>")[0]
            try:
                data = json.loads(block.strip())
                if isinstance(data, list):
                    followups = [str(x).strip()[:160] for x in data if str(x).strip()][:3]
            except ValueError:
                followups = [q.strip(" -•\"'")[:160] for q in re.findall(r"\"([^\"]+)\"", block)][:3]
        return answer.strip(), followups

    def remainder(self) -> str:
        """Text not yet emitted (when the stream ends without a tag)."""
        if self.closed:
            return ""
        out = self.full[self.emitted:]
        self.emitted = len(self.full)
        return out


_HEADING = re.compile(r"^\s*\*\*(.+?)\*\*")


def thought_status(text: str) -> Optional[str]:
    """Turn a thought-summary chunk into a short status line."""
    match = _HEADING.search(text or "")
    if match:
        heading = match.group(1).strip().rstrip(".:")
        if 2 <= len(heading) <= 80:
            return heading + "…"
    return None


def _history(notebook_id: str, before_id: str) -> list[dict]:
    rows = db.query(
        "messages",
        "SELECT id, role, content, status FROM messages WHERE notebook_id = ? AND status != 'error' "
        "ORDER BY created_at DESC LIMIT ?",
        (notebook_id, HISTORY_TURNS * 2 + 1),
    )
    rows = [r for r in reversed(rows) if r["id"] != before_id]
    turns = []
    for row in rows:
        text = (row["content"] or "").strip()
        if not text:
            continue
        # Strip citation markers from earlier answers: their numbers refer to an older context.
        text = re.sub(r"\[\d{1,4}(?:\s*,\s*\d{1,4})*\]", "", text)
        turns.append({"role": "model" if row["role"] == "assistant" else "user", "text": text[:6000]})
    return turns


def _save_assistant(notebook_id: str, content: str, *, thoughts: str = "", citations: dict | None = None,
                    followups: list | None = None, status: str = "done", meta: dict | None = None) -> dict:
    mid = db.new_id("m")
    db.insert("messages", {
        "id": mid, "notebook_id": notebook_id, "role": "assistant", "content": content, "thoughts": thoughts[:20000],
        "citations": citations or {}, "followups": followups or [], "status": status, "meta": meta or {},
        "created_at": db.now(),
    })
    db.execute("UPDATE notebooks SET updated_at = ? WHERE id = ?", (db.now(), notebook_id))
    return db.get("messages", mid)


def save_user(notebook_id: str, text: str) -> dict:
    mid = db.new_id("m")
    db.insert("messages", {"id": mid, "notebook_id": notebook_id, "role": "user", "content": text,
                           "created_at": db.now()})
    db.log_activity(notebook_id, "chat")
    return db.get("messages", mid)


# ── Main entry ───────────────────────────────────────────────────

async def stream_chat(notebook_id: str, message: str, source_ids: Optional[list[str]] = None,
                      focus: Optional[str] = None) -> AsyncIterator[str]:
    notebook = db.get("notebooks", notebook_id)
    if not notebook:
        yield sse({"type": "error", "message": "Notebook not found."})
        return
    user = save_user(notebook_id, message)
    yield sse({"type": "user", "message": user})
    yield sse({"type": "status", "text": "Getting the gist…"})

    urls = source_service.extract_urls(message)
    if urls:
        async with aclosing(_add_sources_from_chat(notebook_id, message, urls)) as gen:
            async for event in gen:
                yield event
        return

    provider = get_provider()
    if provider is None:
        msg = _save_assistant(notebook_id, NOT_CONFIGURED_MESSAGE, status="error")
        yield sse({"type": "done", "message": msg})
        return

    ready = retrieval.ready_sources(notebook_id, source_ids)
    if not ready and source_ids is None:
        processing = db.query_one("sources", "SELECT COUNT(*) AS n FROM sources WHERE notebook_id = ? "
                                  "AND status IN ('queued','processing')", (notebook_id,))
        any_ready = db.query_one("sources", "SELECT COUNT(*) AS n FROM sources WHERE notebook_id = ? "
                                 "AND status = 'ready'", (notebook_id,))
        if (processing or {}).get("n"):
            yield sse({"type": "status", "text": "Waiting for your sources to finish processing…"})
        elif (any_ready or {}).get("n"):
            msg = _save_assistant(
                notebook_id,
                "No sources are selected. Tick at least one source in the **Sources** panel (or use "
                "**Select all**) so I can answer from your material.",
            )
            yield sse({"type": "done", "message": msg})
            return

    learn = re.match(r"^\s*(?:i\s+(?:want|would like)\s+to\s+)?learn\s+(?:about\s+)?(.{2,200})$", message, re.I)
    if learn and not ready and provider.supports_search:
        async with aclosing(_learn_topic(notebook_id, learn.group(1).strip(" .?!"))) as gen:
            async for event in gen:
                yield event
        return

    async with aclosing(_answer(notebook_id, user, message, source_ids, focus, notebook)) as gen:
        async for event in gen:
            yield event


async def _answer(notebook_id: str, user: dict, message: str, source_ids, focus, notebook) -> AsyncIterator[str]:
    provider = get_provider()
    config = notebook.get("chat_config") or {}
    pro = bool(db.get_setting("pro_mode", False))
    tier = "pro" if pro else "default"

    yield sse({"type": "status", "text": "Searching your sources…"})
    history = _history(notebook_id, user["id"])
    previous_user = next((t["text"] for t in reversed(history) if t["role"] == "user"), "")
    query = f"{message}\n{previous_user}" if len(message.split()) < 6 else message
    ctx = await asyncio.to_thread(retrieval.build_context, notebook_id, source_ids, query)
    has_sources = not ctx.empty
    if has_sources:
        yield sse({"type": "status", "text": "Searched your sources"})

    question = message
    if focus:
        question = f"Explain this part of my material:\n\"\"\"\n{focus[:3000]}\n\"\"\"\n\n{message}"
    turns = history[-HISTORY_TURNS * 2:]
    final_prompt = (f"{ctx.text}\n\nStudent's question:\n{question}" if has_sources else question)
    turns.append({"role": "user", "text": final_prompt})
    system = tutor_system(config, has_sources=has_sources)

    filt = FollowupFilter()
    thoughts = ""
    last_status = ""
    upstream = provider.stream(system=system, contents=turns, tier=tier, thinking=True)
    try:
        async for event in upstream:
            if event.kind == "thought":
                thoughts += event.text
                status = thought_status(event.text)
                if status and status != last_status:
                    last_status = status
                    yield sse({"type": "status", "text": status})
                yield sse({"type": "thought", "text": event.text})
            else:
                out = filt.feed(event.text)
                if out:
                    yield sse({"type": "delta", "text": out})
        tail = filt.remainder()
        if tail:
            yield sse({"type": "delta", "text": tail})
    except (asyncio.CancelledError, GeneratorExit):
        # The student pressed stop (or closed the page): keep what was written.
        answer, _ = filt.finish()
        if answer:
            _save_assistant(notebook_id, answer, thoughts=thoughts,
                            citations=retrieval.citation_map(answer, ctx.index), status="stopped")
        raise
    except AIError as err:
        answer, _ = filt.finish()
        content = (answer + "\n\n" if answer else "") + f"⚠️ {err.message}"
        msg = _save_assistant(notebook_id, content, thoughts=thoughts, status="error",
                              citations=retrieval.citation_map(answer, ctx.index))
        yield sse({"type": "done", "message": msg})
        return
    finally:
        await upstream.aclose()

    answer, followups = filt.finish()
    if not answer:
        answer = "I couldn't produce an answer this time. Please try rephrasing your question."
    citations = retrieval.citation_map(answer, ctx.index)
    meta = {"model": getattr(provider, "last_model", ""), "context_truncated": ctx.truncated}
    msg = _save_assistant(notebook_id, answer, thoughts=thoughts, citations=citations, followups=followups, meta=meta)
    yield sse({"type": "done", "message": msg})


# ── Links pasted into the chat ───────────────────────────────────

async def _add_sources_from_chat(notebook_id: str, message: str, urls: list[str]) -> AsyncIterator[str]:
    yield sse({"type": "status", "text": "Analyzing URL input…"})
    created, errors = [], []
    for url in urls:
        try:
            created.append(source_service.add_url(notebook_id, url))
        except source_service.SourceError as exc:
            errors.append(f"{url}: {exc}")
    if created:
        yield sse({"type": "sources", "sources": created})
        yield sse({"type": "status", "text": "Adding sources…"})

    # Follow the real ingestion progress.
    last = ""
    pending = {s["id"] for s in created}
    ticks = 0
    while pending and ticks < 45 * 60 * 5 // 4:
        await asyncio.sleep(0.8)
        ticks += 1
        for sid in list(pending):
            row = db.get("sources", sid)
            if row is None or row["status"] in ("ready", "error"):
                pending.discard(sid)
                continue
            text = row["status_message"] or "Adding sources…"
            if text != last:
                last = text
                yield sse({"type": "status", "text": text})
        if ticks % 20 == 0:
            yield ": keep-alive\n\n"

    rows = [db.get("sources", s["id"]) for s in created]
    ready = [r for r in rows if r and r["status"] == "ready"]
    failed = [r for r in rows if r and r["status"] == "error"]
    for row in failed:
        errors.append(f"**{row['title']}** — {row['error']}")

    if ready:
        yield sse({"type": "status", "text": "Reviewing the source…"})
        await jobs.wait_for(guide_service.guide_key(notebook_id), timeout=90)

    remaining = source_service.URL_RE.sub("", message).strip()
    is_question = len(remaining.split()) >= 4 and remaining.lower() not in ("add this", "add these")

    notebook = db.get("notebooks", notebook_id)
    provider = get_provider()
    if ready and is_question and provider is not None:
        user = {"id": ""}
        async with aclosing(_answer(notebook_id, user, remaining, None, None, notebook)) as gen:
            async for event in gen:
                yield event
        return

    followups: list[str] = list((notebook or {}).get("questions") or [])
    if ready:
        kinds = {"youtube": "YouTube video", "website": "web page"}
        titles = ", ".join(f"{kinds.get(r['kind'], 'source')} \"{r['title']}\"" for r in ready)
        content = ""
        if provider is not None:
            try:
                summary = " ".join(((r.get("guide") or {}).get("summary") or "")[:600] for r in ready)
                result = await provider.json(
                    system=SOURCE_ADDED_SYSTEM,
                    prompt=SOURCE_ADDED_PROMPT.format(titles=titles, summary=summary or "(no summary)"),
                    schema=ChatSourceReply, tier="lite", max_tokens=2000,
                )
                content = result.reply.strip()
                followups = [q.strip() for q in result.suggested_questions if q.strip()][:3] or followups
            except AIError:
                content = ""
        if not content:
            names = ", ".join(f"**\"{r['title']}\"**" for r in ready)
            content = (f"I've added {names} to your notebook sources.\n\nYou can now ask questions about it, "
                       "request a detailed overview, or create study material from the **Studio** panel.")
            if provider is None:
                content += f"\n\n⚠️ {NOT_CONFIGURED_MESSAGE}"
        warnings = [w for r in ready for w in (r.get("warnings") or [])]
        if warnings:
            content += "\n\n" + "\n".join(f"> ⚠️ {w}" for w in warnings[:3])
        if errors:
            content += "\n\nSome links couldn't be added:\n" + "\n".join(f"- {e}" for e in errors)
        msg = _save_assistant(notebook_id, content, followups=followups[:3])
    else:
        content = "I couldn't add that link to your notebook.\n\n" + "\n".join(f"- {e}" for e in errors)
        content += ("\n\nIf it's a YouTube video, check that it is public. You can also upload the video file, "
                    "or paste the transcript with **Add sources → Paste text**.")
        msg = _save_assistant(notebook_id, content, status="error")
    yield sse({"type": "done", "message": msg})


async def _learn_topic(notebook_id: str, topic: str) -> AsyncIterator[str]:
    provider = get_provider()
    yield sse({"type": "status", "text": "Searching the web for sources…"})
    results: list[dict] = []
    try:
        web, videos = await asyncio.gather(
            provider.web_search(topic), provider.web_search(topic, youtube_only=True), return_exceptions=True
        )
        for block in (videos, web):
            if isinstance(block, dict):
                for item in block.get("results", []):
                    if item["url"] not in {r["url"] for r in results}:
                        results.append(item)
    except AIError:
        results = []
    yield sse({"type": "status", "text": "Planning your learning path…"})
    try:
        text = await provider.text(system=tutor_system({}, has_sources=False),
                                   prompt=LEARN_TOPIC_PROMPT.format(topic=topic[:200]), max_tokens=3000)
    except AIError as err:
        msg = _save_assistant(notebook_id, f"⚠️ {err.message}", status="error")
        yield sse({"type": "done", "message": msg})
        return
    filt = FollowupFilter()
    filt.feed(text)
    answer, followups = filt.finish()
    msg = _save_assistant(notebook_id, answer, followups=followups,
                          meta={"web_results": results[:8], "topic": topic})
    yield sse({"type": "done", "message": msg})
