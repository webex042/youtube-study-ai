"""
A tiny imitation of the Gemini REST API — FOR TESTS ONLY.

It lets tests drive the *real* GeminiProvider (and therefore the real
google-genai SDK request/response code) without network access, by pointing
GEMINI_BASE_URL at this server. Every request body is recorded so tests can
assert on exactly what the app would send to Google.
"""

from __future__ import annotations

import base64
import io
import json
import math
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDED: list[dict] = []
UNKNOWN_MODELS = {"gemini-3.8-flash"}   # pretend this one isn't available → tests fallback
FAIL_ONCE_429 = {"enabled": False, "done": False}


def _wav(seconds: float = 0.5, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(800 * math.sin(i / 8))) for i in range(int(rate * seconds))))
    return buf.getvalue()


def _json_for(body: dict) -> dict:
    """Return a structured-output payload matching the requested schema."""
    cfg = body.get("generationConfig", {})
    schema = cfg.get("responseSchema") or cfg.get("responseJsonSchema") or {}
    props = set((schema.get("properties") or {}).keys())
    if {"visual_notes", "key_terms", "topics"} <= props:
        return {"title": "Lecture", "language": "English", "is_educational": True, "main_topic": "Testing",
                "summary": "A lecture about testing.", "topics": [{"title": "Intro", "start": "00:00", "end": "01:00",
                                                                   "summary": "Basics."}],
                "visual_notes": [{"time": "00:30", "kind": "code", "description": "Shows code", "content": "print(1)"}],
                "key_terms": [{"term": "Mock", "definition": "A stand-in", "time": "00:10"}], "formulas": [],
                "transcript": [{"time": "00:00", "text": "Hello and welcome."}, {"time": "00:20", "text": "Mocks help testing."}]}
    if {"cards"} <= props:
        return {"title": "Deck", "cards": [{"front": "Q1", "back": "A1", "difficulty": "easy", "topic": "t", "hint": ""},
                                           {"front": "Q2", "back": "A2", "difficulty": "hard", "topic": "t", "hint": ""},
                                           {"front": "Q3", "back": "A3", "difficulty": "medium", "topic": "t", "hint": ""}]}
    if {"suggested_questions", "emoji"} <= props:
        return {"title": "Notebook", "emoji": "🧪", "summary": "About **testing**.", "suggested_questions": ["Why?"]}
    if {"turns"} <= props:
        return {"title": "Episode", "summary": "s", "turns": [{"speaker": "Alex", "text": "Hi"}, {"speaker": "Sam", "text": "Hello"},
                                                              {"speaker": "Alex", "text": "So?"}, {"speaker": "Sam", "text": "Right."}]}
    return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, code: int, payload, headers: dict | None = None, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        RECORDED.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
        if "/files/" in self.path:
            name = self.path.split("/files/")[1].split("?")[0]
            return self._send(200, {"name": f"files/{name}", "uri": f"https://mock/files/{name}", "mimeType": "video/mp4",
                                    "state": "ACTIVE"})
        return self._send(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self.path
        try:
            body = json.loads(raw) if raw and raw[:1] in (b"{", b"[") else {}
        except ValueError:
            body = {}
        RECORDED.append({"method": "POST", "path": path, "body": body, "headers": dict(self.headers), "size": len(raw)})

        if path.startswith("/upload/v1beta/files"):
            base = f"http://{self.headers.get('Host')}"
            return self._send(200, {}, {"x-goog-upload-url": f"{base}/upload-session/abc123"})
        if path.startswith("/upload-session/"):
            return self._send(200, {"file": {"name": "files/abc123", "uri": "https://mock/files/abc123",
                                             "mimeType": "video/mp4", "state": "PROCESSING"}},
                              {"x-goog-upload-status": "final"})

        model = path.split("/models/")[1].split(":")[0] if "/models/" in path else ""
        if model in UNKNOWN_MODELS:
            return self._send(404, {"error": {"code": 404, "message": f"models/{model} is not found for API version v1beta",
                                              "status": "NOT_FOUND"}})
        if FAIL_ONCE_429["enabled"] and not FAIL_ONCE_429["done"]:
            FAIL_ONCE_429["done"] = True
            return self._send(429, {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})
        if body.get("contents") and "token type bad" in json.dumps(body.get("contents")):
            # The shape Google returns when it rejects a key's type (seen with some "AQ." keys).
            return self._send(401, {"error": {
                "code": 401, "status": "UNAUTHENTICATED",
                "message": "Request had invalid authentication credentials. Expected OAuth 2 access token, "
                           "login cookie or other valid authentication credential.",
                "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                             "reason": "ACCESS_TOKEN_TYPE_UNSUPPORTED", "domain": "googleapis.com"}]}})
        if body.get("contents") and "api key bad" in json.dumps(body.get("contents")):
            return self._send(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                              "status": "INVALID_ARGUMENT"}})

        cfg = body.get("generationConfig", {})
        if ":streamGenerateContent" in path:
            chunks = []
            thinking = cfg.get("thinkingConfig") or {}
            if thinking.get("includeThoughts") or thinking.get("include_thoughts"):
                chunks.append({"candidates": [{"content": {"role": "model", "parts": [
                    {"text": "**Analyzing Authentication**\n\nThinking about it.", "thought": True}]}}]})
            for piece in ("The answer ", "uses JWT [1].\n", "<followups>[\"Next?\"]</followups>"):
                chunks.append({"candidates": [{"content": {"role": "model", "parts": [{"text": piece}]}}]})
            chunks[-1]["candidates"][0]["finishReason"] = "STOP"
            data = b"".join(f"data: {json.dumps(c)}\r\n\r\n".encode() for c in chunks)
            return self._send(200, data, content_type="text/event-stream")

        if cfg.get("responseModalities") == ["AUDIO"]:
            audio = base64.b64encode(_wav()).decode()
            return self._send(200, {"candidates": [{"content": {"role": "model", "parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": audio}}]}, "finishReason": "STOP"}]})

        if body.get("tools"):
            return self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": "Overview of sources."}]},
                                                    "finishReason": "STOP",
                                                    "groundingMetadata": {"groundingChunks": [
                                                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x1",
                                                                 "title": "example.com", "domain": "example.com"}}]}}]})

        if cfg.get("responseMimeType") == "application/json":
            text = json.dumps(_json_for(body))
        else:
            text = "OK"
        return self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                                                "finishReason": "STOP"}],
                                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
