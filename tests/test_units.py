"""Unit tests: chunking, retrieval, citations, streaming helpers, video segmentation, documents, SSRF."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.models.schemas import MindMap, VideoAnalysis
from app.services import retrieval
from app.services.chat import FollowupFilter, thought_status
from app.services.documents import Segment, extract_docx, extract_pdf, extract_pptx, extract_text
from app.services.sources import extract_urls, kind_for_filename, mp4_duration, SourceError, _video_segments
from app.services.studio import build_tree
from app.services.video_understanding import _merge, _plan, _relative


# ── time helpers ─────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("12:34", 754), ("1:02:03", 3723), ("75s", 75), ("1h2m3s", 3723), ("00:05", 5), (42, 42), ("", None), ("abc", None),
])
def test_parse_time(value, expected):
    assert retrieval.parse_time(value) == expected


def test_fmt_time():
    assert retrieval.fmt_time(65) == "01:05"
    assert retrieval.fmt_time(3723) == "1:02:03"
    assert retrieval.fmt_time(None) == ""


# ── chunking ─────────────────────────────────────────────────────

def test_chunks_keep_time_ranges():
    segs = [Segment(text=f"sentence {i} " * 30, start=i * 30.0, end=i * 30.0 + 30) for i in range(10)]
    chunks = retrieval.build_chunks(segs, target=900)
    assert len(chunks) > 1
    assert chunks[0]["start_sec"] == 0.0
    assert chunks[0]["label"] == "00:00"
    starts = [c["start_sec"] for c in chunks]
    assert starts == sorted(starts)
    assert chunks[-1]["end_sec"] == 300.0


def test_chunks_never_span_pages():
    segs = [Segment(text="a" * 300, page=1), Segment(text="b" * 300, page=2)]
    chunks = retrieval.build_chunks(segs, target=5000)
    assert [c["page"] for c in chunks] == [1, 2]
    assert chunks[1]["label"] == "p. 2"


def test_long_segment_is_split():
    chunks = retrieval.build_chunks([Segment(text="Word. " * 2000, start=10)], target=1000)
    assert len(chunks) > 5
    assert all(len(c["text"]) <= 2100 for c in chunks)
    assert all(c["start_sec"] == 10 for c in chunks)


def test_bm25_prefers_relevant_passage():
    docs = ["photosynthesis converts light energy", "jwt tokens are signed with a secret", "mitochondria make atp"]
    scores = retrieval.bm25_rank("how are JWT tokens signed?", docs)
    assert scores.index(max(scores)) == 1


# ── citations ────────────────────────────────────────────────────

def test_cited_numbers_ignore_code_and_expand_ranges():
    text = "Fact one [1]. Fact two [2, 3]. Range [5-7]. Code `arr[0]` and\n```py\nx = y[4]\n```\n[1] again."
    assert retrieval.cited_numbers(text) == [1, 2, 3, 5, 6, 7]


def test_citation_map_keeps_only_known_passages():
    idx = {1: {"chunk_id": 10}, 2: {"chunk_id": 11}}
    assert set(retrieval.citation_map("a [1] b [2] c [9]", idx)) == {"1", "2"}


# ── streaming helpers ────────────────────────────────────────────

def test_followup_filter_hides_block_across_deltas():
    f = FollowupFilter()
    out = "".join(f.feed(piece) for piece in ["Answer text", " more.\n<fol", "lowups>[\"A?\", ", "\"B?\"]</followups>"])
    out += f.remainder()
    assert out == "Answer text more.\n"
    answer, followups = f.finish()
    assert answer == "Answer text more."
    assert followups == ["A?", "B?"]


def test_followup_filter_without_block():
    f = FollowupFilter()
    out = f.feed("No suggestions <b>here</b>") + f.remainder()
    assert out == "No suggestions <b>here</b>"
    assert f.finish()[1] == []


def test_thought_status_uses_bold_heading():
    assert thought_status("**Analyzing Authentication**\n\nI am thinking") == "Analyzing Authentication…"
    assert thought_status("no heading here") is None


def test_extract_urls():
    msg = "Summarise https://youtu.be/kA2l4Rzho4Y?si=dYOL6BH7thQz1zRi and www.example.com/page."
    assert extract_urls(msg) == ["https://youtu.be/kA2l4Rzho4Y?si=dYOL6BH7thQz1zRi", "https://www.example.com/page"]


# ── video understanding ─────────────────────────────────────────

def test_plan_segments_long_videos():
    assert _plan(None) == [(None, None)]
    assert _plan(20 * 60) == [(None, None)]
    assert len(_plan(30 * 60)) == 2
    plan = _plan(100 * 60)
    assert plan[0][0] == 0 and plan[-1][1] == 100 * 60
    assert all(b > a for a, b in plan)


def test_relative_timestamp_detection():
    assert _relative([10, 300, 2000], offset=2400, length=2400) is True     # clip-relative
    assert _relative([2450, 3000, 4700], offset=2400, length=2400) is False  # already absolute
    assert _relative([5, 10], offset=0, length=2400) is False


def test_merge_shifts_relative_times():
    seg2 = VideoAnalysis.model_validate({
        "title": "T", "main_topic": "m", "summary": "s",
        "topics": [{"title": "Part", "start": "00:10", "end": "05:00", "summary": "x"}],
        "visual_notes": [{"time": "01:00", "kind": "code", "description": "d", "content": "c"}],
        "key_terms": [], "transcript": [{"time": "00:30", "text": "hi"}],
    })
    seg1 = VideoAnalysis.model_validate({
        "title": "T", "main_topic": "m", "summary": "first", "topics": [{"title": "Intro", "start": "00:00", "summary": "y"}],
        "visual_notes": [], "key_terms": [{"term": "X", "definition": "def"}], "transcript": [],
    })
    merged = _merge([(seg1, 0.0, 2400.0), (seg2, 2400.0, 2400.0)], low_res=False)
    assert merged.topics[1]["start"] == 2410.0
    assert merged.visuals[0]["time"] == 2460.0
    assert merged.transcript[0]["start"] == 2430.0
    assert merged.segments == 2


def test_video_segments_interleave_visual_notes():
    class A:
        visuals = [{"time": 50.0, "kind": "code", "description": "Shows a loop", "content": "for i in x: pass"}]
        transcript = []
    cues = [{"text": f"Sentence {i}.", "start": i * 10.0, "duration": 10.0} for i in range(12)]
    segs = _video_segments(cues, A())
    visual = [s for s in segs if s.kind == "visual"]
    assert visual and visual[0].start == 50.0
    assert "```" in visual[0].text
    assert segs == sorted(segs, key=lambda s: (s.start or 0))


# ── documents ────────────────────────────────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{p[2:]}</w:t></w:r></w:p>' if p.startswith("# ")
        else f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_extract_docx_headings_and_text():
    ex = extract_docx(_docx(["# Scheduling", "Round robin uses a time quantum."]), "notes.docx")
    text = "\n".join(s.text for s in ex.segments)
    assert "# Scheduling" in text and "time quantum" in text
    assert ex.title == "notes"


def test_extract_pptx_slides_and_notes():
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, (title, bullet) in enumerate([("Intro", "What is TCP"), ("Handshake", "SYN, SYN-ACK, ACK")], start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:p="x" xmlns:a="{a}"><a:p><a:r><a:t>{title}</a:t></a:r></a:p>'
                                                    f'<a:p><a:r><a:t>{bullet}</a:t></a:r></a:p></p:sld>')
        z.writestr("ppt/notesSlides/notesSlide2.xml", f'<n xmlns:a="{a}"><a:p><a:r><a:t>Explain the three steps.</a:t></a:r></a:p></n>')
    ex = extract_pptx(buf.getvalue(), "net.pptx")
    assert [s.page for s in ex.segments] == [1, 2]
    assert "SYN-ACK" in ex.segments[1].text and "Speaker notes" in ex.segments[1].text


def _pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with real text (Helvetica)."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({ln}) Tj 0 -16 Td" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_extract_pdf_pages():
    ex = extract_pdf(_pdf(["Binary search trees keep keys ordered.", "In-order traversal is sorted."]), "bst.pdf")
    assert ex.meta["page_count"] == 1
    assert "ordered" in ex.segments[0].text
    assert ex.segments[0].page == 1


def test_extract_text_requires_content():
    with pytest.raises(ValueError):
        extract_text("   ", "x")


def test_kind_detection():
    assert kind_for_filename("Lecture 1.MP4")[0] == "video"
    assert kind_for_filename("notes.pdf") == ("pdf", "application/pdf")
    assert kind_for_filename("talk.m4a")[0] == "audio"
    assert kind_for_filename("subs.srt")[0] == "subtitles"
    with pytest.raises(SourceError):
        kind_for_filename("old.doc")
    with pytest.raises(SourceError):
        kind_for_filename("virus.exe")


def test_mp4_duration(tmp_path):
    import struct
    mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, 1000, 90_500)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 16 + struct.pack(">I", len(mvhd) + 4) + mvhd)
    assert round(mp4_duration(str(f))) == 90


# ── studio helpers ───────────────────────────────────────────────

def test_mind_map_tree_repairs_bad_structure():
    mind = MindMap.model_validate({"title": "T", "nodes": [
        {"id": "r", "label": "Root", "parent_id": ""},
        {"id": "a", "label": "A", "parent_id": "r"},
        {"id": "b", "label": "B", "parent_id": "missing"},       # orphan → attached to root
        {"id": "c", "label": "C", "parent_id": "d"},             # cycle c<->d → broken
        {"id": "d", "label": "D", "parent_id": "c"},
        {"id": "a", "label": "dup", "parent_id": "r"},           # duplicate id ignored
    ], "links": [{"from_id": "a", "to_id": "b", "label": "uses"}, {"from_id": "a", "to_id": "zzz"}]})
    tree = build_tree(mind)
    root = tree["root"]
    assert root["label"] == "Root"
    labels = set()

    def walk(n):
        labels.add(n["label"])
        for ch in n.get("children", []):
            walk(ch)
    walk(root)
    assert labels == {"Root", "A", "B", "C", "D"}
    assert tree["links"] == [{"from": "a", "to": "b", "label": "uses"}]


# ── SSRF guard for web sources ──────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost/admin", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "file:///etc/passwd", "http://user:pass@example.com/", "http://example.com:22/",
    "http://intranet.local/", "javascript:alert(1)",
])
def test_public_url_validator_blocks_internal_targets(url):
    from app.security.ssrf import SSRFValidationError, validate_public_url

    with pytest.raises(SSRFValidationError):
        validate_public_url(url, resolve_dns=False)


def test_public_url_validator_allows_normal_sites():
    from app.security.ssrf import validate_public_url

    assert validate_public_url("https://en.wikipedia.org/wiki/Binary_search_tree", resolve_dns=False)


def test_analysis_retries_low_res_and_reports_failed_parts(monkeypatch):
    import asyncio

    from app.services import video_understanding as vu
    from app.services.llm import AIError, MediaRef, Provider

    monkeypatch.setattr(vu, "QUOTA_WAIT_SECONDS", 0)
    payload = {"title": "T", "main_topic": "m", "summary": "s", "topics": [], "visual_notes": [], "key_terms": [],
               "transcript": []}

    class Flaky(Provider):
        def __init__(self):
            self.calls = []

        async def json(self, *, system, prompt, schema, media=None, low_media_resolution=False, **kw):
            seg = (media[0].start_sec, low_media_resolution)
            self.calls.append(seg)
            if seg == (0.0, False):
                raise AIError("quota", "quota")          # first part: too big at full res → low res works
            if media[0].start_sec and media[0].start_sec >= 1200:
                raise AIError("still failing", "unavailable")  # second part fails both ways
            return schema.model_validate(payload)

    p = Flaky()
    result = asyncio.run(vu.analyze(p, MediaRef(uri="u"), duration=40 * 60, need_transcript=False))
    assert (0.0, True) in p.calls
    assert result.low_resolution is True
    assert len(result.failed_ranges) == 1 and result.failed_ranges[0]["start"] == 1200.0
