# AI Study Lab — 学習研究室

> Turn any YouTube lecture into structured study notes, flashcards, quizzes, and flowcharts — powered by AI.

**AI Study Lab** is a simple, modular Python + FastAPI application that takes a YouTube video URL, extracts the transcript, and generates comprehensive study material using an LLM (Google Gemini or OpenAI). It features a Japanese-inspired editorial UI design.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Summary** | Quick bullet-point + detailed topic-by-topic summary |
| **Smart Notes** | Structured markdown notes with definitions, key concepts, exam points |
| **Topic Timeline** | Clickable timestamps that jump to the relevant section in the YouTube video |
| **Flashcards** | Interactive 3D flip cards with topic filtering and keyboard navigation |
| **Memory Mode** | Quick-fire revision cards categorized by definitions, formulas, facts, confusions |
| **Flowcharts** | Visual process diagrams extracted from lecture content |
| **Quiz** | Auto-generated MCQs with instant scoring and explanations |
| **Search** | Search across all study material (notes, flashcards, memory points, transcript) |

---

## 🏗️ Architecture

```
youtube-study-ai/
├── app/
│   ├── main.py                     # FastAPI entrypoint
│   ├── routes/study.py             # API endpoints
│   ├── services/
│   │   ├── youtube_service.py      # Transcript fetching
│   │   ├── transcript_processor.py # Text cleaning & chunking
│   │   ├── study_generator.py      # LLM integration + fallback
│   │   └── flowchart_generator.py  # Node/edge validation
│   ├── models/schemas.py           # Pydantic data models
│   └── utils/
│       ├── youtube.py              # URL parsing
│       └── timestamps.py           # Time formatting
├── static/
│   ├── css/style.css               # Japanese editorial design system
│   └── js/
│       ├── app.js                  # Dashboard & API client
│       ├── flashcards.js           # Card flip & navigation
│       ├── quiz.js                 # MCQ answer checking
│       └── flowchart.js            # Diagram rendering
├── templates/index.html            # Single-page application
├── tests/                          # Unit tests
├── .env.example                    # Environment variable template
├── requirements.txt                # Python dependencies
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- (Optional) A Google Gemini or OpenAI API key

### Installation

```bash
# 1. Clone or navigate to the project
cd youtube-study-ai

# 2. Create a virtual environment
python -m venv venv

# 3. Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt
```

### Configure LLM (Optional)

Copy the example environment file and add your API key:

```bash
cp .env.example .env
```

Edit `.env` and add **one** of:

```env
# Google Gemini (free tier at aistudio.google.com)
GEMINI_API_KEY=your_key_here

# OR OpenAI
OPENAI_API_KEY=your_key_here
```

> **No API key?** No problem! The app will use a rule-based fallback that generates basic study materials from the transcript without AI.

### Run the Server

```bash
uvicorn app.main:app --reload
```

Then open: **http://127.0.0.1:8000**

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Server health check |
| `POST` | `/api/study` | Analyze a YouTube video |

### POST /api/study

**Request:**
```json
{
    "url": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

**Response:** A `StudyResult` JSON object containing summary, topics, flashcards, memory points, flowchart, quiz, and transcript text.

---

## 🎨 Design

The UI follows a **Japanese-inspired editorial / zine aesthetic**:

- High-contrast monochromatic palette (charcoal + off-white)
- Vermilion red accent for interactive elements
- Photocopy grain texture overlay
- Tape and paper-scrap embellishments
- Asymmetric editorial grid with intentional negative space
- Premium magazine typography (Inter, Noto Serif JP, Space Mono)

---

## 📝 License

MIT
