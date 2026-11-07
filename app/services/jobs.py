"""
Background jobs.

Processing a lecture video or generating an Audio Overview can take minutes,
so that work runs as asyncio tasks inside the server process while the API
returns immediately. Progress lives in the database (status / status_message
columns), which the UI polls, so a page reload never loses track of a job.

A semaphore bounds how many jobs run at once. Jobs left "processing" by a
server restart are marked as interrupted on boot so they can be retried.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app import db
from app.config import settings

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()
_keyed: dict[str, asyncio.Task] = {}
_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    return _semaphore


def submit(key: str, factory: Callable[[], Awaitable[None]], *, limited: bool = True) -> asyncio.Task:
    """Start a background job. A job with the same key replaces a pending one."""
    previous = _keyed.get(key)
    if previous is not None and not previous.done():
        previous.cancel()

    async def runner():
        try:
            if limited:
                async with _sem():
                    await factory()
            else:
                await factory()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - jobs record their own errors; this is a backstop
            logger.exception("background job crashed", extra={"job": key.split(":")[0]})

    task = asyncio.create_task(runner(), name=key)
    _tasks.add(task)
    _keyed[key] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if _keyed.get(key) is t:
            _keyed.pop(key, None)

    task.add_done_callback(_done)
    return task


def cancel(key: str) -> None:
    task = _keyed.get(key)
    if task is not None and not task.done():
        task.cancel()


def is_running(key: str) -> bool:
    task = _keyed.get(key)
    return task is not None and not task.done()


async def wait_for(key: str, timeout: float | None = None) -> None:
    task = _keyed.get(key)
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass


def recover_interrupted() -> None:
    """Mark work that was mid-flight when the server stopped."""
    msg = "Processing was interrupted (the server restarted). Click retry."
    db.execute(
        "UPDATE sources SET status = 'error', error = ?, status_message = '' "
        "WHERE status IN ('queued', 'processing')",
        (msg,),
    )
    db.execute(
        "UPDATE artifacts SET status = 'error', error = ?, status_message = '' WHERE status = 'processing'",
        ("Generation was interrupted (the server restarted). Please generate it again.",),
    )
    db.execute("UPDATE notebooks SET guide_status = 'idle' WHERE guide_status = 'processing'")


async def shutdown() -> None:
    for task in list(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
