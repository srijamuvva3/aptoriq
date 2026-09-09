"""Contract between the ingestion pipeline and everything downstream.

The review editor, the exam runtime and the metric engine all read `ParsedPaper`.
Changing a field here is a breaking change for all three.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    MCQ_SINGLE = "mcq_single"
    MCQ_MULTI = "mcq_multi"
    NUMERICAL = "numerical"
    UNKNOWN = "unknown"


class RenderMode(str, Enum):
    """How the exam UI should present the question."""

    TEXT = "text"
    IMAGE = "image"


class ExtractionRoute(str, Enum):
    TEXT_LAYER = "text_layer"
    VISION = "vision"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    UNKNOWN = "unknown"


class BBox(BaseModel):
    """Rectangle in PDF points, origin top-left, as PyMuPDF reports it."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def union(self, other: BBox) -> BBox:
        return BBox(
            page=self.page,
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def padded(self, pad: float, max_x: float, max_y: float) -> BBox:
        return BBox(
            page=self.page,
            x0=max(0.0, self.x0 - pad),
            y0=max(0.0, self.y0 - pad),
            x1=min(max_x, self.x1 + pad),
            y1=min(max_y, self.y1 + pad),
        )


class Option(BaseModel):
    label: str
    text: str = ""
    image_path: str | None = None


class Passage(BaseModel):
    """Shared stimulus for a cluster of questions."""

    id: str
    text: str = ""
    image_path: str | None = None
    page: int
    question_numbers: list[int] = Field(default_factory=list)


class Question(BaseModel):
    number: int
    type: QuestionType = QuestionType.UNKNOWN
    section: str | None = None
    passage_id: str | None = None

    stem_text: str = ""
    options: list[Option] = Field(default_factory=list)

    render_mode: RenderMode = RenderMode.TEXT
    stem_image_path: str | None = None
    crop_path: str | None = None
    """Always populated. The review editor shows this beside the parsed text."""

    marks: float | None = None
    negative_marks: float | None = None

    topic: str | None = None
    chapter: str | None = None
    difficulty: Difficulty = Difficulty.UNKNOWN

    correct_values: list[str] = Field(default_factory=list)
    numeric_tolerance: float | None = None
    solution_text: str | None = None

    source_page: int
    bbox: BBox | None = None
    route: ExtractionRoute = ExtractionRoute.TEXT_LAYER
    has_figure: bool = False

    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)
    verified: bool = False

    def flag(self, note: str, penalty: float = 0.0) -> None:
        if note not in self.notes:
            self.notes.append(note)
        if penalty:
            self.confidence = max(0.0, self.confidence - penalty)


class PageInfo(BaseModel):
    index: int
    width: float
    height: float
    char_count: int
    has_text_layer: bool
    image_area_ratio: float
    column_count: int = 1
    blank: bool = False
    """Cover pages and spacers. Skipped rather than sent to a vision model."""
    route: ExtractionRoute
    render_path: str | None = None


class PaperStats(BaseModel):
    page_count: int = 0
    question_count: int = 0
    questions_with_figures: int = 0
    questions_from_vision: int = 0
    questions_missing_key: int = 0
    low_confidence_count: int = 0
    mean_confidence: float = 0.0


class ParsedPaper(BaseModel):
    title: str
    profile: str | None = None
    source_pdf: str
    key_source: str | None = None

    pages: list[PageInfo] = Field(default_factory=list)
    passages: list[Passage] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    stats: PaperStats = Field(default_factory=PaperStats)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def recompute_stats(self, low_confidence_threshold: float = 0.7) -> None:
        questions = self.questions
        self.stats = PaperStats(
            page_count=len(self.pages),
            question_count=len(questions),
            questions_with_figures=sum(1 for q in questions if q.has_figure),
            questions_from_vision=sum(
                1 for q in questions if q.route is ExtractionRoute.VISION
            ),
            questions_missing_key=sum(1 for q in questions if not q.correct_values),
            low_confidence_count=sum(
                1 for q in questions if q.confidence < low_confidence_threshold
            ),
            mean_confidence=(
                round(sum(q.confidence for q in questions) / len(questions), 3)
                if questions
                else 0.0
            ),
        )
