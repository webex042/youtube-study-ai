"""
Outbound HTTP for sources: web pages, YouTube metadata, search-result links.

Every hop is validated by the SSRF guard *before* a connection is made, and
redirects are followed manually so each new location is validated too.
Response size is capped while streaming, so a huge or endless body can't
exhaust memory.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import settings
from app.security.ssrf import SSRFValidationError, validate_outbound_url, validate_public_url

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 AIStudyLab/2.0"
)


class FetchError(ValueError):
    """User-safe fetch failure."""


@dataclass
class FetchResult:
    url: str
    content_type: str
    body: bytes


async def fetch(url: str, *, public: bool = True, max_bytes: int = 15_000_000,
                timeout: float | None = None, max_redirects: int = 5) -> FetchResult:
    validate = validate_public_url if public else validate_outbound_url
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout or settings.web_fetch_timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client:
        for _ in range(max_redirects + 1):
            try:
                validate(current)
            except SSRFValidationError as exc:
                raise FetchError(str(exc)) from exc
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The page redirected without a destination.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code == 404:
                        raise FetchError("That page was not found (404).")
                    if response.status_code in (401, 403):
                        raise FetchError("That page is private or blocks automated access.")
                    if response.status_code >= 400:
                        raise FetchError(f"The website returned an error ({response.status_code}).")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError("That page is too large to import.")
                        chunks.append(chunk)
                    return FetchResult(
                        url=current,
                        content_type=(response.headers.get("content-type") or "").lower(),
                        body=b"".join(chunks),
                    )
            except FetchError:
                raise
            except httpx.TimeoutException as exc:
                raise FetchError("The website took too long to respond.") from exc
            except httpx.HTTPError as exc:
                raise FetchError("Couldn't connect to that website.") from exc
    raise FetchError("Too many redirects.")


async def resolve_redirect(url: str) -> str:
    """Resolve a search-grounding redirect link to its destination URL."""
    try:
        validate_outbound_url(url)
    except SSRFValidationError:
        return url
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(url)
            location = response.headers.get("location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                return urljoin(url, location)
    except httpx.HTTPError:
        pass
    return url


# ── HTML → readable text ─────────────────────────────────────────

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form",
              "iframe", "button", "select", "template", "canvas"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "tr", "br", "h1", "h2", "h3", "h4",
               "h5", "h6", "pre", "blockquote", "table", "ul", "ol", "dd", "dt", "figcaption"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self._in_pre = 0
        self.meta_description = ""

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            attr = dict(attrs)
            if (attr.get("name") or attr.get("property") or "").lower() in ("description", "og:description"):
                self.meta_description = self.meta_description or (attr.get("content") or "")
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "pre":
            self._in_pre += 1
            self.parts.append("\n```\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if self.skip:
            return
        if tag == "pre":
            self._in_pre = max(0, self._in_pre - 1)
            self.parts.append("\n```\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip:
            return
        if self._in_pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(raw_html: str) -> tuple[str, str, str]:
    """Return (title, text, description) from an HTML document."""
    # Prefer the <main>/<article> region when the page has one.
    focus = raw_html
    for tag in ("article", "main"):
        match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", raw_html, re.S | re.I)
        if match and len(match.group(1)) > 2000:
            focus = raw_html[: raw_html.lower().find("<body") if "<body" in raw_html.lower() else 0] + match.group(1)
            break
    parser = _TextExtractor()
    try:
        parser.feed(focus)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML is common; keep what we have
        pass
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = html_lib.unescape(re.sub(r"\s+", " ", parser.title)).strip()
    if not title:
        # Fall back to the full document's <title>
        match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.S | re.I)
        if match:
            title = html_lib.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return title[:300], text, parser.meta_description[:500]


def decode_body(body: bytes, content_type: str) -> str:
    charset = None
    match = re.search(r"charset=([\w\-]+)", content_type or "")
    if match:
        charset = match.group(1)
    for enc in filter(None, (charset, "utf-8", "latin-1")):
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


# ── YouTube metadata ─────────────────────────────────────────────

async def youtube_metadata(video_id: str) -> dict:
    """Title, channel, thumbnail and (best effort) duration. Never raises."""
    meta: dict = {
        "video_id": video_id,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }
    watch = f"https://www.youtube.com/watch?v={video_id}"
    try:
        result = await fetch(
            f"https://www.youtube.com/oembed?url={watch}&format=json", public=False, max_bytes=200_000, timeout=10
        )
        data = json.loads(result.body.decode("utf-8", errors="replace"))
        meta["title"] = str(data.get("title") or "")[:300]
        meta["channel"] = str(data.get("author_name") or "")[:200]
    except (FetchError, ValueError) as exc:
        logger.info("oembed lookup failed", extra={"error_type": type(exc).__name__})
        if isinstance(exc, FetchError) and "not found" in str(exc).lower():
            meta["unavailable"] = True
    try:
        result = await fetch(watch, public=False, max_bytes=3_000_000, timeout=12)
        page = decode_body(result.body, result.content_type)
        match = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', page)
        if match:
            meta["duration"] = int(match.group(1))
        if "title" not in meta:
            match = re.search(r"<title>(.*?)</title>", page, re.S)
            if match:
                meta["title"] = html_lib.unescape(match.group(1)).replace(" - YouTube", "").strip()[:300]
        match = re.search(r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.)*)"', page)
        if match:
            try:
                meta["description"] = json.loads(f'"{match.group(1)}"')[:4000]
            except ValueError:
                pass
        if '"playabilityStatus":{"status":"LOGIN_REQUIRED"' in page or '"status":"UNPLAYABLE"' in page:
            meta["restricted"] = True
    except FetchError as exc:
        logger.info("watch page lookup failed", extra={"error_type": type(exc).__name__})
    return meta


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host
