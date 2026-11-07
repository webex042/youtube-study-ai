"""
Pydantic Schemas

Two jobs here, both security-relevant:

1. Validate untrusted *inbound* request data before it reaches any logic.
2. Validate untrusted *LLM output* before it reaches the browser. Model
   output is treated exactly like user input — it is never trusted just
   because it came from our own API call.

Every string field has an explicit maximum length and every list an
explicit maximum size, so a hostile or malfunctioning model cannot push a
multi-megabyte payload through to the client.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.utils.youtube import VIDEO_ID_REGEX

# ── Reusable constrained types ───────────────────────────────────

ShortText = Annotated[str, StringConstraints(max_length=300, strip_whitespace=True)]
MediumText = Annotated[str, StringConstraints(max_length=2_000, strip_whitespace=True)]
LongText = Annotated[str, StringConstraints(max_length=20_000)]
NotesText = Annotated[str, StringConstraints(max_length=120_000)]

Importance = Literal["low", "medium", "high"]
Difficulty = Literal["easy", "medium", "hard"]
MemoryCategory = Literal[
    "definition", "formula", "fact", "confusion", "exam_point", "general"
]

# Control characters (other than tab/newline/carriage-return) have no place
# in text destined for the DOM; they are stripped rather than rejected so a
# single stray byte doesn't discard an otherwise-good response.
_ALLOWED_CONTROL = {"\t", "\n", "\r"}


def _strip_control_chars(value: str) -> str:
    return "".join(
        ch for ch in value
        if ch in _ALLOWED_CONTROL or ord(ch) >= 0x20 and ord(ch) != 0x7F
    )


class SafeModel(BaseModel):
    """
    Base model that rejects unknown fields and scrubs control characters.

    `extra="forbid"` matters for inbound requests: a client cannot smuggle
    additional attributes into a model that later gets passed around.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("*", mode="after")
    @classmethod
    def _clean_strings(cls, value):
        if isinstance(value, str):
            return _strip_control_chars(value)
        return value


# ═══════════════════════════════════════════════════════════════════
# INBOUND REQUEST
# ═══════════════════════════════════════════════════════════════════

class StudyRequest(SafeModel):
    """
    Incoming request from the frontend.

    Only a URL is accepted. The length cap is enforced here so an oversized
    string is rejected by validation before any parsing work happens.
    """

    url: Annotated[str, StringConstraints(min_length=1, max_length=2048, strip_whitespace=True)]

    @field_validator("url")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("Invalid URL.")
        return value


# ═══════════════════════════════════════════════════════════════════
# STUDY MATERIAL (also used to validate LLM output)
# ═══════════════════════════════════════════════════════════════════

class TranscriptSnippet(SafeModel):
    text: MediumText = ""
    start: float = Field(default=0.0, ge=0, le=200_000)
    duration: float = Field(default=0.0, ge=0, le=200_000)


class TopicSegment(SafeModel):
    title: ShortText = "Untitled"
    start_time: float = Field(default=0.0, ge=0, le=200_000)
    end_time: float = Field(default=0.0, ge=0, le=200_000)
    text: MediumText = ""
    timestamp: Annotated[str, StringConstraints(max_length=16)] = ""

    @model_validator(mode="after")
    def _ordered_times(self) -> "TopicSegment":
        # A reversed range would render a nonsensical timeline; clamp instead
        # of failing so one bad segment doesn't discard the whole response.
        if self.end_time < self.start_time:
            object.__setattr__(self, "end_time", self.start_time)
        return self


class Flashcard(SafeModel):
    question: MediumText
    answer: MediumText
    topic: ShortText = ""
    difficulty: Difficulty = "medium"

    @field_validator("question", "answer")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Flashcard text cannot be empty.")
        return value


class MemoryPoint(SafeModel):
    concept: ShortText
    memory_hook: MediumText
    importance: Importance = "medium"
    category: MemoryCategory = "general"

    @field_validator("concept", "memory_hook")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Memory point text cannot be empty.")
        return value


class QuizQuestion(SafeModel):
    question: MediumText
    options: list[ShortText] = Field(min_length=2, max_length=6)
    correct_answer: int = Field(ge=0, le=5)
    explanation: MediumText = ""

    @model_validator(mode="after")
    def _answer_in_range(self) -> "QuizQuestion":
        # An out-of-range index would mean the quiz UI marks nothing correct,
        # so reject the question rather than shipping a broken one.
        if self.correct_answer >= len(self.options):
            raise ValueError("correct_answer must index one of the options.")
        if not self.question.strip():
            raise ValueError("Quiz question cannot be empty.")
        return self


class FlowchartNode(SafeModel):
    id: Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]
    label: ShortText

    @field_validator("label")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Node label cannot be empty.")
        return value


class FlowchartEdge(SafeModel):
    source: Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]
    target: Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]


class Flowchart(SafeModel):
    title: ShortText = ""
    nodes: list[FlowchartNode] = Field(default_factory=list, max_length=40)
    edges: list[FlowchartEdge] = Field(default_factory=list, max_length=80)


class StudyResult(SafeModel):
    """Complete study material returned to the frontend."""

    video_id: Annotated[str, StringConstraints(max_length=16)]
    language: ShortText = "en"
    language_code: Annotated[str, StringConstraints(max_length=16)] = "en"
    is_generated: bool = False

    # Summary
    overview: MediumText = ""
    quick_summary: list[MediumText] = Field(default_factory=list, max_length=12)
    detailed_summary: LongText = ""

    # Topics & notes (markdown, rendered safely on the client)
    topics: list[TopicSegment] = Field(default_factory=list, max_length=30)
    notes: NotesText = ""

    # Study aids
    flashcards: list[Flashcard] = Field(default_factory=list, max_length=60)
    memory_points: list[MemoryPoint] = Field(default_factory=list, max_length=40)
    flowchart: Flowchart = Field(default_factory=Flowchart)
    quiz: list[QuizQuestion] = Field(default_factory=list, max_length=30)

    # Raw transcript, for in-page search only. Truncated before sending.
    transcript_text: Annotated[str, StringConstraints(max_length=2_000_000)] = ""

    @field_validator("video_id")
    @classmethod
    def _valid_video_id(cls, value: str) -> str:
        if value and not VIDEO_ID_REGEX.match(value):
            raise ValueError("Invalid video ID.")
        return value


# ═══════════════════════════════════════════════════════════════════
# LLM OUTPUT ENVELOPE
# ═══════════════════════════════════════════════════════════════════

class LLMStudyPayload(BaseModel):
    """
    Schema for raw LLM JSON output.

    Kept separate from StudyResult and deliberately permissive about extra
    keys — models add stray fields, and dropping them is friendlier than
    failing the whole generation. The strict validation still happens: each
    item is re-validated into its StudyResult counterpart afterwards, so
    nothing reaches the client unchecked.
    """

    model_config = ConfigDict(extra="ignore")

    overview: str = ""
    quick_summary: list[str] = Field(default_factory=list)
    detailed_summary: str = ""
    topics: list[dict] = Field(default_factory=list)
    notes: str = ""
    flashcards: list[dict] = Field(default_factory=list)
    memory_points: list[dict] = Field(default_factory=list)
    flowchart: dict = Field(default_factory=dict)
    quiz: list[dict] = Field(default_factory=list)


class LLMChunkSummary(BaseModel):
    """Schema for a single chunk summary during map-reduce."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    key_concepts: list[str] = Field(default_factory=list)
    topic_title: str = ""


class ErrorResponse(BaseModel):
    """Uniform, information-free error body."""

    error: str
    request_id: str | None = None
