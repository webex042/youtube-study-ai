"""
Rate Limiting & Concurrency Control

/api/study is expensive: it fetches a transcript and then makes several LLM
calls, each of which costs real money and real tokens. Disabling a button in
the browser is not a control, so the ceiling is enforced here, server-side.

Implementation is an in-memory sliding-window counter. That is the right fit
for a single-process deployment: it is exact within the window, needs no
extra dependency, and fails closed if the store is exhausted. Behind
multiple workers each process keeps its own window, so the effective limit
is per-worker — noted in the README, and the point at which a shared store
(Redis) would be the correct upgrade.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import Request

from app.config import settings

# Cap on how many distinct clients we track, so the limiter itself cannot be
# used as a memory-exhaustion vector.
_MAX_TRACKED_CLIENTS = 20_000


@dataclass
class _Window:
    """Timestamps of recent hits for one client/bucket pair."""

    hits: deque[float] = field(default_factory=deque)


class SlidingWindowLimiter:
    """
    Fixed-capacity sliding-window limiter, safe for concurrent use.

    Each (client, bucket) pair gets a deque of hit timestamps. Entries older
    than the window are discarded on access, so memory tracks active clients
    rather than total clients ever seen.
    """

    def __init__(self) -> None:
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float, max_window: int) -> None:
        """Drop windows with no recent activity. Caller must hold the lock."""
        if now - self._last_sweep < 60:
            return
        self._last_sweep = now
        cutoff = now - max_window
        stale = [
            key for key, window in self._windows.items()
            if not window.hits or window.hits[-1] < cutoff
        ]
        for key in stale:
            del self._windows[key]

    def check(
        self,
        client_key: str,
        bucket: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """
        Record a hit and report whether it is allowed.

        Returns (allowed, remaining, retry_after_seconds). When the limit is
        exceeded the hit is *not* recorded, so a client hammering the endpoint
        cannot push its own reset time further out.
        """
        now = time.monotonic()
        key = (client_key, bucket)

        with self._lock:
            self._sweep(now, window_seconds)

            # Fail closed rather than growing without bound.
            if key not in self._windows and len(self._windows) >= _MAX_TRACKED_CLIENTS:
                return False, 0, window_seconds

            window = self._windows.setdefault(key, _Window())
            cutoff = now - window_seconds

            while window.hits and window.hits[0] < cutoff:
                window.hits.popleft()

            if len(window.hits) >= limit:
                retry_after = max(1, int(window.hits[0] + window_seconds - now) + 1)
                return False, 0, retry_after

            window.hits.append(now)
            return True, max(0, limit - len(window.hits)), 0

    def reset(self) -> None:
        """Clear all state. Used by tests."""
        with self._lock:
            self._windows.clear()
            self._last_sweep = time.monotonic()


limiter = SlidingWindowLimiter()

# Process-wide ceiling on simultaneous study generations. Without this, a
# handful of concurrent requests could each spawn several LLM calls and
# exhaust memory or the provider's own concurrency budget.
class ConcurrencyGuard:
    """
    Counting guard for the expensive pipeline.

    A plain semaphore would queue callers behind the limit, which is the wrong
    behaviour here: a request that waits for two minutes for a slot and then
    still has to run the whole pipeline is worse for the user than an
    immediate "try again shortly". So this rejects rather than queues.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Take a slot if one is free. Returns False when at capacity."""
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        return self._active


_study_guard = ConcurrencyGuard(settings.max_concurrent_studies)


def get_study_guard() -> ConcurrencyGuard:
    return _study_guard


# ── Client identification ────────────────────────────────────────

def get_client_key(request: Request) -> str:
    """
    Derive a stable rate-limit key for the caller.

    X-Forwarded-For is only consulted when TRUST_PROXY_HEADERS is set,
    because otherwise any client could set the header to a random value and
    get a fresh quota on every request.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Left-most entry is the original client per the convention.
            candidate = forwarded.split(",")[0].strip()
            if candidate and len(candidate) <= 64:
                return candidate
        real_ip = request.headers.get("x-real-ip")
        if real_ip and len(real_ip) <= 64:
            return real_ip.strip()

    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds a configured limit."""

    def __init__(self, retry_after: int, scope: str = "request") -> None:
        self.retry_after = max(1, retry_after)
        self.scope = scope
        super().__init__("Rate limit exceeded")


def enforce_study_limit(request: Request) -> None:
    """
    Apply both study windows to a caller.

    The short window blocks bursts; the daily window blocks slow-drip abuse
    that would otherwise stay under the short limit indefinitely.
    """
    if not settings.rate_limit_enabled:
        return

    client_key = get_client_key(request)

    allowed, _, retry_after = limiter.check(
        client_key, "study:burst", settings.study_rate_limit, settings.study_rate_window
    )
    if not allowed:
        raise RateLimitExceeded(retry_after, "study")

    allowed, _, retry_after = limiter.check(
        client_key, "study:daily", settings.study_daily_limit, settings.study_daily_window
    )
    if not allowed:
        raise RateLimitExceeded(retry_after, "study-daily")


def enforce_global_limit(request: Request) -> None:
    """Apply the general per-client ceiling to cheap endpoints."""
    if not settings.rate_limit_enabled:
        return

    client_key = get_client_key(request)
    allowed, _, retry_after = limiter.check(
        client_key, "global", settings.global_rate_limit, settings.global_rate_window
    )
    if not allowed:
        raise RateLimitExceeded(retry_after, "global")
