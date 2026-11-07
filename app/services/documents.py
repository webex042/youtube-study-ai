"""
Document text extraction: PDF, Word (.docx), PowerPoint (.pptx), plain text.

Each extractor returns a list of `Segment`s that keep their location (page or
slide number) so citations can point back to the exact place in the source.
Office formats are read straight from their ZIP/XML structure, which avoids
heavyweight dependencies and never executes anything inside the file.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass
class Segment:
    text: str
    start: Optional[float] = None   # seconds (media)
    end: Optional[float] = None
    page: Optional[int] = None      # page / slide number (documents)
    kind: str = "text"              # text | speech | visual | heading


@dataclass
class Extracted:
    title: str
    segments: list[Segment] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.segments)


class ExtractionError(ValueError):
    """User-safe extraction failure."""


_WS = re.compile(r"[ \t ]+")


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── PDF ──────────────────────────────────────────────────────────

def extract_pdf(data: bytes, filename: str) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise ExtractionError("PDF support needs the 'pypdf' package (pip install -r requirements.txt).") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("This PDF is password-protected.") from exc
        pages = reader.pages
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("This PDF could not be read (it may be damaged).") from exc

    title = ""
    try:
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip()
    except Exception:  # noqa: BLE001
        pass

    segments: list[Segment] = []
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page shouldn't sink the document
            text = ""
        text = _reflow(_tidy(text))
        if text:
            segments.append(Segment(text=text, page=number))

    result = Extracted(title=title or _stem(filename), segments=segments, meta={"page_count": len(pages)})
    return result


def _reflow(text: str) -> str:
    """Join PDF hard line-breaks that fall in the middle of a sentence."""
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)              # hyphenated line breaks
    text = re.sub(r"(?<=[^.!?:;\n])\n(?=[a-z(])", " ", text)       # continuation lines
    return text


def pdf_looks_scanned(extracted: Extracted) -> bool:
    pages = extracted.meta.get("page_count") or 1
    return extracted.char_count < max(200, 60 * pages)


# ── Word ─────────────────────────────────────────────────────────

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(data: bytes, filename: str) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
            core = zf.read("docProps/core.xml") if "docProps/core.xml" in zf.namelist() else b""
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractionError("This Word file could not be read. Save it as .docx and try again.") from exc

    root = ET.fromstring(xml)
    lines: list[str] = []
    body = root.find(f"{_W}body")
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            line = _docx_paragraph(block)
            if line:
                lines.append(line)
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = []
                for cell in row.iter(f"{_W}tc"):
                    cells.append(" ".join(filter(None, (_docx_paragraph(p, plain=True) for p in cell.iter(f"{_W}p")))))
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")

    title = _core_title(core) or _stem(filename)
    text = _tidy("\n".join(lines))
    if not text:
        raise ExtractionError("This Word document has no readable text.")
    return Extracted(title=title, segments=_split_paragraph_blocks(text))


def _docx_paragraph(p, plain: bool = False) -> str:
    texts = []
    for node in p.iter():
        if node.tag == f"{_W}t" and node.text:
            texts.append(node.text)
        elif node.tag == f"{_W}tab":
            texts.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            texts.append("\n")
    text = "".join(texts).strip()
    if not text or plain:
        return text
    style = p.find(f"{_W}pPr/{_W}pStyle")
    val = style.get(f"{_W}val", "") if style is not None else ""
    match = re.match(r"(?i)heading\s*(\d)", val)
    if match:
        return "#" * min(4, int(match.group(1))) + " " + text
    if val.lower() == "title":
        return "# " + text
    if p.find(f"{_W}pPr/{_W}numPr") is not None:
        return "- " + text
    return text


def _core_title(core: bytes) -> str:
    if not core:
        return ""
    match = re.search(rb"<dc:title>(.*?)</dc:title>", core, re.S)
    return match.group(1).decode("utf-8", errors="ignore").strip()[:300] if match else ""


# ── PowerPoint ───────────────────────────────────────────────────

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def extract_pptx(data: bytes, filename: str) -> Extracted:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError("This PowerPoint file could not be read.") from exc
    with zf:
        slides = sorted(
            (n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)),
        )
        segments: list[Segment] = []
        for index, name in enumerate(slides, start=1):
            root = ET.fromstring(zf.read(name))
            paras = []
            for para in root.iter(f"{_A}p"):
                text = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                if text:
                    paras.append(text)
            notes_name = f"ppt/notesSlides/notesSlide{index}.xml"
            if notes_name in zf.namelist():
                nroot = ET.fromstring(zf.read(notes_name))
                notes = " ".join((t.text or "") for t in nroot.iter(f"{_A}t")).strip()
                if notes and not notes.isdigit():
                    paras.append(f"(Speaker notes) {notes}")
            if paras:
                body = paras[0] if len(paras) == 1 else f"## {paras[0]}\n" + "\n".join(f"- {p}" for p in paras[1:])
                segments.append(Segment(text=body, page=index))
        core = zf.read("docProps/core.xml") if "docProps/core.xml" in zf.namelist() else b""
    if not segments:
        raise ExtractionError("This presentation has no readable text.")
    return Extracted(title=_core_title(core) or _stem(filename), segments=segments,
                     meta={"slide_count": len(slides)})


# ── Plain text ───────────────────────────────────────────────────

def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(text: str, title: str) -> Extracted:
    text = _tidy(text)
    if not text:
        raise ExtractionError("The text is empty.")
    return Extracted(title=title, segments=_split_paragraph_blocks(text))


def _split_paragraph_blocks(text: str, target: int = 1200) -> list[Segment]:
    """Group paragraphs into segments of roughly `target` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    segments: list[Segment] = []
    buf: list[str] = []
    size = 0
    for para in paragraphs:
        while len(para) > target * 2:  # split very long paragraphs on sentence boundaries
            cut = para.rfind(". ", 0, target)
            cut = cut + 1 if cut > target // 3 else target
            if buf:
                segments.append(Segment("\n\n".join(buf)))
                buf, size = [], 0
            segments.append(Segment(para[:cut].strip()))
            para = para[cut:].strip()
        if size + len(para) > target and buf:
            segments.append(Segment("\n\n".join(buf)))
            buf, size = [], 0
        buf.append(para)
        size += len(para)
    if buf:
        segments.append(Segment("\n\n".join(buf)))
    return segments


def _stem(filename: str) -> str:
    name = (filename or "Document").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.replace("_", " ").strip()[:200] or "Document"
