# AI Study Lab

A NotebookLM-style study workspace that turns lectures into understanding.
Add YouTube lectures, video or audio files, PDFs, slides and notes; Gemini
**watches and reads** them (speech *and* what is shown on screen); then study
with a tutor that answers only from your sources, with clickable citations and
video timestamps, and generate revision material in one click.

**Video → understanding → contextual notes → AI explanation → revision material.**

The interface is a dark three-panel workspace: **Sources** on the left, the
**chat tutor** in the middle and **Studio** on the right (tabs on phones).

---

## Quick start

You need Python 3.11+ (3.13 works) and a Gemini API key.

**Windows (this project's existing `venv`):**

```bat
cd "C:\Users\prans\OneDrive\Desktop\youtube transcript\youtube-study-ai"
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**macOS / Linux:**

```bash
cd youtube-study-ai
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**, create a notebook and add a source.

> Run `pip install -r requirements.txt` again whenever you update the code.
> This version needs two packages the first version didn't: `pypdf` (PDFs)
> and `python-multipart` (file uploads). Without the latter the server won't start.

### Configure the AI

Put your key in `.env` in the project folder (copy `.env.example` if you don't
have one yet), then restart the server:

```env
GEMINI_API_KEY=your_key_here
```

Get a key at <https://aistudio.google.com/apikey>. New keys start with `AQ.`;
older ones start with `AIza`. Both work. Open **Settings (⚙) → Test AI
connection** to check it.

**Without a key** the app still runs. You can create notebooks and add sources:
YouTube captions, PDFs, Word and PowerPoint files, subtitles and text are
extracted and can be read in the source viewer. Anything that needs the model
(chat, Studio, watching videos, scanned PDFs, images) shows a clear "AI is not
configured" message. Nothing is faked.

`OPENAI_API_KEY` works as a text-only alternative. It can't watch videos,
create audio overviews or search the web.

---

## Features

### Sources

| Source | What happens |
|---|---|
| **YouTube link** | The app fetches captions *and* Gemini watches the video itself (frames + audio). Long videos are analysed in 20-minute parts. Code, slides, diagrams, formulas and terminal commands shown on screen become their own timestamped passages. If there are no captions, Gemini writes the transcript. |
| **Video / audio file** (MP4, MOV, WebM, MKV, MP3, WAV, M4A…) | Uploaded to Gemini and analysed the same way. Up to `MAX_UPLOAD_MB` (default 1024 MB). |
| **PDF** | Text with page numbers. Scanned PDFs are transcribed by Gemini. |
| **Word (.docx) / PowerPoint (.pptx)** | Headings, lists, tables, slides and speaker notes. |
| **Images** (PNG, JPG, WebP, HEIC) | Handwritten notes, whiteboards and textbook pages are transcribed, with diagrams described. |
| **Text, Markdown, code, notebooks (.ipynb), SRT/VTT subtitles** | Imported directly. |
| **Websites** | The page's readable text. |
| **Pasted text** | Any notes you paste. |
| **Web search** | "Search the web for new sources" finds pages and imports the ones you pick. Typing *"learn about …"* in an empty notebook does the same from the chat. |

Several sources can be combined in one notebook. Tick or untick them to choose
what the tutor and Studio use. Each source gets a **source guide** (summary,
key topics, key terms and chapters for videos). The viewer plays YouTube videos
and seeks to any timestamp you click.

If a video can't be processed (it's private, has no captions and can't be
watched, the quota ran out and so on), the source shows the real reason and a
**Retry** button. The app never invents content for it. When some parts of a
long video fail, the parts that worked are kept and the gaps are listed as
warnings.

### AI tutor (chat)

- Answers only from the selected sources and says so when they don't cover a question.
- Every claim carries a numbered citation `[1]`. Hover or click it to see the exact passage, and click a video timestamp to jump there.
- Shows the model's thinking as live status lines ("Analyzing authentication…") and suggests follow-up questions.
- **Configure chat (⚙ in the composer):** goal (default, learning guide or custom), level (beginner, intermediate or expert), answer length and answer language.
- Stop a reply mid-way (the partial answer is kept), copy it, rate it or **save it to a note**.
- Paste a YouTube or web link into the chat to add it as a source.
- Select any text to **Explain**, **Bookmark**, **Highlight**, **Quiz me** or **Copy**.
- **PRO** in the header switches to the most capable model.

### Studio: 10 tools

| Tool | What you get |
|---|---|
| **Study Notes** | Contextual learning notes in 6 styles: **Comprehensive, Beginner, Exam, Interview, Quick Revision, Deep-Dive**, plus a practice-question set. Each covers objectives, concepts (what, why, how, example and connections), definitions, formulas (LaTeX), code, comparisons, key takeaways, exam points and a revision summary. |
| **Reports** | Briefing doc, study guide, blog post, FAQ, timeline or a custom prompt. |
| **Flashcards** | Flip, previous and next, *Got it / Needs revision* marking, filters (by difficulty or cards that need revision) and a random (shuffle) mode. Progress is saved. |
| **Quiz** | MCQs with explanations, an easy/medium/hard setting, a score, per-topic performance summary and review. Attempts are saved. |
| **Mind Map** | Interactive, with pan, zoom, collapse and expand. Click a concept to see its note, then have the tutor explain it or quiz you on it. Export as PNG. |
| **Audio Overview** | A two-host spoken discussion with a synced transcript. Formats: deep dive, brief, critique and debate. English or 9 Indian languages. Download as WAV. |
| **Video Overview** | Narrated scenes synced to audio, with a play-through view. |
| **Slide Deck** | Slides with speaker notes, a present mode and PDF export. |
| **Infographic** | A visual one-pager with themes. Export as PNG. |
| **Data Table** | A sortable, filterable, editable table with CSV export. |

Each tool has a **customise** dialog (✎) for focus, difficulty, count and
language. Outputs are saved in the Studio list, where you can rename, delete or retry
them and open them full screen.

### Student tools

- **Notes:** create and edit them (Markdown with live preview), search inside, add a table of contents, highlight, copy, and download as **PDF**, `.md` or `.txt`. Generate practice questions from a note, or turn a note into a source.
- **Bookmarks** and **highlights** from any answer or note.
- **Search** across sources, notes, answers and Studio outputs.
- **Progress:** study streak, 30-day activity, quiz scores and flashcard mastery.
- **Separate notebooks** per subject, and a **Continue studying** card on the home page.
- **Export** a notebook (notes, outputs and optionally the chat) as Markdown.
- Responsive layout with loading skeletons, empty states, error messages and toasts.

---

## Configuration

All settings come from environment variables (`.env`). Everything except the
key is optional. See `.env.example` for the full, commented list.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini key (required for AI features). |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Main model; `GEMINI_FALLBACK_MODELS` are tried if it isn't available. |
| `GEMINI_PRO_MODEL` | `gemini-4-argon` | Model used in PRO mode, with its own fallbacks. |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | Text-to-speech model for audio and video overviews. |
| `VIDEO_ANALYSIS` | `true` | Let Gemini watch videos. Set `false` to rely on captions only (fewer tokens). |
| `VIDEO_SEGMENT_MINUTES` | `20` | Length of each analysed part of a long video. |
| `VIDEO_FPS` | (Gemini default, 1) | Frames per second sampled from videos. |
| `MAX_UPLOAD_MB` | `1024` | Largest file you can upload. |
| `MAX_CONTEXT_CHARS` | `700000` | How much source text goes into one request; larger notebooks are searched for the most relevant passages. |
| `AI_RATE_LIMIT` / `AI_DAILY_LIMIT` | `40` per minute / `1500` per day | Limits on AI requests per client. |
| `DATA_DIR` | `./data` | Where the database, uploads and generated audio are stored. |

---

## Data and privacy

- Everything is stored locally in `data/`: `notebooks.db` (SQLite), `uploads/` and `media/` (generated audio). Delete the folder to start fresh. It is git-ignored.
- Source content is sent to the configured AI provider to process it. Uploaded files go to the Gemini Files API, which deletes them automatically after 48 hours. The extracted text stays in your local database.
- There are no user accounts. It's a single-user app for your own computer, and it binds to `127.0.0.1` by default. Don't expose it to the internet without adding authentication.
- Security measures:
  - a strict Content-Security-Policy (only YouTube embeds are allowed as frames)
  - request-size limits
  - rate limits
  - SSRF protection for web sources (private and internal addresses are blocked, including after redirects)
  - source text treated as data, never as instructions to the model
  - API keys redacted from logs and error messages

---

## Troubleshooting

| Problem | Fix |
|---|---|
| **"AI is not configured"** | Add `GEMINI_API_KEY=…` to `.env` in the `youtube-study-ai` folder, then restart `uvicorn`. |
| **`401 … ACCESS_TOKEN_TYPE_UNSUPPORTED`** or **"API key not valid"** | Copy the whole key again from AI Studio, without quotes or spaces. `AQ.` keys are the current format and are supported. If one still fails, create a new key for the project in AI Studio. |
| **"model was not found"** | The app tries fallback models automatically. To pin one, set `GEMINI_MODEL` to a model your key can use ([model list](https://ai.google.dev/gemini-api/docs/models)). |
| **429 / quota reached** | Free-tier keys have per-minute and per-day limits, and watching a long video uses many tokens. The app waits and retries at lower resolution, and reports any parts it couldn't analyse. Retry later, lower `VIDEO_SEGMENT_MINUTES`, or set `VIDEO_ANALYSIS=false` to use captions only. |
| **YouTube: "captions unavailable" / blocked** | Gemini can still watch public videos. Private or members-only videos can't be watched: download the lecture and upload the file instead. |
| **`Form data requires "python-multipart"`** at startup | Run `pip install -r requirements.txt` inside the venv. |
| **Upload rejected as too large** | Raise `MAX_UPLOAD_MB` in `.env`. |
| **Port 8000 is busy** | `uvicorn app.main:app --reload --port 8001` |

---

## Project structure

```
youtube-study-ai/
├── app/
│   ├── main.py                    # FastAPI app, pages, error handlers
│   ├── config.py                  # every setting, read from the environment
│   ├── db.py                      # SQLite storage (notebooks, sources, passages, chat, outputs, notes…)
│   ├── models/schemas.py          # API request models + structured AI output schemas
│   ├── routes/                    # notebooks & chat, sources, studio, notes/bookmarks/progress/settings
│   ├── services/
│   │   ├── llm.py                 # Gemini provider (streaming, JSON, Files API, TTS, search) + OpenAI text fallback
│   │   ├── video_understanding.py # Gemini watches videos in segments → topics, on-screen notes, terms, transcript
│   │   ├── sources.py             # ingestion for every source type
│   │   ├── documents.py           # PDF / DOCX / PPTX / text extraction
│   │   ├── youtube_service.py     # caption fetching
│   │   ├── retrieval.py           # passages, BM25 ranking, citation mapping
│   │   ├── chat.py                # grounded tutor (SSE streaming)
│   │   ├── studio.py              # the 10 Studio generators
│   │   ├── prompts.py             # all prompts and note styles
│   │   ├── guide.py, jobs.py, web_fetch.py
│   └── security/                  # headers/CSP, rate limits, SSRF guard, log redaction
├── static/
│   ├── css/app.css
│   ├── js/                        # ES modules, no build step (notebook/, viewers/, lib/)
│   ├── img/                       # logo and artwork (original)
│   └── vendor/                    # marked, DOMPurify, KaTeX, highlight.js, fonts (served locally)
├── templates/index.html
├── tests/
├── legacy_v1/                     # the previous version's UI and pipeline, kept as a backup (unused)
├── .env.example
└── requirements.txt
```

---

## Tests

```bash
python -m pytest tests -q
```

The suite covers ingestion, retrieval, citations, every Studio generator, the
API end to end, and the real Gemini SDK against a local mock of the Gemini API
(request shapes, streaming thoughts, model fallback, uploads, TTS and error
mapping). No key or network access is needed.

`python -m tests.run_test_server` starts a **test-only** UI server whose "AI"
returns canned sample data, so the interface can be clicked through without a
key. It is clearly labelled **TEST MODE** in the header and under the chat
box. Never use it for real studying.
