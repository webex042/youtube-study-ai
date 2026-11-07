"""
AI Study Lab — Application Entry Point

Wires the middleware stack, CORS, exception handlers and routes.

Middleware order matters and is deliberate. Starlette runs middleware in
reverse registration order on the way in, so registering security headers
*first* means they are applied last on the way out — which is what guarantees
they end up on every response, including the ones produced by an exception
handler further down the stack.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.routes.study import router as study_router
from app.security.logging_config import configure_logging
from app.security.middleware import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

configure_logging()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Log the effective security posture on boot."""
    _log_startup_posture()
    yield


app = FastAPI(
    title="AI Study Lab",
    description="Turn any YouTube lecture into structured study material.",
    version="1.0.0",
    lifespan=lifespan,
    # The interactive docs enumerate the API surface and schemas. Useful in
    # development, unnecessary exposure in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# ── Middleware ───────────────────────────────────────────────────
# Registered outermost-first (see module docstring).
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

# ── CORS ─────────────────────────────────────────────────────────
# The app is same-origin: the page and its API share a host, so no CORS
# headers are needed by default. They are added only when an operator has
# explicitly listed trusted origins. A wildcard is filtered out in config.
if settings.cors_allow_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=False,   # no cookie auth, so credentials are never needed
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    logger.info("CORS enabled for %d configured origin(s)", len(settings.cors_allow_origins))

# ── Static files & templates ─────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Jinja2 autoescaping is on by default for .html; assert it so a future
# template-config change can't silently turn off HTML escaping.
templates.env.autoescape = True

app.include_router(study_router)


# ── Exception handlers ───────────────────────────────────────────
# Every handler returns a generic message. Stack traces, file paths, database
# details and internal service names stay in the server log.

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle schema validation failures.

    Pydantic's default error body echoes the rejected input and the internal
    field structure. That is a small information leak and confusing for users,
    so it is replaced with a fixed message; the detail goes to the log.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.info(
        "request validation failed",
        extra={
            "endpoint": request.url.path,
            "request_id": request_id,
            "error_type": "RequestValidationError",
        },
    )
    body = {"error": "The request was not valid. Please check your input and try again."}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Map HTTP errors onto safe, uniform messages."""
    request_id = getattr(request.state, "request_id", None)

    safe_messages = {
        400: "The request was malformed.",
        401: "Authentication is required.",
        403: "You do not have access to this resource.",
        404: "The requested resource was not found.",
        405: "That method is not allowed for this endpoint.",
        413: "The request was too large.",
        415: "Unsupported content type.",
        429: "Too many requests. Please slow down.",
        503: "The service is temporarily unavailable.",
    }
    message = safe_messages.get(exc.status_code, "Something went wrong. Please try again.")

    if exc.status_code >= 500:
        logger.error(
            "http error",
            extra={
                "endpoint": request.url.path,
                "status_code": exc.status_code,
                "request_id": request_id,
            },
        )

    body = {"error": message}
    if request_id:
        body["request_id"] = request_id

    headers = dict(getattr(exc, "headers", None) or {})
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers or None)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for unexpected failures.

    The traceback is logged; the client receives a fixed message and a
    request ID. Nothing about the internals crosses the boundary.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "unhandled exception",
        extra={
            "endpoint": request.url.path,
            "error_type": type(exc).__name__,
            "request_id": request_id,
            "status_code": 500,
        },
    )
    body = {"error": "Something went wrong. Please try again."}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=500, content=body)


# ── Pages ────────────────────────────────────────────────────────

@app.get("/")
async def home(request: Request):
    """Render the single-page application shell."""
    return templates.TemplateResponse(request, "index.html", {})


def _log_startup_posture() -> None:
    """
    Record the security posture at boot.

    Deliberately logs booleans and counts only — whether a provider is
    configured, never which key. This is the line an operator checks to
    confirm production isn't running with development settings.
    """
    logger.info(
        "AI Study Lab starting — env=%s debug=%s https=%s rate_limit=%s "
        "cors_origins=%d ai_provider=%s persist_transcripts=%s",
        settings.env,
        settings.debug,
        settings.force_https,
        settings.rate_limit_enabled,
        len(settings.cors_allow_origins),
        settings.llm_provider or "none",
        settings.persist_transcripts,
    )

    if settings.is_production:
        if settings.debug:
            logger.error("DEBUG is enabled in production — disable it immediately.")
        if not settings.force_https:
            logger.warning("FORCE_HTTPS is off in production; HSTS will not be sent.")
        if not settings.rate_limit_enabled:
            logger.warning("Rate limiting is disabled in production.")
