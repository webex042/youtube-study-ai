"""
Data models.

Two families live here:

* **API models** — what the browser may send. Strict lengths and types, so
  oversized or malformed input is rejected before any work happens.
* **LLM output models** — the JSON shapes requested from the model via
  structured output. They are deliberately simple (no recursion, no unions)
  because provider schema support is a subset of JSON Schema. Everything the
  model returns is still treated as untrusted and re-validated / clamped.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ═════════════════════════════════════════════════════════════════
# API request models
# ═════════════════════════════════════════════════════════════════


class NotebookCreate(BaseModel):
    title: str = Field(default="Untitled notebook", max_length=200)


class NotebookUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    emoji: Optional[str] = Field(default=None, max_length=16)
    chat_config: Optional[dict] = None


class AddUrlRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


class AddTextRequest(BaseModel):
    title: str = Field(default="Pasted text", max_length=200)
    text: str = Field(min_length=1, max_length=1_000_000)


class SelectSourcesRequest(BaseModel):
    source_ids: Optional[list[str]] = Field(default=None, max_length=500)
    selected: bool


class SourceUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    selected: Optional[bool] = None


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    mode: str = Field(default="fast", max_length=10)       # fast | deep
    scope: str = Field(default="web", max_length=10)       # web | youtube


class ImportResultsRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    source_ids: Optional[list[str]] = Field(default=None, max_length=500)
    focus: Optional[str] = Field(default=None, max_length=4000)  # selected text to explain


class FeedbackRequest(BaseModel):
    value: int = Field(ge=-1, le=1)


class StudioRequest(BaseModel):
    type: str = Field(max_length=30)
    options: dict = Field(default_factory=dict)
    source_ids: Optional[list[str]] = Field(default=None, max_length=500)


class ArtifactUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    content: Optional[dict] = None


class NoteCreate(BaseModel):
    title: str = Field(default="New Note", max_length=300)
    content: str = Field(default="", max_length=2_000_000)
    kind: str = Field(default="manual", max_length=30)
    citations: Optional[dict] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    content: Optional[str] = Field(default=None, max_length=2_000_000)


class BookmarkCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    origin_type: str = Field(default="", max_length=30)
    origin_id: str = Field(default="", max_length=64)
    origin_title: str = Field(default="", max_length=300)
    comment: str = Field(default="", max_length=2000)


class FlashcardReview(BaseModel):
    status: str = Field(max_length=20)  # understood | review | clear


class QuizAttempt(BaseModel):
    score: int = Field(ge=0, le=1000)
    total: int = Field(ge=1, le=1000)
    answers: list[int] = Field(default_factory=list, max_length=1000)


class SettingsUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=60)
    pro_mode: Optional[bool] = None
    response_language: Optional[str] = Field(default=None, max_length=40)
    explain_level: Optional[str] = Field(default=None, max_length=20)


# ═════════════════════════════════════════════════════════════════
# LLM output models
# ═════════════════════════════════════════════════════════════════

# ── Video understanding ──────────────────────────────────────────


class VideoTopic(BaseModel):
    title: str
    start: str = Field(description="Start time as MM:SS or H:MM:SS")
    end: str = Field(default="", description="End time as MM:SS or H:MM:SS")
    summary: str = Field(description="2-4 sentences: what is taught in this part and how")


class VisualNote(BaseModel):
    time: str = Field(description="When it appears, MM:SS or H:MM:SS")
    kind: str = Field(description="code | slide | diagram | formula | whiteboard | demo | ui | chart | other")
    description: str = Field(description="What is shown and why it matters for understanding")
    content: str = Field(default="", description="Exact text/code/formula visible on screen, if any")


class KeyTerm(BaseModel):
    term: str
    definition: str
    time: str = ""


class TranscriptLine(BaseModel):
    time: str
    text: str


class VideoAnalysis(BaseModel):
    title: str
    language: str = ""
    is_educational: bool = True
    main_topic: str
    summary: str = Field(description="A clear 4-6 sentence overview of what the video teaches")
    topics: list[VideoTopic]
    visual_notes: list[VisualNote]
    key_terms: list[KeyTerm]
    formulas: list[str] = Field(default_factory=list)
    transcript: list[TranscriptLine] = Field(default_factory=list)


# ── Guides ───────────────────────────────────────────────────────


class SourceGuide(BaseModel):
    summary: str
    key_topics: list[str]
    suggested_questions: list[str]


class NotebookGuide(BaseModel):
    title: str = Field(description="Short notebook title, at most 8 words")
    emoji: str = Field(description="One emoji that represents the subject")
    summary: str = Field(description="3-5 sentence Markdown overview; bold 4-8 key terms with **")
    suggested_questions: list[str]


class ChatSourceReply(BaseModel):
    reply: str
    suggested_questions: list[str]


# ── Studio ───────────────────────────────────────────────────────


class Flashcard(BaseModel):
    front: str
    back: str
    difficulty: str = "medium"
    topic: str = ""
    hint: str = ""


class FlashcardSet(BaseModel):
    title: str
    cards: list[Flashcard]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str
    difficulty: str = "medium"
    topic: str = ""
    hint: str = ""


class QuizSet(BaseModel):
    title: str
    questions: list[QuizQuestion]


class MindMapNode(BaseModel):
    id: str
    label: str
    parent_id: str = Field(default="", description="Empty for the single root node")
    note: str = Field(default="", description="One-sentence explanation of this concept")


class MindMapLink(BaseModel):
    from_id: str
    to_id: str
    label: str = ""


class MindMap(BaseModel):
    title: str
    nodes: list[MindMapNode]
    links: list[MindMapLink] = Field(default_factory=list)


class InfographicSection(BaseModel):
    heading: str
    icon: str = Field(description="One of the allowed icon names")
    points: list[str]
    stat_value: str = ""
    stat_label: str = ""


class InfographicStat(BaseModel):
    value: str
    label: str


class Infographic(BaseModel):
    title: str
    subtitle: str
    key_stats: list[InfographicStat]
    sections: list[InfographicSection]
    steps_title: str = ""
    steps: list[str] = Field(default_factory=list)
    takeaway: str


class Slide(BaseModel):
    layout: str = Field(description="title | bullets | two_column | code | quote | summary")
    title: str
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    left_title: str = ""
    left: list[str] = Field(default_factory=list)
    right_title: str = ""
    right: list[str] = Field(default_factory=list)
    code: str = ""
    code_language: str = ""
    quote: str = ""
    notes: str = Field(default="", description="Speaker notes")


class SlideDeck(BaseModel):
    title: str
    subtitle: str = ""
    slides: list[Slide]


class DataTable(BaseModel):
    title: str
    description: str = ""
    columns: list[str]
    rows: list[list[str]]
    notes: str = ""


class AudioTurn(BaseModel):
    speaker: str
    text: str


class AudioScript(BaseModel):
    title: str
    summary: str = ""
    turns: list[AudioTurn]


class VideoScene(BaseModel):
    title: str
    bullets: list[str]
    narration: str
    visual: str = Field(default="", description="One short phrase describing an illustrative visual")


class VideoScript(BaseModel):
    title: str
    scenes: list[VideoScene]
