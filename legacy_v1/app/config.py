"""
Application Configuration

Single source of truth for every security-relevant setting. All values come
from environment variables so that nothing sensitive is ever hardcoded.

Defaults are deliberately the *safe* option: if an operator forgets to set
something, the application should end up more restrictive, not less.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple of trimmed values."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class Settings:
    """Immutable, validated view of the runtime environment."""

    def __init__(self) -> None:
        # ── Environment ──────────────────────────────────────────
        env = (os.getenv("APP_ENV") or "development").strip().lower()
        if env not in {"development", "production", "test"}:
            env = "production"  # unknown value → assume the strict one
        self.env: str = env

        # DEBUG can never be on in production, regardless of what the env says.
        self.debug: bool = _env_bool("DEBUG", env == "development") and env != "production"

        # ── CORS ─────────────────────────────────────────────────
        # The app is same-origin by default (server-rendered page + fetch to
        # its own /api). No cross-origin access is granted unless explicitly
        # configured. A wildcard is never accepted.
        raw_origins = _env_list("CORS_ALLOW_ORIGINS", ())
        self.cors_allow_origins: tuple[str, ...] = tuple(
            o for o in raw_origins if o != "*" and o.startswith(("http://", "https://"))
        )

        # ── HTTPS / cookies ──────────────────────────────────────
        self.force_https: bool = _env_bool("FORCE_HTTPS", env == "production")
        self.hsts_max_age: int = _env_int("HSTS_MAX_AGE", 31_536_000, minimum=0)

        # ── Request limits ───────────────────────────────────────
        # A study request is a tiny JSON body; 4 KB is generous.
        self.max_request_bytes: int = _env_int("MAX_REQUEST_BYTES", 4096, minimum=256, maximum=1_048_576)
        self.max_url_length: int = _env_int("MAX_URL_LENGTH", 2048, minimum=16, maximum=8192)

        # ── Transcript / AI resource limits ──────────────────────
        self.max_transcript_chars: int = _env_int(
            "MAX_TRANSCRIPT_CHARS", 400_000, minimum=1000, maximum=4_000_000
        )
        self.max_transcript_snippets: int = _env_int(
            "MAX_TRANSCRIPT_SNIPPETS", 20_000, minimum=10, maximum=200_000
        )
        self.chunk_max_chars: int = _env_int("CHUNK_MAX_CHARS", 10_000, minimum=1000, maximum=60_000)
        self.max_chunks: int = _env_int("MAX_CHUNKS", 12, minimum=1, maximum=100)
        self.max_prompt_chars: int = _env_int("MAX_PROMPT_CHARS", 60_000, minimum=1000, maximum=500_000)
        self.max_llm_response_chars: int = _env_int(
            "MAX_LLM_RESPONSE_CHARS", 400_000, minimum=1000, maximum=4_000_000
        )
        # Transcript text is echoed back to the browser for in-page search.
        # Cap it so a 6-hour video can't push a multi-megabyte response.
        self.max_transcript_response_chars: int = _env_int(
            "MAX_TRANSCRIPT_RESPONSE_CHARS", 120_000, minimum=0, maximum=2_000_000
        )

        # ── Timeouts (seconds) ───────────────────────────────────
        self.transcript_timeout: float = float(_env_int("TRANSCRIPT_TIMEOUT", 20, minimum=1, maximum=300))
        self.llm_timeout: float = float(_env_int("LLM_TIMEOUT", 60, minimum=1, maximum=600))
        self.total_study_timeout: float = float(
            _env_int("TOTAL_STUDY_TIMEOUT", 240, minimum=5, maximum=1800)
        )

        # ── Rate limiting ────────────────────────────────────────
        self.rate_limit_enabled: bool = _env_bool("RATE_LIMIT_ENABLED", True)
        # Expensive endpoint: transcript fetch + multiple LLM calls.
        self.study_rate_limit: int = _env_int("STUDY_RATE_LIMIT", 5, minimum=1, maximum=1000)
        self.study_rate_window: int = _env_int("STUDY_RATE_WINDOW", 300, minimum=1, maximum=86_400)
        # A second, longer window stops slow-drip abuse.
        self.study_daily_limit: int = _env_int("STUDY_DAILY_LIMIT", 60, minimum=1, maximum=100_000)
        self.study_daily_window: int = _env_int("STUDY_DAILY_WINDOW", 86_400, minimum=60, maximum=604_800)
        # Cheap endpoints still get a ceiling.
        self.global_rate_limit: int = _env_int("GLOBAL_RATE_LIMIT", 120, minimum=1, maximum=100_000)
        self.global_rate_window: int = _env_int("GLOBAL_RATE_WINDOW", 60, minimum=1, maximum=86_400)
        # Concurrency ceiling for the expensive endpoint, process-wide.
        self.max_concurrent_studies: int = _env_int("MAX_CONCURRENT_STUDIES", 4, minimum=1, maximum=64)

        # ── Proxy handling ───────────────────────────────────────
        # Only trust X-Forwarded-For when a proxy is actually in front of us,
        # otherwise clients could spoof their identity to defeat rate limits.
        self.trust_proxy_headers: bool = _env_bool("TRUST_PROXY_HEADERS", False)

        # ── Privacy ──────────────────────────────────────────────
        # Transcripts are processed in memory and never written to disk.
        self.persist_transcripts: bool = _env_bool("PERSIST_TRANSCRIPTS", False)
        self.include_transcript_in_response: bool = _env_bool("INCLUDE_TRANSCRIPT_IN_RESPONSE", True)

        # ── Logging ──────────────────────────────────────────────
        level = (os.getenv("LOG_LEVEL") or ("DEBUG" if self.debug else "INFO")).strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            level = "INFO"
        self.log_level: str = level

    # ── Derived helpers ──────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def llm_provider(self) -> str | None:
        """Which LLM provider is configured, if any. Never returns the key."""
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


settings = get_settings()
