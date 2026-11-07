"""
Drives the real GeminiProvider + google-genai SDK against a local mock of the
Gemini REST API, to check the exact requests the app sends and that responses
(streams with thought summaries, JSON, audio, grounding, uploads) are parsed.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests import mock_gemini


@pytest.fixture(scope="module")
def provider():
    server, base = mock_gemini.start()
    os.environ["GEMINI_BASE_URL"] = base
    from app.services.llm import GeminiProvider

    p = GeminiProvider("test-key-123")
    yield p
    server.shutdown()
    os.environ.pop("GEMINI_BASE_URL", None)


def run(coro):
    return asyncio.run(coro)


def last(path_part: str) -> dict:
    return [r for r in mock_gemini.RECORDED if path_part in r["path"]][-1]


def g(obj: dict, camel: str):
    """Read a field the SDK may send in camelCase or snake_case (the API accepts both)."""
    snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in camel)
    return obj[camel] if camel in obj else obj[snake]


def test_stream_with_thoughts_and_model_fallback(provider):
    async def go():
        events = []
        async for ev in provider.stream(system="sys", contents=[{"role": "user", "text": "hi"}], thinking=True):
            events.append(ev)
        return events

    events = run(go())
    kinds = [e.kind for e in events]
    assert "thought" in kinds and "text" in kinds
    assert events[0].text.startswith("**Analyzing Authentication**")
    text = "".join(e.text for e in events if e.kind == "text")
    assert "uses JWT [1]" in text
    # First model 404s in the mock → the provider falls back and remembers it.
    paths = [r["path"] for r in mock_gemini.RECORDED if ":streamGenerateContent" in r["path"]]
    assert any("gemini-3.8-flash" in p for p in paths)
    assert "gemini-3.6-flash" in paths[-1]
    assert provider._working["default"] == "gemini-3.6-flash"
    body = last(":streamGenerateContent")["body"]
    assert g(body["generationConfig"]["thinkingConfig"], "includeThoughts") is True
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    # The key goes in a header, never the URL.
    rec = last(":streamGenerateContent")
    assert "test-key-123" not in rec["path"]
    assert rec["headers"].get("x-goog-api-key") == "test-key-123"


def test_json_video_analysis_request_shape(provider):
    from app.models.schemas import VideoAnalysis
    from app.services.llm import MediaRef

    ref = MediaRef(uri="https://www.youtube.com/watch?v=abcdefghijk", start_sec=0, end_sec=2400)
    result = run(provider.json(system="s", prompt="Analyse", schema=VideoAnalysis, media=[ref],
                               low_media_resolution=True, timeout=900))
    assert result.title == "Lecture"
    assert result.transcript[1].text == "Mocks help testing."
    body = last(":generateContent")["body"]
    part = body["contents"][0]["parts"][0]
    assert g(part["fileData"], "fileUri") == "https://www.youtube.com/watch?v=abcdefghijk"
    assert g(part["videoMetadata"], "startOffset") == "0s"
    assert g(part["videoMetadata"], "endOffset") == "2400s"
    assert body["contents"][0]["parts"][1]["text"] == "Analyse"  # text after the video, as recommended
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert "visual_notes" in (cfg.get("responseSchema") or cfg.get("responseJsonSchema"))["properties"]


def test_every_studio_schema_is_accepted_by_the_sdk(provider):
    """The SDK must be able to convert each Pydantic schema into a request."""
    from app.models import schemas as S

    for schema in (S.SourceGuide, S.NotebookGuide, S.ChatSourceReply, S.FlashcardSet, S.QuizSet, S.MindMap,
                   S.Infographic, S.SlideDeck, S.DataTable, S.AudioScript, S.VideoScript, S.VideoAnalysis):
        cfg = provider._config(system="s", max_tokens=100, response_mime_type="application/json", response_schema=schema)
        from google.genai import _transformers as tr

        converted = tr.t_schema(provider.client._api_client, schema)
        assert converted is not None, schema.__name__
        assert cfg.response_schema is schema


def test_tts_multi_speaker_request(provider):
    clip = run(provider.tts([("Alex", "Hi there"), ("Sam", "Hello")], {"Alex": "Puck", "Sam": "Kore"}))
    assert clip.sample_rate == 24000 and len(clip.pcm) > 1000
    body = last(":generateContent")["body"]
    cfg = body["generationConfig"]
    assert cfg["responseModalities"] == ["AUDIO"]
    voices = g(g(cfg["speechConfig"], "multiSpeakerVoiceConfig"), "speakerVoiceConfigs")
    assert {v["speaker"] for v in voices} == {"Alex", "Sam"}
    parts = body["contents"][0]["parts"]
    assert parts[0]["speechMetadata"]["speaker"] == "Alex"
    assert "-tts" in last(":generateContent")["path"]


def test_web_search_uses_grounding(provider):
    res = run(provider.web_search("binary trees"))
    assert res["results"][0]["url"].startswith("https://vertexaisearch.cloud.google.com/")
    body = last(":generateContent")["body"]
    assert "googleSearch" in body["tools"][0]


def test_upload_media_resumable_protocol(provider, tmp_path):
    f = tmp_path / "lecture.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 5000)
    ref = run(provider.upload_media(str(f), "video/mp4", "lecture"))
    assert ref.uri == "https://mock/files/abc123"
    starts = [r for r in mock_gemini.RECORDED if r["path"].startswith("/upload/v1beta/files")]
    assert starts and starts[-1]["headers"].get("X-Goog-Upload-Protocol") == "resumable"
    assert any(r["path"].startswith("/upload-session/") for r in mock_gemini.RECORDED)


def test_error_mapping_bad_key(provider):
    from app.services.llm import AIError

    with pytest.raises(AIError) as err:
        run(provider.text(system="s", prompt="api key bad"))
    assert err.value.code == "auth"
    assert "GEMINI_API_KEY" in err.value.message


def test_error_mapping_unsupported_key_type(provider):
    from app.services.llm import AIError

    with pytest.raises(AIError) as err:
        run(provider.text(system="s", prompt="token type bad"))
    assert err.value.code == "auth"
    assert "ACCESS_TOKEN_TYPE_UNSUPPORTED" in err.value.message
    assert "AQ." in err.value.message


def test_error_mapping_network_failures(provider):
    import httpx

    blocked = provider._map_error(httpx.ProxyError("403 Forbidden"))
    assert blocked.code == "network" and "proxy" in blocked.message.lower()
    offline = provider._map_error(httpx.ConnectError("Name or service not known"))
    assert offline.code == "network" and offline.retryable


def test_rate_limit_is_retried(provider):
    mock_gemini.FAIL_ONCE_429.update(enabled=True, done=False)
    try:
        out = run(provider.text(system="s", prompt="hello"))
    finally:
        mock_gemini.FAIL_ONCE_429["enabled"] = False
    assert out == "OK"
