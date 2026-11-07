"""
Studio — every tool in the right-hand panel produces a real, saved artifact.

    notes        Study Notes in six styles (+ practice questions)
    report       Briefing doc, study guide, blog post, FAQ, timeline, custom
    flashcards   Q/A cards with difficulty and topic
    quiz         MCQs with explanations for every option
    mindmap      Concept tree with cross-links
    infographic  One-page visual summary
    slides       Presentation deck with speaker notes
    datatable    Structured comparison / glossary / timeline tables
    audio        Two-host Audio Overview (script + multi-speaker TTS)
    video        Video Overview: narrated scenes played as a lesson

Generation runs as a background job; long text artifacts are streamed into
the database while they are written so the UI can show them filling in.
All model output is re-validated and clamped before it is stored.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from app import db
from app.config import settings
from app.models.schemas import (
    AudioScript,
    DataTable,
    FlashcardSet,
    Infographic,
    MindMap,
    QuizSet,
    SlideDeck,
    VideoScript,
)
from app.services import jobs, retrieval
from app.services.chat import FollowupFilter
from app.services.llm import AIError, AudioClip, get_provider, require_provider, wav_bytes
from app.services.prompts import (
    AUDIO_FORMATS,
    AUDIO_PROMPT,
    DATATABLE_KINDS,
    DATATABLE_PROMPT,
    FLASHCARD_PROMPT,
    INFOGRAPHIC_ICONS,
    INFOGRAPHIC_PROMPT,
    MINDMAP_PROMPT,
    NOTE_PROMPT,
    NOTE_STYLES,
    QUIZ_PROMPT,
    REPORT_FORMATS,
    SLIDES_PROMPT,
    STUDIO_SYSTEM,
    VIDEO_PROMPT,
)

logger = logging.getLogger(__name__)

TYPES = {
    "audio": "Audio Overview",
    "video": "Video Overview",
    "mindmap": "Mind Map",
    "report": "Report",
    "flashcards": "Flashcards",
    "quiz": "Quiz",
    "infographic": "Infographic",
    "slides": "Slide Deck",
    "datatable": "Data Table",
    "notes": "Study Notes",
}

LANGUAGES = {
    "hi": "Hindi (हिन्दी)", "bn": "Bengali (বাংলা)", "gu": "Gujarati (ગુજરાતી)", "kn": "Kannada (ಕನ್ನಡ)",
    "ml": "Malayalam (മലയാളം)", "mr": "Marathi (मराठी)", "pa": "Punjabi (ਪੰਜਾਬੀ)", "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)", "en": "English",
}

HOSTS = ("Alex", "Sam")
HOST_VOICES = {"Alex": "Puck", "Sam": "Kore"}
NARRATOR_VOICE = "Charon"


class StudioError(ValueError):
    pass


# ── Option helpers ───────────────────────────────────────────────

def _language(options: dict) -> str:
    raw = str(options.get("language") or "").strip()
    if not raw:
        return "English"
    return LANGUAGES.get(raw.lower(), raw[:40])


def _lang_line(options: dict) -> str:
    lang = _language(options)
    return "Write in English." if lang == "English" else f"Write everything in {lang} (keep technical terms in English where clearer)."


def _focus_line(options: dict) -> str:
    focus = str(options.get("focus") or "").strip()[:500]
    return f"Focus especially on: {focus}." if focus else "Cover the material as a whole."


def _count(options: dict, mapping: dict, default: str) -> int:
    value = options.get("count", default)
    if isinstance(value, int):
        return max(1, min(value, max(mapping.values()) * 2))
    return mapping.get(str(value), mapping[default])


def _difficulty(options: dict) -> str:
    d = str(options.get("difficulty") or "mixed").lower()
    return d if d in ("easy", "medium", "hard", "mixed") else "mixed"


# ── Starting a job ───────────────────────────────────────────────

def default_title(kind: str, options: dict) -> str:
    if kind == "notes":
        return NOTE_STYLES.get(str(options.get("style") or "comprehensive"), NOTE_STYLES["comprehensive"])["label"]
    if kind == "report":
        return REPORT_FORMATS.get(str(options.get("format") or "briefing"), REPORT_FORMATS["briefing"])["label"]
    if kind == "audio":
        lang = _language(options)
        return "Audio Overview" + ("" if lang == "English" else f" · {lang.split(' (')[0]}")
    return TYPES[kind]


def start(notebook_id: str, kind: str, options: dict, source_ids: Optional[list[str]]) -> dict:
    if kind not in TYPES:
        raise StudioError("Unknown Studio tool.")
    require_provider()
    sources = retrieval.ready_sources(notebook_id, source_ids)
    if not sources:
        raise StudioError("Add a source (and make sure it's selected) before creating study material.")
    provider = get_provider()
    if kind in ("audio", "video") and not provider.supports_tts:
        raise StudioError("Audio and Video Overviews need a Gemini API key (text-to-speech).")
    aid = db.new_id("a")
    ts = db.now()
    db.insert("artifacts", {
        "id": aid, "notebook_id": notebook_id, "type": kind, "title": default_title(kind, options),
        "status": "processing", "status_message": "Reading your sources…", "options": options,
        "source_ids": [s["id"] for s in sources], "created_at": ts, "updated_at": ts,
    })
    jobs.submit(f"artifact:{aid}", lambda: run(aid))
    return db.get("artifacts", aid)


def _progress(aid: str, message: str) -> None:
    db.update("artifacts", aid, {"status_message": message[:200], "updated_at": db.now()})


async def run(aid: str) -> None:
    art = db.get("artifacts", aid)
    if not art:
        return
    kind = art["type"]
    options = art.get("options") or {}
    try:
        provider = require_provider()
        tier = "pro" if db.get_setting("pro_mode", False) else "default"
        focus = str(options.get("focus") or "")
        ctx = await asyncio.to_thread(retrieval.build_context, art["notebook_id"], art.get("source_ids"), focus)
        if ctx.empty:
            raise StudioError("The selected sources have no content yet.")
        generator = GENERATORS[kind]
        result = await generator(provider, ctx, options, aid, tier)
        values = {
            "title": (result.get("title") or art["title"])[:200],
            "content": result["content"],
            "citations": result.get("citations") or {},
            "status": "ready",
            "status_message": "",
            "error": "",
            "updated_at": db.now(),
        }
        if result.get("file_path"):
            values["file_path"] = result["file_path"]
        db.update("artifacts", aid, values)
        db.log_activity(art["notebook_id"], f"studio:{kind}")
    except asyncio.CancelledError:
        db.update("artifacts", aid, {"status": "error", "error": "Generation was cancelled.", "status_message": ""})
        raise
    except (StudioError, AIError) as exc:
        message = exc.message if isinstance(exc, AIError) else str(exc)
        db.update("artifacts", aid, {"status": "error", "error": message[:600], "status_message": ""})
    except Exception:  # noqa: BLE001
        logger.exception("studio generation failed", extra={"artifact_type": kind})
        db.update("artifacts", aid, {"status": "error", "status_message": "",
                                     "error": "Something went wrong while generating this. Please try again."})


# ── Text documents (notes, reports) ──────────────────────────────

async def _stream_document(provider, prompt: str, aid: str, tier: str, base: dict) -> tuple[str, str]:
    text = ""
    last_save = 0.0
    _progress(aid, "Writing…")
    async for event in provider.stream(system=STUDIO_SYSTEM, contents=[{"role": "user", "text": prompt}],
                                       tier=tier, thinking=False, max_tokens=60000):
        if event.kind != "text":
            continue
        text += event.text
        now = time.monotonic()
        if now - last_save > 1.2:
            last_save = now
            db.update("artifacts", aid, {"content": {**base, "markdown": text, "partial": True},
                                         "status_message": f"Writing… ({len(text.split())} words)"})
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", r"\1", text, flags=re.S)
    filt = FollowupFilter()
    filt.feed(text)
    text, _ = filt.finish()
    if len(text) < 200:
        raise StudioError("The AI returned an incomplete document. Please try again.")
    match = re.search(r"^#\s+(.+)$", text, re.M)
    title = match.group(1).strip()[:150] if match else ""
    return text, title


async def gen_notes(provider, ctx, options, aid, tier):
    style_key = str(options.get("style") or "comprehensive")
    style = NOTE_STYLES.get(style_key, NOTE_STYLES["comprehensive"])
    prompt = NOTE_PROMPT.format(context=ctx.text, instructions=style["instructions"],
                                focus=_focus_line(options), language=_lang_line(options))
    base = {"style": style_key, "label": style["label"]}
    text, title = await _stream_document(provider, prompt, aid, tier, base)
    return {"title": title or style["label"], "content": {**base, "markdown": text},
            "citations": retrieval.citation_map(text, ctx.index)}


async def gen_report(provider, ctx, options, aid, tier):
    fmt_key = str(options.get("format") or "briefing")
    fmt = REPORT_FORMATS.get(fmt_key, REPORT_FORMATS["briefing"])
    instructions = fmt["instructions"]
    if fmt_key == "custom":
        custom = str(options.get("prompt") or "").strip()[:2000]
        if not custom:
            raise StudioError("Describe the report you want.")
        instructions += f"\n\nThe student's description:\n{custom}"
    prompt = NOTE_PROMPT.format(context=ctx.text, instructions=instructions, focus=_focus_line(options),
                                language=_lang_line(options))
    base = {"format": fmt_key, "label": fmt["label"]}
    text, title = await _stream_document(provider, prompt, aid, tier, base)
    return {"title": title or fmt["label"], "content": {**base, "markdown": text},
            "citations": retrieval.citation_map(text, ctx.index)}


# ── Flashcards / quiz ────────────────────────────────────────────

async def gen_flashcards(provider, ctx, options, aid, tier):
    count = _count(options, {"fewer": 12, "standard": 20, "more": 35}, "standard")
    difficulty = _difficulty(options)
    _progress(aid, f"Writing {count} flashcards…")
    prompt = FLASHCARD_PROMPT.format(
        context=ctx.text, count=count,
        difficulty="mix easy, medium and hard" if difficulty == "mixed" else f"mostly {difficulty}",
        focus=_focus_line(options), language=_lang_line(options))
    deck = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=FlashcardSet, tier=tier, max_tokens=24000)
    cards = []
    for card in deck.cards:
        front, back = card.front.strip(), card.back.strip()
        if not front or not back:
            continue
        diff = card.difficulty.lower().strip()
        cards.append({"front": front[:600], "back": back[:1500],
                      "difficulty": diff if diff in ("easy", "medium", "hard") else "medium",
                      "topic": card.topic.strip()[:80], "hint": card.hint.strip()[:200]})
    if len(cards) < 3:
        raise StudioError("The AI didn't produce enough flashcards. Please try again.")
    return {"title": deck.title.strip()[:150] or "Flashcards", "content": {"cards": cards[:60]}}


async def gen_quiz(provider, ctx, options, aid, tier):
    count = _count(options, {"fewer": 5, "standard": 10, "more": 20}, "standard")
    difficulty = _difficulty(options)
    _progress(aid, f"Writing {count} questions…")
    prompt = QUIZ_PROMPT.format(
        context=ctx.text, count=count,
        difficulty="a mix of easy, medium and hard" if difficulty == "mixed" else difficulty,
        focus=_focus_line(options), language=_lang_line(options))
    quiz = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=QuizSet, tier=tier, max_tokens=24000)
    questions = []
    for q in quiz.questions:
        opts = [o.strip()[:300] for o in q.options if o and o.strip()]
        if len(opts) < 2 or not q.question.strip():
            continue
        answer = q.answer_index if 0 <= q.answer_index < len(opts) else None
        if answer is None:
            continue
        if len(opts) > 4:
            keep = [answer] + [i for i in range(len(opts)) if i != answer][:3]
            keep.sort()
            answer = keep.index(answer)
            opts = [opts[i] for i in keep]
        diff = q.difficulty.lower().strip()
        questions.append({"question": q.question.strip()[:600], "options": opts, "answer": answer,
                          "explanation": q.explanation.strip()[:1500],
                          "difficulty": diff if diff in ("easy", "medium", "hard") else "medium",
                          "topic": q.topic.strip()[:80], "hint": q.hint.strip()[:200]})
    if len(questions) < 2:
        raise StudioError("The AI didn't produce a usable quiz. Please try again.")
    return {"title": quiz.title.strip()[:150] or "Quiz", "content": {"questions": questions[:40]}}


# ── Mind map ─────────────────────────────────────────────────────

def build_tree(mind: MindMap) -> dict:
    nodes: dict[str, dict] = {}
    for n in mind.nodes:
        nid = (n.id or "").strip()[:40]
        if not nid or nid in nodes or not n.label.strip():
            continue
        nodes[nid] = {"id": nid, "label": n.label.strip()[:80], "note": n.note.strip()[:300],
                      "parent": (n.parent_id or "").strip()[:40], "children": []}
    if not nodes:
        raise StudioError("The AI returned an empty mind map. Please try again.")
    roots = [n for n in nodes.values() if not n["parent"] or n["parent"] not in nodes or n["parent"] == n["id"]]
    root = next((n for n in roots if not n["parent"]), roots[0] if roots else next(iter(nodes.values())))
    if not root.get("label"):
        root["label"] = mind.title
    for n in nodes.values():
        if n is root:
            continue
        parent_id = n["parent"] if n["parent"] in nodes and n["parent"] != n["id"] else root["id"]
        # Break cycles: walk up; if we come back to n, attach to root.
        seen, cur = {n["id"]}, parent_id
        while cur and cur != root["id"]:
            if cur in seen:
                parent_id = root["id"]
                break
            seen.add(cur)
            cur = nodes[cur]["parent"] if nodes[cur]["parent"] in nodes else root["id"]
        n["parent"] = parent_id
    for n in nodes.values():
        if n is not root:
            nodes[n["parent"]]["children"].append(n)

    def strip(node, depth=0):
        out = {"id": node["id"], "label": node["label"], "note": node["note"]}
        if depth < 5 and node["children"]:
            out["children"] = [strip(c, depth + 1) for c in node["children"]]
        return out

    links = [
        {"from": link.from_id, "to": link.to_id, "label": link.label.strip()[:30]}
        for link in mind.links if link.from_id in nodes and link.to_id in nodes and link.from_id != link.to_id
    ][:12]
    return {"root": strip(root), "links": links}


async def gen_mindmap(provider, ctx, options, aid, tier):
    _progress(aid, "Mapping the concepts…")
    prompt = MINDMAP_PROMPT.format(context=ctx.text, focus=_focus_line(options), language=_lang_line(options))
    mind = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=MindMap, tier=tier, max_tokens=16000)
    tree = build_tree(mind)
    return {"title": mind.title.strip()[:150] or tree["root"]["label"], "content": tree}


# ── Infographic ──────────────────────────────────────────────────

async def gen_infographic(provider, ctx, options, aid, tier):
    _progress(aid, "Designing the infographic…")
    prompt = INFOGRAPHIC_PROMPT.format(context=ctx.text, icons=", ".join(INFOGRAPHIC_ICONS),
                                       focus=_focus_line(options), language=_lang_line(options))
    info = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=Infographic, tier=tier, max_tokens=8000)
    sections = []
    for sec in info.sections[:6]:
        icon = sec.icon.strip().lower().replace(" ", "_")
        sections.append({"heading": sec.heading.strip()[:80],
                         "icon": icon if icon in INFOGRAPHIC_ICONS else "lightbulb",
                         "points": [p.strip()[:160] for p in sec.points if p.strip()][:4],
                         "stat_value": sec.stat_value.strip()[:24], "stat_label": sec.stat_label.strip()[:60]})
    if len(sections) < 2:
        raise StudioError("The AI didn't produce enough content for an infographic. Please try again.")
    content = {
        "title": info.title.strip()[:100], "subtitle": info.subtitle.strip()[:200],
        "stats": [{"value": s.value.strip()[:24], "label": s.label.strip()[:80]} for s in info.key_stats][:4],
        "sections": sections,
        "steps_title": info.steps_title.strip()[:80],
        "steps": [s.strip()[:120] for s in info.steps if s.strip()][:7],
        "takeaway": info.takeaway.strip()[:300],
        "theme": int(time.time()) % 4,
    }
    return {"title": content["title"] or "Infographic", "content": content}


# ── Slides ───────────────────────────────────────────────────────

_LAYOUTS = {"title", "bullets", "two_column", "code", "quote", "summary"}


async def gen_slides(provider, ctx, options, aid, tier):
    count = _count(options, {"fewer": 7, "standard": 11, "more": 16}, "standard")
    _progress(aid, f"Building {count} slides…")
    prompt = SLIDES_PROMPT.format(context=ctx.text, count=count, focus=_focus_line(options),
                                  language=_lang_line(options))
    deck = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=SlideDeck, tier=tier, max_tokens=24000)
    slides = []
    for s in deck.slides:
        layout = s.layout.strip().lower().replace("-", "_")
        if layout not in _LAYOUTS:
            layout = "bullets"
        if layout == "code" and not s.code.strip():
            layout = "bullets"
        if layout == "two_column" and not (s.left or s.right):
            layout = "bullets"
        slides.append({
            "layout": layout, "title": s.title.strip()[:120], "subtitle": s.subtitle.strip()[:200],
            "bullets": [b.strip()[:220] for b in s.bullets if b.strip()][:7],
            "left_title": s.left_title.strip()[:60], "left": [b.strip()[:200] for b in s.left if b.strip()][:6],
            "right_title": s.right_title.strip()[:60], "right": [b.strip()[:200] for b in s.right if b.strip()][:6],
            "code": s.code[:2500], "code_language": s.code_language.strip()[:20],
            "quote": s.quote.strip()[:400], "notes": s.notes.strip()[:1500],
        })
    if len(slides) < 3:
        raise StudioError("The AI didn't produce a usable slide deck. Please try again.")
    return {"title": deck.title.strip()[:150] or "Slide Deck",
            "content": {"title": deck.title.strip()[:150], "subtitle": deck.subtitle.strip()[:200], "slides": slides}}


# ── Data table ───────────────────────────────────────────────────

async def gen_datatable(provider, ctx, options, aid, tier):
    kind_key = str(options.get("kind") or "concepts")
    kind = DATATABLE_KINDS.get(kind_key, DATATABLE_KINDS["concepts"])
    if kind_key == "custom":
        kind = str(options.get("prompt") or "").strip()[:800]
        if not kind:
            raise StudioError("Describe the table you want.")
    _progress(aid, "Building the table…")
    prompt = DATATABLE_PROMPT.format(context=ctx.text, kind=kind, focus=_focus_line(options),
                                     language=_lang_line(options))
    table = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=DataTable, tier=tier, max_tokens=16000)
    columns = [c.strip()[:60] for c in table.columns if c.strip()][:8]
    if len(columns) < 2:
        raise StudioError("The AI didn't produce a usable table. Please try again.")
    rows = []
    for row in table.rows[:60]:
        cells = [str(c).strip()[:400] or "—" for c in row][: len(columns)]
        cells += ["—"] * (len(columns) - len(cells))
        if any(c != "—" for c in cells):
            rows.append(cells)
    return {"title": table.title.strip()[:150] or "Data Table",
            "content": {"description": table.description.strip()[:400], "columns": columns, "rows": rows,
                        "notes": table.notes.strip()[:600]}}


# ── Audio Overview ───────────────────────────────────────────────

def _batches(turns: list[tuple[str, str]], limit: int = 1500) -> list[list[tuple[str, str]]]:
    batches, cur, size = [], [], 0
    for speaker, text in turns:
        if cur and size + len(text) > limit:
            batches.append(cur)
            cur, size = [], 0
        cur.append((speaker, text))
        size += len(text)
    if cur:
        batches.append(cur)
    return batches


def _media_path(aid: str) -> Path:
    settings.ensure_dirs()
    return settings.media_dir / f"{aid}.wav"


async def _tts_many(provider, jobs_list, aid: str, label: str):
    sem = asyncio.Semaphore(3)
    done = 0

    async def one(job):
        nonlocal done
        turns, voices = job
        async with sem:
            clip = await provider.tts(turns, voices)
        done += 1
        _progress(aid, f"{label} ({done}/{len(jobs_list)})…")
        return clip

    return await asyncio.gather(*(one(j) for j in jobs_list))


async def gen_audio(provider, ctx, options, aid, tier):
    fmt_key = str(options.get("format") or "deep_dive")
    fmt_label, fmt_desc = AUDIO_FORMATS.get(fmt_key, AUDIO_FORMATS["deep_dive"])
    language = _language(options)
    _progress(aid, "Writing the conversation…")
    prompt = AUDIO_PROMPT.format(context=ctx.text, format_desc=fmt_desc, host=HOSTS[0], expert=HOSTS[1],
                                 focus=_focus_line(options), language=language)
    script = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=AudioScript, tier=tier, max_tokens=24000)
    turns: list[tuple[str, str]] = []
    for turn in script.turns:
        text = re.sub(r"[*_#`]+", "", turn.text).strip()
        if not text:
            continue
        name = turn.speaker.strip()
        speaker = HOSTS[0] if name.lower().startswith(HOSTS[0].lower()) else (
            HOSTS[1] if name.lower().startswith(HOSTS[1].lower()) else (HOSTS[len(turns) % 2]))
        turns.append((speaker, text[:1200]))
    if len(turns) < 4:
        raise StudioError("The AI didn't produce a usable script. Please try again.")
    batches = _batches(turns)
    clips: list[AudioClip] = await _tts_many(provider, [(b, HOST_VOICES) for b in batches], aid, "Recording audio")
    wav, offsets = await asyncio.to_thread(wav_bytes, clips, 0.15)
    path = _media_path(aid)
    await asyncio.to_thread(path.write_bytes, wav)
    # Approximate each line's start time inside its batch by character share.
    timeline = []
    for batch, clip, start in zip(batches, clips, offsets):
        total = sum(len(t) for _, t in batch) or 1
        t = start
        for speaker, text in batch:
            timeline.append({"speaker": speaker, "text": text, "start": round(t, 2)})
            t += clip.seconds * len(text) / total
    duration = offsets[-1] + clips[-1].seconds if offsets else 0
    title = script.title.strip()[:150] or f"Audio Overview"
    return {
        "title": title,
        "content": {"format": fmt_key, "format_label": fmt_label, "language": language,
                    "summary": script.summary.strip()[:300], "turns": timeline, "duration": round(duration, 1)},
        "file_path": str(path),
    }


# ── Video Overview ───────────────────────────────────────────────

async def gen_video(provider, ctx, options, aid, tier):
    count = _count(options, {"fewer": 5, "standard": 8, "more": 12}, "standard")
    language = _language(options)
    _progress(aid, "Writing the lesson script…")
    prompt = VIDEO_PROMPT.format(context=ctx.text, count=count, focus=_focus_line(options), language=language)
    script = await provider.json(system=STUDIO_SYSTEM, prompt=prompt, schema=VideoScript, tier=tier, max_tokens=20000)
    scenes = []
    for scene in script.scenes:
        narration = re.sub(r"[*_#`]+", "", scene.narration).strip()
        if not narration:
            continue
        scenes.append({"title": scene.title.strip()[:100],
                       "bullets": [b.strip()[:140] for b in scene.bullets if b.strip()][:5],
                       "narration": narration[:1500], "visual": scene.visual.strip()[:120]})
    if len(scenes) < 2:
        raise StudioError("The AI didn't produce a usable video script. Please try again.")
    clips = await _tts_many(provider, [([("Narrator", s["narration"])], {"Narrator": NARRATOR_VOICE})
                                       for s in scenes], aid, "Narrating scenes")
    wav, offsets = await asyncio.to_thread(wav_bytes, clips, 0.6)
    path = _media_path(aid)
    await asyncio.to_thread(path.write_bytes, wav)
    for scene, start, clip in zip(scenes, offsets, clips):
        scene["start"] = round(start, 2)
        scene["duration"] = round(clip.seconds, 2)
    duration = offsets[-1] + clips[-1].seconds
    return {"title": script.title.strip()[:150] or "Video Overview",
            "content": {"language": language, "scenes": scenes, "duration": round(duration, 1)},
            "file_path": str(path)}


GENERATORS = {
    "notes": gen_notes,
    "report": gen_report,
    "flashcards": gen_flashcards,
    "quiz": gen_quiz,
    "mindmap": gen_mindmap,
    "infographic": gen_infographic,
    "slides": gen_slides,
    "datatable": gen_datatable,
    "audio": gen_audio,
    "video": gen_video,
}
