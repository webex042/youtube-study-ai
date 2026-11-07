"""
AI Study Lab — Application Entry Point

Wires the middleware stack, exception handlers, API routers and pages.

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

from app import db
from app.config import settings
from app.routes.common import UserError
from app.routes.notebooks import router as notebooks_router
from app.routes.notes import router as notes_router
from app.routes.sources import router as sources_router
from app.routes.studio import router as studio_router
from app.security.logging_config import configure_logging
from app.security.middleware import (
    BodyLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.security.rate_limit import RateLimitExceeded
from app.services import jobs

configure_logging()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
ASSET_VERSION = "2.0.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Open the database, recover interrupted jobs, log the security posture."""
    settings.ensure_dirs()
    db.get_conn()
    jobs.recover_interrupted()
    _log_startup_posture()
    yield
    await jobs.shutdown()


app = FastAPI(
    title="AI Study Lab",
    description="Turn lectures, PDFs and notes into understanding: notes, tutor chat and revision tools.",
    version=ASSET_VERSION,
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# ── Middleware ───────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

if settings.cors_allow_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    logger.info("CORS enabled for %d configured origin(s)", len(settings.cors_allow_origins))

# ── Static files & templates ─────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True

for router in (notebooks_router, sources_router, studio_router, notes_router):
    app.include_router(router)


# ── Exception handlers ───────────────────────────────────────────

def _body(request: Request, message: str) -> dict:
    body = {"error": message}
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return body


@app.exception_handler(UserError)
async def user_error_handler(request: Request, exc: UserError):
    return JSONResponse(status_code=exc.status, content=_body(request, exc.message))


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    message = (
        "You're sending requests very quickly. Please wait a moment."
        if exc.scope != "ai-daily" else "Daily AI request limit reached (AI_DAILY_LIMIT in .env)."
    )
    return JSONResponse(status_code=429, content=_body(request, message),
                        headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.info("request validation failed", extra={"endpoint": request.url.path,
                                                    "error_type": "RequestValidationError"})
    message = "The request was not valid. Please check your input and try again."
    try:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        if first.get("type", "").startswith("string_too_long"):
            message = f"'{loc}' is too long."
        elif first.get("type", "").startswith(("string_too_short", "missing")):
            message = f"'{loc}' is required."
    except (IndexError, KeyError, TypeError):
        pass
    return JSONResponse(status_code=422, content=_body(request, message))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
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
        logger.error("http error", extra={"endpoint": request.url.path, "status_code": exc.status_code})
    if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/static/")):
        return templates.TemplateResponse(request, "index.html", _page_context("home"), status_code=404)
    headers = dict(getattr(exc, "headers", None) or {})
    return JSONResponse(status_code=exc.status_code, content=_body(request, message), headers=headers or None)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled exception", extra={"endpoint": request.url.path,
                                                  "error_type": type(exc).__name__, "status_code": 500})
    return JSONResponse(status_code=500, content=_body(request, "Something went wrong. Please try again."))


# ── Pages ────────────────────────────────────────────────────────

def _page_context(page: str, notebook_id: str = "") -> dict:
    return {"page": page, "notebook_id": notebook_id, "version": ASSET_VERSION}


@app.get("/")
async def home(request: Request):
    """Notebook list."""
    return templates.TemplateResponse(request, "index.html", _page_context("home"))


@app.get("/notebook/{notebook_id}")
async def notebook_page(request: Request, notebook_id: str):
    """The three-panel notebook workspace."""
    if not db.get("notebooks", notebook_id):
        return templates.TemplateResponse(request, "index.html", _page_context("home"), status_code=404)
    return templates.TemplateResponse(request, "index.html", _page_context("notebook", notebook_id))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _log_startup_posture() -> None:
    logger.info(
        "AI Study Lab starting — env=%s debug=%s https=%s rate_limit=%s cors_origins=%d ai_provider=%s",
        settings.env,
        settings.debug,
        settings.force_https,
        settings.rate_limit_enabled,
        len(settings.cors_allow_origins),
        settings.llm_provider or "none",
    )
    if settings.llm_provider is None:
        logger.warning("No AI key configured — add GEMINI_API_KEY to .env to enable AI features.")
    if settings.is_production:
        if settings.debug:
            logger.error("DEBUG is enabled in production — disable it immediately.")
        if not settings.force_https:
            logger.warning("FORCE_HTTPS is off in production; HSTS will not be sent.")
