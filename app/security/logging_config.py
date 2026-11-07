"""
Logging Configuration

Logs exist to make failures diagnosable, not to archive user content. Two
rules follow from that, and both are enforced mechanically rather than by
convention:

1. Structured metadata only — request_id, endpoint, status, duration,
   error type. Never bodies, transcripts, prompts or headers.
2. A redaction filter sits on the root handler as a backstop. If any code
   path ever does log something that looks like a key or a token, the
   pattern is replaced before it reaches the stream.
"""

from __future__ import annotations

import logging
import re
import sys

from app.config import settings

# Patterns that must never appear in a log line. Each captures the label so
# the redacted output still shows *what* was suppressed.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Provider key formats.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-***REDACTED***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "AIza***REDACTED***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***REDACTED***"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "xox-***REDACTED***"),
    # Bearer tokens and JWTs.
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{10,}", re.I), "Bearer ***REDACTED***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"),
     "***REDACTED_JWT***"),
    # key=value / "key": "value" shapes for anything sensitive-sounding.
    (re.compile(
        r"(?i)\b(api[_\-]?key|apikey|secret|password|passwd|token|authorization|"
        r"credential|private[_\-]?key|access[_\-]?key)\b\s*[:=]\s*"
        r"[\"']?[^\s\"',}]{4,}[\"']?"
    ), r"\1=***REDACTED***"),
    # Connection strings with inline credentials.
    (re.compile(r"(?i)\b([a-z+]+)://[^:/@\s]+:[^@\s]+@"), r"\1://***REDACTED***@"),
)


class RedactionFilter(logging.Filter):
    """
    Scrub secret-shaped substrings from every log record.

    Applied to the message and to any string args. This is a safety net —
    the intent is that nothing sensitive is passed to the logger in the
    first place — but a net that costs a regex pass is worth having.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: self._redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(value) if isinstance(value, str) else value
                    for value in record.args
                )
        return True

    @staticmethod
    def _redact(text: str) -> str:
        for pattern, replacement in _REDACTION_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


class StructuredFormatter(logging.Formatter):
    """
    Formatter that appends whitelisted structured fields.

    Only the fields named here are emitted. Anything else attached to a
    record via `extra` is ignored, so a future caller cannot accidentally
    widen what gets logged.
    """

    _FIELDS = (
        "request_id",
        "endpoint",
        "method",
        "status_code",
        "duration_ms",
        "error_type",
        "video_id",
        "provider",
        "chunk_count",
        "transcript_chars",
        "rate_limit_scope",
    )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)

        parts = []
        for field in self._FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                # Strip CR/LF so a value can never forge a second log line.
                flat = str(value).replace("\n", " ").replace("\r", " ")
                parts.append(f"{field}={flat}")

        if parts:
            return f"{base} | {' '.join(parts)}"
        return base


def configure_logging() -> None:
    """
    Install the logging configuration.

    Idempotent: existing handlers are removed first so repeated calls (tests,
    reloads) don't produce duplicate output.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        StructuredFormatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RedactionFilter())

    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))

    # Access logs would record full query strings; our own request middleware
    # already emits the metadata we want, without the URL.
    logging.getLogger("uvicorn.access").disabled = True

    # Third-party libraries can be chatty and occasionally echo payloads.
    for noisy in ("httpx", "httpcore", "urllib3", "google", "openai", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
