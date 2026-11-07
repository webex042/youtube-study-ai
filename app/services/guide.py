"""
Notebook guide — the overview card at the top of the chat.

Whenever the set of ready sources changes, the AI writes a notebook title
(while the student hasn't renamed it), an emoji, a summary with the key terms
in bold, and three suggested questions. Requests are debounced so adding
several sources at once triggers a single generation.
"""

from __future__ import annotations

import asyncio
import logging

from app import db
from app.models.schemas import NotebookGuide
from app.services import jobs, retrieval
from app.services.llm import AIError, get_provider
from app.services.prompts import NOTEBOOK_GUIDE_PROMPT, NOTEBOOK_GUIDE_SYSTEM

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "Untitled notebook"


def schedule(notebook_id: str, delay: float = 1.2) -> None:
    async def run():
        await asyncio.sleep(delay)
        await generate(notebook_id)

    jobs.submit(f"guide:{notebook_id}", run, limited=False)


def guide_key(notebook_id: str) -> str:
    return f"guide:{notebook_id}"


async def generate(notebook_id: str) -> None:
    notebook = db.get("notebooks", notebook_id)
    if not notebook:
        return
    sources = db.query(
        "sources", "SELECT id FROM sources WHERE notebook_id = ? AND status = 'ready' ORDER BY position",
        (notebook_id,),
    )
    if not sources:
        db.update("notebooks", notebook_id, {"summary": "", "questions": [], "guide_status": "idle",
                                             "guide_error": ""})
        return
    provider = get_provider()
    if provider is None:
        db.update("notebooks", notebook_id, {"guide_status": "idle"})
        return
    db.update("notebooks", notebook_id, {"guide_status": "processing", "guide_error": ""})
    try:
        ctx = retrieval.build_context(notebook_id, [s["id"] for s in sources], budget=160_000)
        result = await provider.json(
            system=NOTEBOOK_GUIDE_SYSTEM,
            prompt=NOTEBOOK_GUIDE_PROMPT.format(context=ctx.text),
            schema=NotebookGuide,
            tier="lite",
            max_tokens=4000,
        )
        values = {
            "summary": result.summary.strip()[:3000],
            "questions": [q.strip()[:200] for q in result.suggested_questions if q.strip()][:3],
            "emoji": (result.emoji or "").strip()[:8],
            "guide_status": "ready",
            "updated_at": db.now(),
        }
        current = db.get("notebooks", notebook_id)
        if current and current["title_auto"] and result.title.strip():
            values["title"] = result.title.strip().strip('"')[:120]
        db.update("notebooks", notebook_id, values)
    except AIError as err:
        db.update("notebooks", notebook_id, {"guide_status": "error", "guide_error": err.message[:400]})
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("notebook guide failed")
        db.update("notebooks", notebook_id, {"guide_status": "error",
                                             "guide_error": "Couldn't generate the notebook overview."})
