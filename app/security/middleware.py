"""
Security Middleware

Three concerns, each as its own middleware so they can be reasoned about
and tested independently:

* SecurityHeadersMiddleware — CSP and the other response headers that make
  the browser an ally rather than a liability.
* BodyLimitMiddleware — rejects oversized requests before the body is read
  into memory.
* RequestContextMiddleware — assigns a request ID and emits one structured
  log line per request, carrying no user content.
"""

from __future__ import annotations

import logging
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings

logger = logging.getLogger("app.request")


# ── Content Security Policy ──────────────────────────────────────

def _build_csp() -> str:
    """
    Assemble the CSP.

    Notable choices:

    * `script-src 'self'` with no 'unsafe-inline' and no 'unsafe-eval'. Every
      script (including the vendored libraries) is served from this origin and
      the templates carry zero inline script.
    * `style-src` allows 'unsafe-inline' because the UI positions popovers and
      animates elements from JavaScript. Inline *styles* cannot execute script.
    * `connect-src 'self'` — the page only ever talks to its own API.
    * `frame-src` admits only YouTube's embed hosts, for the in-app player that
      jumps to cited timestamps.
    * `media-src 'self' blob:` — generated Audio/Video Overviews are served by
      this app.
    * `object-src 'none'` and `base-uri 'none'` close off plugin and
      base-tag injection; `frame-ancestors 'none'` prevents clickjacking.
    """
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self' data:",
        # Video thumbnails come from YouTube's image CDN; data:/blob: cover
        # generated images (infographic export, inline SVG).
        "img-src 'self' data: blob: https://i.ytimg.com https://img.youtube.com",
        "connect-src 'self'",
        "media-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "frame-src https://www.youtube-nocookie.com https://www.youtube.com",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
    ]

    if settings.force_https:
        directives.append("upgrade-insecure-requests")

    return "; ".join(directives)


_CSP_VALUE = _build_csp()

_YT = '"https://www.youtube-nocookie.com" "https://www.youtube.com"'
_PERMISSIONS_POLICY = ", ".join([
    "accelerometer=()",
    "ambient-light-sensor=()",
    f"autoplay=(self {_YT})",
    "camera=()",
    "display-capture=()",
    f"encrypted-media=(self {_YT})",
    f"fullscreen=(self {_YT})",
    "geolocation=()",
    "gyroscope=()",
    "magnetometer=()",
    "microphone=()",
    "midi=()",
    "payment=()",
    f"picture-in-picture=(self {_YT})",
    "usb=()",
    "xr-spatial-tracking=()",
])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response, including error responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        response.headers["Content-Security-Policy"] = _CSP_VALUE
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        # Redundant with CSP frame-ancestors, but still honoured by older browsers.
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        if settings.force_https:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age}; includeSubDomains"
            )

        # API responses are per-request and may reflect user content; keep
        # them out of shared and browser caches.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"

        # Don't advertise the server implementation.
        if "server" in response.headers:
            del response.headers["server"]

        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """
    Reject oversized request bodies and unexpected content types.

    Content-Length is checked first so an obviously-too-large request is
    refused without reading anything. JSON endpoints get the JSON limit; the
    single file-upload endpoint accepts multipart/form-data up to the upload
    limit (the route streams the file to disk and re-checks the size).
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes or settings.max_request_bytes

    @staticmethod
    def _is_upload(path: str) -> bool:
        return path.startswith("/api/notebooks/") and path.endswith("/sources/upload")

    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            path = request.url.path
            upload = self._is_upload(path)
            limit = settings.max_upload_bytes + 1_000_000 if upload else self.max_bytes
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    declared = int(content_length)
                except ValueError:
                    return JSONResponse(status_code=400, content={"error": "Malformed request."})
                if declared > limit:
                    message = (
                        f"That file is too large (limit {settings.max_upload_mb} MB)."
                        if upload else "Request body is too large."
                    )
                    return JSONResponse(status_code=413, content={"error": message})

            if path.startswith("/api/"):
                content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
                expected = "multipart/form-data" if upload else "application/json"
                if content_type and content_type != expected:
                    return JSONResponse(
                        status_code=415,
                        content={"error": f"Unsupported content type. Send {expected}."},
                    )

        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Assign a request ID and log one structured line per request.

    The log line carries only non-sensitive metadata: no bodies, no URLs
    beyond the route path, no transcript text, no headers. The request ID is
    echoed to the client so a user can quote it in a support request without
    us having to expose any internal detail in the error message itself.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = secrets.token_hex(8)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response: Response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "endpoint": request.url.path,
                    "method": request.method,
                    "status_code": 500,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "endpoint": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
