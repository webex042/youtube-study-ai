"""
A deterministic stand-in for the AI provider — FOR TESTS ONLY.

It lets the test-suite (and the local UI test server in tests/run_test_server.py)
exercise every code path — streaming with thought summaries, citations,
structured Studio outputs, text-to-speech — without network access or an API
key. It is never used by the real app: the app only ever talks to Gemini or
OpenAI, and shows a configuration message when no key is set.
"""

from __future__ import annotations

import asyncio
import math
import struct
from typing import Any

from app.models import schemas as S
from app.services.llm import AudioClip, MediaRef, Provider, StreamEvent

SAMPLE_TRANSCRIPT = [
    ("00:00", "Welcome! In this tutorial we build a full stack photo storage app using the MERN stack and ImageKit."),
    ("00:45", "First we set up the Express server and connect to MongoDB using Mongoose."),
    ("02:10", "Next, the user model: the schema has name, email and password fields. Email is unique and lowercase."),
    ("04:30", "Passwords are hashed with bcrypt before they are stored, using bcrypt.hash with ten salt rounds."),
    ("06:05", "After registration we sign a JSON Web Token with the user id, valid for thirty days."),
    ("08:20", "The login controller finds the user by email, selects the password field, and compares hashes."),
    ("10:15", "A requireAuth middleware verifies the token from the Authorization header and sets req.user."),
    ("12:40", "Images are uploaded to ImageKit; we store the URL and fileId in MongoDB inside albums."),
    ("15:00", "On the React side, the token is kept in localStorage and attached to Axios requests."),
]


class FakeProvider(Provider):
    name = "fake"
    supports_media = True
    supports_tts = True
    supports_search = True
    supports_thoughts = True
    last_model = "fake-model"

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []

    async def stream(self, *, system, contents, tier="default", thinking=True, max_tokens=12000):
        self.calls.append("stream")
        prompt = contents[-1]["text"] if contents else ""
        if thinking:
            for heading in ("Getting the Gist", "Analyzing Authentication", "Reviewing Backend Details"):
                await asyncio.sleep(self.delay)
                yield StreamEvent("thought", f"**{heading}**\n\nI'm looking at how the sources describe this.\n")
        has_sources = "SOURCE_MATERIAL" in prompt
        if "Output only the Markdown document" in prompt:
            body = (
                "# MERN Photo Storage — Study Notes\n\nThis lecture builds a **full-stack photo storage app** [1].\n\n"
                "## 🎯 Learning objectives\n- Explain JWT authentication [5]\n- Hash passwords with bcrypt [4]\n\n"
                "## 📚 Core concepts\n### 1. Password hashing (▶ 04:30)\n**What it is** — storing a one-way hash [4].\n\n"
                "```js\nconst hash = await bcrypt.hash(password, 10);\n```\n\n"
                "### 2. JSON Web Tokens (▶ 06:05)\nA signed token carrying the user id [5]. The expiry is $t = 30$ days.\n\n"
                "| Term | Meaning | Example |\n|---|---|---|\n| JWT | Signed token | `jwt.sign()` |\n\n"
                "## ✅ Key takeaways\n- Never store plain passwords [4]\n- Protect routes with middleware [7]\n"
            ) * 1
            chunks = [body[i:i + 80] for i in range(0, len(body), 80)]
        else:
            answer = (
                "The backend handles authentication using **Express.js**, **MongoDB (via Mongoose)**, **bcryptjs** "
                "for password encryption, and **JSON Web Tokens (JWT)** for stateless session authorization [1].\n\n"
                "### 1. User Model & Schema (`user.js`)\n"
                "- The Mongoose schema defines `name`, `email`, and `password` fields [3].\n"
                "- The `email` field is set to `unique: true`, `lowercase: true` [3].\n\n"
                "### 2. User Registration\n"
                "- **Encryption:** Hashes the password using `bcrypt.hash(password, 10)` before storage [4].\n"
                "- **Token Signing:** signs a JWT with a **30-day expiration** [5][6] (▶ 06:05).\n\n"
                "💡 Would you like to explore how the React frontend stores this JWT in `localStorage`?\n"
                "<followups>[\"How do image uploads work with ImageKit?\", \"Explain how the frontend manages selected items.\"]</followups>"
            ) if has_sources else (
                "Here is a general explanation (no sources yet). Add your lecture with **Add sources** to ground my answers.\n\n"
                "💡 Want me to suggest sources?\n<followups>[\"What should I learn first?\"]</followups>"
            )
            chunks = [answer[i:i + 24] for i in range(0, len(answer), 24)]
        for piece in chunks:
            await asyncio.sleep(self.delay / 4)
            yield StreamEvent("text", piece)

    async def text(self, *, system, prompt, tier="default", max_tokens=16000, timeout=None):
        self.calls.append("text")
        return ("**Learning path (general knowledge):**\n- Basics\n- Practice\n\n💡 Add a source to begin.\n"
                "<followups>[\"Where do I start?\"]</followups>")

    async def json(self, *, system, prompt, schema, tier="default", media=None, max_tokens=32000, timeout=None,
                   low_media_resolution=False):
        self.calls.append(f"json:{schema.__name__}")
        await asyncio.sleep(self.delay)
        return schema.model_validate(_fake_payload(schema, prompt, media))

    async def upload_media(self, path, mime_type, display_name):
        return MediaRef(uri=f"files/fake-{display_name}", mime_type=mime_type)

    async def tts(self, turns, voices, *, style=""):
        self.calls.append("tts")
        words = sum(len(t.split()) for _, t in turns)
        seconds = max(0.5, min(4.0, words / 12))
        rate = 24000
        frames = b"".join(struct.pack("<h", int(1200 * math.sin(2 * math.pi * 220 * i / rate)))
                          for i in range(int(rate * seconds)))
        return AudioClip(frames, rate)

    async def web_search(self, query, *, youtube_only=False, deep=False):
        base = [
            {"title": f"{query} — crash course", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "domain": "youtube.com"},
            {"title": f"Understanding {query}", "url": "https://developer.mozilla.org/en-US/docs/Web", "domain": "developer.mozilla.org"},
        ]
        return {"summary": f"These sources introduce {query}.", "results": base[:1] if youtube_only else base}

    async def ping(self):
        return {"ok": True, "model": self.last_model}


def _fake_payload(schema, prompt: str, media) -> dict[str, Any]:
    name = schema.__name__
    if name == "VideoAnalysis":
        want_transcript = "COMPLETE transcript" in prompt
        return {
            "title": "Build Full Stack Photo Storage Using MERN Stack & ImageKit",
            "language": "English", "is_educational": True,
            "main_topic": "Full-stack MERN app with JWT auth and ImageKit uploads",
            "summary": "The instructor builds a photo storage app with React, Express, MongoDB and ImageKit, covering "
                       "authentication with bcrypt and JWT, protected routes, and image uploads organised into albums.",
            "topics": [
                {"title": "Project setup", "start": "00:00", "end": "02:10", "summary": "Express server and MongoDB connection."},
                {"title": "Authentication", "start": "02:10", "end": "12:40", "summary": "User model, bcrypt hashing, JWT signing and verification."},
                {"title": "Image uploads", "start": "12:40", "end": "15:00", "summary": "Uploading to ImageKit and storing metadata."},
            ],
            "visual_notes": [
                {"time": "04:35", "kind": "code", "description": "Registration controller hashing the password.",
                 "content": "const hashed = await bcrypt.hash(password, 10);\nconst user = await User.create({ name, email, password: hashed });"},
                {"time": "10:20", "kind": "code", "description": "requireAuth middleware.",
                 "content": "const token = req.headers.authorization?.split(' ')[1];\nreq.user = jwt.verify(token, process.env.JWT_SECRET);"},
            ],
            "key_terms": [
                {"term": "JWT", "definition": "A signed token that proves who the user is.", "time": "06:05"},
                {"term": "bcrypt", "definition": "A slow hashing function for passwords.", "time": "04:30"},
            ],
            "formulas": [],
            "transcript": [{"time": t, "text": x} for t, x in SAMPLE_TRANSCRIPT] if want_transcript else [],
        }
    if name == "SourceGuide":
        return {"summary": "This source explains **authentication** and **image uploads** in a MERN app.",
                "key_topics": ["Express setup", "JWT auth", "ImageKit uploads"],
                "suggested_questions": ["How is the password hashed?", "How do uploads work?", "What does requireAuth do?"]}
    if name == "NotebookGuide":
        return {"title": "MERN Stack Photo Storage Development Guide", "emoji": "💻",
                "summary": "The provided text outlines the development of a **full-stack photo storage application** using "
                           "the **MERN stack** and **ImageKit**. It covers building both the front-end user interface with "
                           "**React** and the backend server using **Node.js, Express, and MongoDB**. Key features implemented "
                           "include **user authentication** via JSON Web Tokens, **media upload and management**, **album "
                           "organization**, and **storage tracking**.",
                "suggested_questions": ["How do I upload photos to an album?", "Explain how the backend handles authentication.",
                                        "What does the photo grid component do?"]}
    if name == "ChatSourceReply":
        return {"reply": "I've added the YouTube video **\"Build Full Stack Photo Storage Using MERN Stack & ImageKit | Full Stack "
                         "Project in React Js\"** to your notebook sources.\n\nYou can now ask questions about the video content, "
                         "request a detailed overview, or explore specific implementation steps and technologies covered in the "
                         "tutorial.\n\n💡 Would you like a key summary of the project architecture and technologies used in this video?",
                "suggested_questions": ["How do I upload photos to an album?", "Explain how the backend handles authentication.",
                                        "What does the photo grid component do?"]}
    if name == "FlashcardSet":
        return {"title": "MERN Auth Flashcards", "cards": [
            {"front": "What does bcrypt do?", "back": "Hashes passwords one-way with a salt.", "difficulty": "easy", "topic": "Security", "hint": "Think hashing"},
            {"front": "What is stored in the JWT payload here?", "back": "The user id (plus email and name).", "difficulty": "medium", "topic": "JWT", "hint": ""},
            {"front": "How long is the token valid?", "back": "30 days.", "difficulty": "easy", "topic": "JWT", "hint": ""},
            {"front": "What does requireAuth set?", "back": "`req.user` from the verified token.", "difficulty": "hard", "topic": "Middleware", "hint": ""},
        ]}
    if name == "QuizSet":
        return {"title": "Authentication Quiz", "questions": [
            {"question": "Which library hashes passwords?", "options": ["bcryptjs", "axios", "multer", "cors"], "answer_index": 0,
             "explanation": "bcryptjs hashes passwords; axios is an HTTP client.", "difficulty": "easy", "topic": "Security", "hint": "b…"},
            {"question": "Where does the React app keep the JWT?", "options": ["Cookies only", "localStorage", "MongoDB", "ImageKit"], "answer_index": 1,
             "explanation": "The tutorial stores it in localStorage and attaches it to Axios requests.", "difficulty": "medium", "topic": "Frontend", "hint": ""},
            {"question": "What does `.select('+password')` do?", "options": ["Hides the password", "Includes the hidden password field", "Deletes it", "Hashes it"],
             "answer_index": 1, "explanation": "The schema hides password by default; this overrides it.", "difficulty": "hard", "topic": "Security", "hint": ""},
        ]}
    if name == "MindMap":
        return {"title": "MERN Photo App", "nodes": [
            {"id": "n1", "label": "MERN Photo App", "parent_id": "", "note": "The whole project"},
            {"id": "n2", "label": "Backend", "parent_id": "n1", "note": "Express + MongoDB"},
            {"id": "n3", "label": "Frontend", "parent_id": "n1", "note": "React UI"},
            {"id": "n4", "label": "Authentication", "parent_id": "n2", "note": "bcrypt + JWT"},
            {"id": "n5", "label": "Uploads", "parent_id": "n2", "note": "ImageKit"},
            {"id": "n6", "label": "bcrypt hashing", "parent_id": "n4", "note": "10 salt rounds"},
            {"id": "n7", "label": "JWT (30 days)", "parent_id": "n4", "note": "Signed token"},
            {"id": "n8", "label": "Photo grid", "parent_id": "n3", "note": "Displays images"},
        ], "links": [{"from_id": "n8", "to_id": "n5", "label": "shows"}]}
    if name == "Infographic":
        return {"title": "MERN Photo Storage at a Glance", "subtitle": "How the app authenticates users and stores images",
                "key_stats": [{"value": "30 days", "label": "JWT lifetime"}, {"value": "10", "label": "bcrypt salt rounds"}],
                "sections": [
                    {"heading": "Authentication", "icon": "lock", "points": ["bcrypt hashes passwords", "JWT proves identity"], "stat_value": "", "stat_label": ""},
                    {"heading": "Storage", "icon": "cloud", "points": ["Images on ImageKit", "Metadata in MongoDB"], "stat_value": "", "stat_label": ""},
                    {"heading": "Frontend", "icon": "web", "points": ["React + Axios", "Token in localStorage"], "stat_value": "", "stat_label": ""},
                    {"heading": "Backend", "icon": "dns", "points": ["Express routes", "requireAuth middleware"], "stat_value": "", "stat_label": ""},
                ],
                "steps_title": "Login flow", "steps": ["Find user", "Compare hash", "Sign JWT", "Return token"],
                "takeaway": "Never store plain passwords — hash them and use signed tokens."}
    if name == "SlideDeck":
        return {"title": "MERN Photo Storage", "subtitle": "Auth & uploads", "slides": [
            {"layout": "title", "title": "MERN Photo Storage", "subtitle": "Building a full-stack app", "notes": "Intro."},
            {"layout": "bullets", "title": "Authentication", "bullets": ["bcrypt hashing", "JWT signing", "Protected routes"], "notes": "Explain auth."},
            {"layout": "two_column", "title": "JWT vs Sessions", "left_title": "JWT", "left": ["Stateless", "Client stores token"],
             "right_title": "Sessions", "right": ["Server state", "Cookie id"], "notes": "Compare."},
            {"layout": "code", "title": "Hashing", "code": "await bcrypt.hash(password, 10)", "code_language": "js", "bullets": ["10 salt rounds"], "notes": ""},
            {"layout": "summary", "title": "Summary", "bullets": ["Hash passwords", "Sign tokens", "Verify on each request"], "notes": "Recap."},
        ]}
    if name == "DataTable":
        return {"title": "Key Concepts", "description": "Core ideas from the lecture.", "columns": ["Concept", "What it is", "Example"],
                "rows": [["JWT", "Signed token", "jwt.sign()"], ["bcrypt", "Password hashing", "bcrypt.hash()"], ["ImageKit", "Media CDN", "upload()"]],
                "notes": ""}
    if name == "AudioScript":
        return {"title": "Unpacking MERN Authentication", "summary": "Two hosts explain auth.", "turns": [
            {"speaker": "Alex", "text": "Today we're unpacking how this MERN app keeps users secure."},
            {"speaker": "Sam", "text": "Right, it starts with hashing passwords using bcrypt."},
            {"speaker": "Alex", "text": "Wait, so the server never stores the real password?"},
            {"speaker": "Sam", "text": "Exactly. Then it signs a JSON Web Token that lasts thirty days."},
            {"speaker": "Alex", "text": "And the frontend sends that token with every request."},
        ]}
    if name == "VideoScript":
        return {"title": "How MERN Auth Works", "scenes": [
            {"title": "The big picture", "bullets": ["React frontend", "Express backend"], "narration": "Let's see how the pieces fit together.", "visual": "Two boxes"},
            {"title": "Hashing passwords", "bullets": ["bcrypt", "10 salt rounds"], "narration": "Passwords are hashed before storage. This keeps them safe.", "visual": "A lock"},
            {"title": "Tokens", "bullets": ["JWT", "30 days"], "narration": "A signed token proves who you are on every request.", "visual": "A key"},
        ]}
    raise AssertionError(f"no fake payload for {name}")
