"""Request and response bodies for the HTTP API.

`QuestionOut` deliberately has no field for the answer key. Serving a paper to a student
and serving the result after submission use different models so a careless change cannot
leak correct answers into a live exam payload.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    cohort: str | None = None


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessageIn] = Field(default_factory=list)


class ChatOut(BaseModel):
    reply: str


class OptionOut(BaseModel):
    label: str
    text: str = ""
    image_path: str | None = None


class QuestionOut(BaseModel):
    id: int
    number: int
    type: str
    section_id: int | None = None
    passage_key: str | None = None
    stem_text: str = ""
    options: list[OptionOut] = Field(default_factory=list)
    render_mode: str = "text"
    image_url: str | None = None
    marks: float
    negative_marks: float


class SectionOut(BaseModel):
    id: int
    name: str
    order_index: int
    duration_min: int | None = None
    question_ids: list[int] = Field(default_factory=list)


class PassageOut(BaseModel):
    key: str
    text: str = ""
    image_url: str | None = None


class SectionClockOut(BaseModel):
    section_id: int | None
    name: str
    remaining_ms: int | None
    consumed_ms: int
    locked: bool
    active: bool


class ClockOut(BaseModel):
    elapsed_ms: int
    remaining_ms: int
    total_ms: int
    expired: bool
    paused: bool
    freeze_remaining_ms: int
    sectional_lock: bool
    sections: list[SectionClockOut] = Field(default_factory=list)


class ResponseOut(BaseModel):
    question_id: int
    value: list[str] = Field(default_factory=list)
    is_marked: bool = False
    state: str


class PaperMetaOut(BaseModel):
    id: int
    title: str
    profile_id: str | None = None
    calculator: bool = False
    sectional_lock: bool = False
    total_duration_min: int


class AttemptOut(BaseModel):
    id: int
    status: str
    mode: str
    paper: PaperMetaOut
    sections: list[SectionOut]
    questions: list[QuestionOut]
    passages: list[PassageOut]
    responses: list[ResponseOut]
    clock: ClockOut
    current_question_id: int | None = None
    last_event_seq: int = 0
    score: float | None = None
    max_score: float | None = None


class AttemptStateOut(BaseModel):
    """Lightweight polling payload; excludes the paper body."""

    id: int
    status: str
    clock: ClockOut
    last_event_seq: int
    disconnect_count: int
    score: float | None = None
    max_score: float | None = None


class ResponseIn(BaseModel):
    value: list[str] = Field(default_factory=list)
    is_marked: bool = False


class EventIn(BaseModel):
    seq: int
    type: str
    client_ts: int
    question_id: int | None = None
    payload: dict = Field(default_factory=dict)


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list)


class EventBatchOut(BaseModel):
    accepted: int
    duplicates: int
    last_event_seq: int
    clock: ClockOut
    status: str


class AssignmentOut(BaseModel):
    id: int
    paper_id: int
    title: str
    profile_id: str | None = None
    mode: str
    question_count: int
    total_duration_min: int
    attempt_id: int | None = None
    attempt_status: str | None = None


class ResultQuestionOut(BaseModel):
    question_id: int
    number: int
    section: str | None = None
    type: str
    given: list[str] = Field(default_factory=list)
    correct: list[str] = Field(default_factory=list)
    is_correct: bool | None = None
    marks_awarded: float | None = None
    marks: float
    topic: str | None = None
    difficulty: str = "unknown"


class ResultOut(BaseModel):
    attempt_id: int
    status: str
    score: float | None = None
    max_score: float | None = None
    attempted: int
    correct: int
    incorrect: int
    unattempted: int
    questions: list[ResultQuestionOut]


class PaperSummaryOut(BaseModel):
    id: int
    title: str
    profile_id: str | None = None
    status: str
    question_count: int
    verified_count: int
    low_confidence_count: int
    missing_key_count: int
    warnings: list[str] = Field(default_factory=list)


class CohortOut(BaseModel):
    id: int
    name: str
    student_count: int


class AssignmentCreateIn(BaseModel):
    paper_id: int
    cohort_id: int | None = None
    mode: str = "test"
