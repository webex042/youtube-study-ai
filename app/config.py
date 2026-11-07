"""
Application Configuration

Single source of truth for every runtime setting. All values come from
environment variables so that nothing sensitive is ever hardcoded.

Defaults are deliberately the *safe* option: if an operator forgets to set
something, the application should end up more restrictive, not less.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


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


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


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

        # ── Storage ──────────────────────────────────────────────
        # Everything the app persists (database, uploaded files, generated
        # audio) lives under one folder that is git-ignored.
        data_dir = Path(_env_str("DATA_DIR", str(BASE_DIR / "data")))
        if not data_dir.is_absolute():
            data_dir = (BASE_DIR / data_dir).resolve()
        self.data_dir: Path = data_dir
        self.db_path: Path = data_dir / "notebooks.db"
        self.upload_dir: Path = data_dir / "uploads"
        self.media_dir: Path = data_dir / "media"

        # ── CORS ─────────────────────────────────────────────────
        raw_origins = _env_list("CORS_ALLOW_ORIGINS", ())
        self.cors_allow_origins: tuple[str, ...] = tuple(
            o for o in raw_origins if o != "*" and o.startswith(("http://", "https://"))
        )

        # ── HTTPS / cookies ──────────────────────────────────────
        self.force_https: bool = _env_bool("FORCE_HTTPS", env == "production")
        self.hsts_max_age: int = _env_int("HSTS_MAX_AGE", 31_536_000, minimum=0)

        # ── Request limits ───────────────────────────────────────
        # JSON bodies (chat messages, pasted text, edited notes).
        self.max_request_bytes: int = _env_int(
            "MAX_REQUEST_BYTES", 2_000_000, minimum=4096, maximum=50_000_000
        )
        # File uploads (lecture videos can be large).
        self.max_upload_mb: int = _env_int("MAX_UPLOAD_MB", 1024, minimum=1, maximum=2000)
        self.max_url_length: int = _env_int("MAX_URL_LENGTH", 2048, minimum=16, maximum=8192)
        self.max_pasted_chars: int = _env_int("MAX_PASTED_CHARS", 1_000_000, minimum=1000)

        # ── Transcript / source limits ───────────────────────────
        self.max_transcript_chars: int = _env_int(
            "MAX_TRANSCRIPT_CHARS", 1_500_000, minimum=1000, maximum=8_000_000
        )
        self.max_transcript_snippets: int = _env_int(
            "MAX_TRANSCRIPT_SNIPPETS", 60_000, minimum=10, maximum=400_000
        )
        self.max_source_chars: int = _env_int(
            "MAX_SOURCE_CHARS", 2_000_000, minimum=10_000, maximum=20_000_000
        )
        # Kept for the transcript processor's chunker.
        self.chunk_max_chars: int = _env_int("CHUNK_MAX_CHARS", 10_000, minimum=1000, maximum=60_000)
        self.max_chunks: int = _env_int("MAX_CHUNKS", 12, minimum=1, maximum=10_000)

        # How much source text may go into one model request. Gemini Flash
        # models take ~1M tokens; 700k characters (~175k tokens) keeps cost
        # sane while still fitting several long lectures whole.
        self.max_context_chars: int = _env_int(
            "MAX_CONTEXT_CHARS", 700_000, minimum=20_000, maximum=3_500_000
        )
        self.max_llm_response_chars: int = _env_int(
            "MAX_LLM_RESPONSE_CHARS", 600_000, minimum=1000, maximum=4_000_000
        )

        # ── Timeouts (seconds) ───────────────────────────────────
        self.transcript_timeout: float = float(_env_int("TRANSCRIPT_TIMEOUT", 30, minimum=1, maximum=300))
        self.llm_timeout: float = float(_env_int("LLM_TIMEOUT", 300, minimum=10, maximum=1800))
        self.video_analysis_timeout: float = float(
            _env_int("VIDEO_ANALYSIS_TIMEOUT", 900, minimum=60, maximum=3600)
        )
        self.web_fetch_timeout: float = float(_env_int("WEB_FETCH_TIMEOUT", 20, minimum=3, maximum=120))

        # ── AI models ────────────────────────────────────────────
        # The first model that the API accepts is used; the rest are fallbacks
        # for when a model name is retired or unavailable to an account.
        self.gemini_models: tuple[str, ...] = _env_list(
            "GEMINI_MODEL",
            ("gemini-3.8-flash",),
        ) + _env_list(
            "GEMINI_FALLBACK_MODELS",
            ("gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash"),
        )
        self.gemini_pro_models: tuple[str, ...] = _env_list(
            "GEMINI_PRO_MODEL",
            ("gemini-4-argon",),
        ) + _env_list(
            "GEMINI_PRO_FALLBACK_MODELS",
            ("gemini-3.1-pro-preview", "gemini-pro-latest", "gemini-2.5-pro"),
        )
        self.gemini_lite_models: tuple[str, ...] = _env_list(
            "GEMINI_LITE_MODEL",
            ("gemini-3.5-flash-lite",),
        ) + ("gemini-flash-lite-latest", "gemini-2.5-flash-lite") + self.gemini_models
        self.gemini_tts_models: tuple[str, ...] = _env_list(
            "GEMINI_TTS_MODEL",
            ("gemini-3.8-flash-tts",),
        ) + _env_list(
            "GEMINI_TTS_FALLBACK_MODELS",
            ("gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"),
        )
        self.openai_model: str = _env_str("OPENAI_MODEL", "gpt-4o-mini")

        # Analyse the actual video (frames + audio) with Gemini, not just the
        # captions. Turn off to save tokens; captions are still used.
        self.video_analysis_enabled: bool = _env_bool("VIDEO_ANALYSIS", True)
        # Long videos are analysed in segments of this many minutes.
        self.video_segment_minutes: int = _env_int("VIDEO_SEGMENT_MINUTES", 20, minimum=5, maximum=180)
        # Frames per second sampled from videos (blank = Gemini's default of 1).
        raw_fps = os.getenv("VIDEO_FPS", "").strip()
        try:
            self.video_fps: float | None = max(0.1, min(5.0, float(raw_fps))) if raw_fps else None
        except ValueError:
            self.video_fps = None
        # Allow adding arbitrary public web pages as sources.
        self.allow_web_sources: bool = _env_bool("ALLOW_WEB_SOURCES", True)

        # ── Rate limiting ────────────────────────────────────────
        self.rate_limit_enabled: bool = _env_bool("RATE_LIMIT_ENABLED", True)
        # Expensive endpoints (chat, generation, source processing).
        self.ai_rate_limit: int = _env_int("AI_RATE_LIMIT", 40, minimum=1, maximum=10_000)
        self.ai_rate_window: int = _env_int("AI_RATE_WINDOW", 60, minimum=1, maximum=86_400)
        self.ai_daily_limit: int = _env_int("AI_DAILY_LIMIT", 1500, minimum=1, maximum=1_000_000)
        self.ai_daily_window: int = _env_int("AI_DAILY_WINDOW", 86_400, minimum=60, maximum=604_800)
        # Cheap endpoints still get a ceiling (the UI polls while jobs run).
        self.global_rate_limit: int = _env_int("GLOBAL_RATE_LIMIT", 900, minimum=1, maximum=100_000)
        self.global_rate_window: int = _env_int("GLOBAL_RATE_WINDOW", 60, minimum=1, maximum=86_400)
        # Background jobs (source processing + Studio generation) at once.
        self.max_concurrent_jobs: int = _env_int("MAX_CONCURRENT_JOBS", 4, minimum=1, maximum=64)

        # Kept for compatibility with the original config names.
        self.study_rate_limit = self.ai_rate_limit
        self.study_rate_window = self.ai_rate_window
        self.study_daily_limit = self.ai_daily_limit
        self.study_daily_window = self.ai_daily_window
        self.max_concurrent_studies = self.max_concurrent_jobs

        # ── Proxy handling ───────────────────────────────────────
        self.trust_proxy_headers: bool = _env_bool("TRUST_PROXY_HEADERS", False)

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
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def llm_provider(self) -> str | None:
        """Which LLM provider is configured, if any. Never returns the key."""
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return None

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.media_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


settings = get_settings()
