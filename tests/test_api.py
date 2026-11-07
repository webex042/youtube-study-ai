"""
End-to-end API tests with the deterministic FakeProvider.

Covers: notebooks, every source type path that works offline, the YouTube
pipeline (captions + video analysis, and the honest failure when neither is
available), chat streaming with thoughts/citations/follow-ups, links pasted in
chat, all ten Studio tools, notes, bookmarks, search, progress, export, and the
behaviour when no AI key is configured.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.services import llm
from tests.fake_provider import FakeProvider
from tests.test_units import _docx


@pytest.fixture(scope="module")
def client():
    llm.set_provider_override(FakeProvider())
    from app.main import app

    with TestClient(app) as c:
        yield c
    llm.set_provider_override(None)


def wait_for(fn, timeout=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        value = fn()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out")


def new_notebook(client) -> str:
    return client.post("/api/notebooks", json={}).json()["notebook"]["id"]


def state(client, nid):
    return client.get(f"/api/notebooks/{nid}/state").json()


def sse_events(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def add_paste(client, nid, text="JSON Web Tokens are signed with a secret key. " * 20):
    src = client.post(f"/api/notebooks/{nid}/sources/text", json={"title": "JWT notes", "text": text}).json()["source"]
    wait_for(lambda: state(client, nid)["sources"][0]["status"] == "ready")
    return src


def test_notebook_crud(client):
    nid = new_notebook(client)
    assert client.patch(f"/api/notebooks/{nid}", json={"title": "Biology"}).json()["notebook"]["title"] == "Biology"
    listed = client.get("/api/notebooks").json()["notebooks"]
    assert any(n["id"] == nid for n in listed)
    assert client.delete(f"/api/notebooks/{nid}").json()["ok"]
    assert client.get(f"/api/notebooks/{nid}").status_code == 404


def test_paste_source_then_guide(client):
    nid = new_notebook(client)
    add_paste(client, nid)
    nb = wait_for(lambda: (s := state(client, nid))["notebook"]["guide_status"] == "ready" and s)["notebook"]
    assert nb["title"] == "MERN Stack Photo Storage Development Guide"   # auto-title while untitled
    assert nb["emoji"] == "💻" and len(nb["questions"]) == 3
    detail = client.get(f"/api/sources/{state(client, nid)['sources'][0]['id']}").json()["source"]
    assert detail["chunks"] and detail["guide"]["summary"]


def test_user_title_is_never_overwritten(client):
    nid = new_notebook(client)
    client.patch(f"/api/notebooks/{nid}", json={"title": "My OS notes"})
    add_paste(client, nid)
    wait_for(lambda: state(client, nid)["notebook"]["guide_status"] == "ready")
    assert state(client, nid)["notebook"]["title"] == "My OS notes"


def test_youtube_with_captions_and_visual_analysis(client, monkeypatch):
    from app.services import sources as src_mod

    async def fake_meta(video_id):
        return {"video_id": video_id, "title": "Real Title From YouTube", "channel": "Teacher", "duration": 900}

    def fake_transcript(video_id, langs):
        return {"snippets": [{"text": f"Line {i} about authentication.", "start": i * 20.0, "duration": 20.0} for i in range(30)],
                "language": "English", "language_code": "en", "is_generated": False, "truncated": False}

    monkeypatch.setattr(src_mod, "youtube_metadata", fake_meta)
    monkeypatch.setattr(src_mod, "fetch_transcript", fake_transcript)
    nid = new_notebook(client)
    r = client.post(f"/api/notebooks/{nid}/sources/url", json={"urls": ["https://youtu.be/kA2l4Rzho4Y?si=abc"]})
    assert r.status_code == 200
    src = wait_for(lambda: (s := state(client, nid)["sources"][0])["status"] in ("ready", "error") and s)
    assert src["status"] == "ready", src["error"]
    assert src["title"] == "Real Title From YouTube"
    assert src["meta"]["captions"] == "manual" and src["meta"]["visual_analysis"] is True
    detail = client.get(f"/api/sources/{src['id']}").json()["source"]
    assert any(c["text"].startswith("[On screen") for c in detail["chunks"])
    assert detail["chunks"][0]["start_sec"] == 0.0
    assert detail["guide"]["chapters"][1]["start"] == 130.0   # "02:10"
    # Same video twice is refused.
    again = client.post(f"/api/notebooks/{nid}/sources/url", json={"urls": ["https://www.youtube.com/watch?v=kA2l4Rzho4Y"]})
    assert again.status_code == 400


def test_youtube_without_any_content_fails_honestly(client, monkeypatch):
    from app.services import sources as src_mod
    from app.services.youtube_service import TranscriptError

    async def fake_meta(video_id):
        return {"video_id": video_id, "unavailable": True}

    def no_transcript(video_id, langs):
        raise TranscriptError("Subtitles are disabled for this video.")

    llm.set_provider_override(None)  # no AI → no video analysis either
    monkeypatch.setattr(src_mod, "youtube_metadata", fake_meta)
    monkeypatch.setattr(src_mod, "fetch_transcript", no_transcript)
    try:
        nid = new_notebook(client)
        client.post(f"/api/notebooks/{nid}/sources/url", json={"urls": ["https://youtu.be/dQw4w9WgXcQ"]})
        src = wait_for(lambda: (s := state(client, nid)["sources"][0])["status"] in ("ready", "error") and s)
    finally:
        llm.set_provider_override(FakeProvider())
    assert src["status"] == "error"
    assert "nothing was imported" in src["error"] and "Subtitles are disabled" in src["error"]
    assert client.get(f"/api/sources/{src['id']}").json()["source"]["chunks"] == []


def test_chat_stream_thoughts_citations_followups(client):
    nid = new_notebook(client)
    add_paste(client, nid)
    r = client.post(f"/api/notebooks/{nid}/chat", json={"message": "Explain how the backend handles authentication."})
    events = sse_events(r)
    types = [e["type"] for e in events]
    assert types[0] == "user" and types[-1] == "done"
    statuses = [e["text"] for e in events if e["type"] == "status"]
    assert "Searching your sources…" in statuses and "Analyzing Authentication…" in statuses
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "<followups>" not in streamed
    msg = events[-1]["message"]
    assert msg["followups"] == ["How do image uploads work with ImageKit?", "Explain how the frontend manages selected items."]
    assert "1" in msg["citations"] and msg["citations"]["1"]["source_title"] == "JWT notes"
    assert msg["thoughts"].startswith("**Getting the Gist**")
    history = client.get(f"/api/notebooks/{nid}/messages").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_link_pasted_in_chat_adds_source(client, monkeypatch):
    from app.services import sources as src_mod

    async def fake_meta(video_id):
        return {"video_id": video_id, "title": "Build Full Stack Photo Storage Using MERN Stack & ImageKit"}

    def fake_transcript(video_id, langs):
        return {"snippets": [{"text": "Welcome to the tutorial.", "start": 0.0, "duration": 5.0}], "language": "English",
                "is_generated": True, "truncated": False}

    monkeypatch.setattr(src_mod, "youtube_metadata", fake_meta)
    monkeypatch.setattr(src_mod, "fetch_transcript", fake_transcript)
    nid = new_notebook(client)
    r = client.post(f"/api/notebooks/{nid}/chat", json={"message": "https://youtu.be/kA2l4Rzho4Y?si=dYOL6BH7thQz1zRi"})
    events = sse_events(r)
    statuses = [e["text"] for e in events if e["type"] == "status"]
    assert "Analyzing URL input…" in statuses and "Adding sources…" in statuses
    assert any(e["type"] == "sources" for e in events)
    done = events[-1]["message"]
    assert "I've added the YouTube video" in done["content"]
    assert len(done["followups"]) == 3
    assert state(client, nid)["sources"][0]["status"] == "ready"


def test_all_studio_tools(client):
    nid = new_notebook(client)
    add_paste(client, nid)
    kinds = {"notes": {"style": "exam"}, "report": {"format": "faq"}, "flashcards": {"count": "fewer"}, "quiz": {"difficulty": "hard"},
             "mindmap": {}, "infographic": {}, "slides": {}, "datatable": {"kind": "glossary"}, "audio": {"language": "hi"},
             "video": {}}
    ids = {}
    for kind, options in kinds.items():
        r = client.post(f"/api/notebooks/{nid}/studio", json={"type": kind, "options": options})
        assert r.status_code == 200, r.text
        ids[kind] = r.json()["artifact"]["id"]
    wait_for(lambda: all(a["status"] != "processing" for a in state(client, nid)["artifacts"]), timeout=60)
    for kind, aid in ids.items():
        art = client.get(f"/api/artifacts/{aid}").json()["artifact"]
        assert art["status"] == "ready", (kind, art["error"])
    assert "markdown" in client.get(f"/api/artifacts/{ids['notes']}").json()["artifact"]["content"]
    quiz = client.get(f"/api/artifacts/{ids['quiz']}").json()["artifact"]["content"]["questions"]
    assert all(len(q["options"]) == 4 and 0 <= q["answer"] < 4 for q in quiz)
    audio = client.get(f"/api/artifacts/{ids['audio']}").json()["artifact"]
    assert audio["title"] and audio["content"]["language"].startswith("Hindi")
    media = client.get(f"/api/artifacts/{ids['audio']}/media")
    assert media.status_code == 200 and media.content[:4] == b"RIFF"
    ranged = client.get(f"/api/artifacts/{ids['audio']}/media", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    video = client.get(f"/api/artifacts/{ids['video']}").json()["artifact"]["content"]
    assert video["scenes"][1]["start"] > 0
    # Progress endpoints
    client.post(f"/api/artifacts/{ids['flashcards']}/cards/0", json={"status": "understood"})
    client.post(f"/api/artifacts/{ids['quiz']}/attempts", json={"score": 2, "total": 3, "answers": [0, 1, 2]})
    progress = client.get(f"/api/notebooks/{nid}/progress").json()["progress"]
    assert progress["flashcards"]["understood"] == 1 and progress["quiz_average"] == 67
    assert progress["streak"] >= 1
    # Export includes Studio output
    export = client.get(f"/api/notebooks/{nid}/export")
    assert export.status_code == 200 and "Authentication Quiz" in export.text and "✅" in export.text
    # Delete removes the audio file too
    assert client.delete(f"/api/artifacts/{ids['audio']}").json()["ok"]


def test_notes_bookmarks_search(client):
    nid = new_notebook(client)
    add_paste(client, nid)
    note = client.post(f"/api/notebooks/{nid}/notes", json={"title": "Mine", "content": "Rotations keep AVL trees balanced."}).json()["note"]
    client.patch(f"/api/notes/{note['id']}", json={"content": "==Rotations== keep AVL trees balanced."})
    assert client.get(f"/api/notes/{note['id']}").json()["note"]["content"].startswith("==Rotations==")
    saved = client.post(f"/api/notebooks/{nid}/notes", json={"content": "## Answer\nJWT is a token.", "kind": "saved_response"}).json()["note"]
    assert saved["title"] == "Answer"
    client.post(f"/api/notebooks/{nid}/bookmarks", json={"text": "JWT = signed token", "origin_type": "note"})
    assert len(client.get(f"/api/notebooks/{nid}/bookmarks").json()["bookmarks"]) == 1
    results = client.get(f"/api/notebooks/{nid}/search", params={"q": "token"}).json()["results"]
    kinds = {r["type"] for r in results}
    assert {"source", "note"} <= kinds
    conv = client.post(f"/api/notes/{note['id']}/to-source").json()["source"]
    assert conv["kind"] == "note"


def test_docx_upload_and_limits(client):
    nid = new_notebook(client)
    files = {"file": ("os_notes.docx", _docx(["# Scheduling", "Round robin gives each process a time quantum. " * 5]),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post(f"/api/notebooks/{nid}/sources/upload", files=files)
    assert r.status_code == 200, r.text
    wait_for(lambda: state(client, nid)["sources"][0]["status"] == "ready")
    bad = client.post(f"/api/notebooks/{nid}/sources/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and "Unsupported file type" in bad.json()["error"]
    wrong_type = client.post(f"/api/notebooks/{nid}/sources/text", content=b"x", headers={"Content-Type": "text/plain"})
    assert wrong_type.status_code == 415


def test_website_source_cannot_reach_internal_hosts(client):
    nid = new_notebook(client)
    client.post(f"/api/notebooks/{nid}/sources/url", json={"urls": ["http://127.0.0.1:8000/admin"]})
    src = wait_for(lambda: (s := state(client, nid)["sources"][0])["status"] in ("ready", "error") and s)
    assert src["status"] == "error"
    assert "not permitted" in src["error"]


def test_without_ai_key_everything_says_so(client):
    llm.set_provider_override(None)
    try:
        nid = new_notebook(client)
        add_paste(client, nid)
        events = sse_events(client.post(f"/api/notebooks/{nid}/chat", json={"message": "What is JWT?"}))
        assert "GEMINI_API_KEY" in events[-1]["message"]["content"]
        assert events[-1]["message"]["status"] == "error"
        r = client.post(f"/api/notebooks/{nid}/studio", json={"type": "quiz"})
        assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["error"]
        status = client.get("/api/system/status").json()["status"]
        assert status["configured"] is False
        assert client.post("/api/system/test-ai").json()["ok"] is False
    finally:
        llm.set_provider_override(FakeProvider())


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp
    assert "frame-src https://www.youtube-nocookie.com" in csp
    assert r.headers["x-frame-options"] == "DENY"
