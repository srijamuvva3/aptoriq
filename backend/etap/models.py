"""ORM models.

Two constraints here are load-bearing rather than cosmetic:

* `Event` is unique on (attempt_id, seq). The client buffers events while offline and
  replays batches, so retries must be idempotent or the behavioural log gets duplicates
  and every derived metric is wrong.
* `Attempt` snapshots its duration and marking at start time. A teacher editing a paper
  mid-session must not change the clock of an attempt already under way.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_column(enum_cls: type[enum.Enum]) -> Enum:
    """Store the enum's value, and load it back as an enum member.

    Without `values_callable` SQLAlchemy persists member *names*, and without a real
    Enum type SQLite returns bare strings, which makes `status is Status.X` quietly
    false everywhere. Both mistakes are easy to make and hard to spot, so the mapping
    lives in one place.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Role(str, enum.Enum):
    TEACHER = "teacher"
    STUDENT = "student"


class PaperStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class AttemptMode(str, enum.Enum):
    TEST = "test"
    PRACTICE = "practice"


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class ResponseState(str, enum.Enum):
    NOT_VISITED = "not_visited"
    VISITED = "visited"
    ANSWERED = "answered"
    MARKED = "marked"
    ANSWERED_MARKED = "answered_marked"


class Cohort(Base):
    __tablename__ = "cohorts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[User]] = relationship(back_populates="cohort")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[Role] = mapped_column(enum_column(Role))
    cohort_id: Mapped[int | None] = mapped_column(ForeignKey("cohorts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cohort: Mapped[Cohort | None] = relationship(back_populates="members")


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[PaperStatus] = mapped_column(enum_column(PaperStatus), default=PaperStatus.DRAFT)

    total_duration_min: Mapped[int] = mapped_column(Integer, default=180)
    sectional_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    calculator: Mapped[bool] = mapped_column(Boolean, default=False)

    source_pdf: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_warnings: Mapped[list] = mapped_column(JSON, default=list)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sections: Mapped[list[Section]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="Section.order_index"
    )
    questions: Mapped[list[Question]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="Question.number"
    )
    passages: Mapped[list[Passage]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )

    @property
    def all_verified(self) -> bool:
        return bool(self.questions) and all(q.verified for q in self.questions)


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="sections")
    questions: Mapped[list[Question]] = relationship(
        back_populates="section", order_by="Question.number"
    )


class Passage(Base):
    __tablename__ = "passages"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    text: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="passages")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("paper_id", "number", name="uq_question_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), nullable=True)
    passage_id: Mapped[int | None] = mapped_column(ForeignKey("passages.id"), nullable=True)

    number: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    stem_text: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)

    render_mode: Mapped[str] = mapped_column(String(10), default="text")
    stem_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    marks: Mapped[float] = mapped_column(Float, default=4.0)
    negative_marks: Mapped[float] = mapped_column(Float, default=1.0)
    partial_credit: Mapped[bool] = mapped_column(Boolean, default=False)

    correct_values: Mapped[list] = mapped_column(JSON, default=list)
    numeric_tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    solution_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    topic: Mapped[str | None] = mapped_column(String(160), nullable=True)
    chapter: Mapped[str | None] = mapped_column(String(160), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="unknown")

    has_figure: Mapped[bool] = mapped_column(Boolean, default=False)
    source_page: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    paper: Mapped[Paper] = relationship(back_populates="questions")
    section: Mapped[Section | None] = relationship(back_populates="questions")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    cohort_id: Mapped[int | None] = mapped_column(ForeignKey("cohorts.id"), nullable=True)
    mode: Mapped[AttemptMode] = mapped_column(enum_column(AttemptMode), default=AttemptMode.TEST)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[Paper] = relationship()
    cohort: Mapped[Cohort | None] = relationship()


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_attempt_per_student"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    mode: Mapped[AttemptMode] = mapped_column(enum_column(AttemptMode), default=AttemptMode.TEST)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_column(AttemptStatus), default=AttemptStatus.IN_PROGRESS
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    """Snapshot taken at start so later edits to the paper cannot alter a live clock."""
    sectional_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    section_state: Mapped[dict] = mapped_column(JSON, default=dict)

    paused_ms_total: Mapped[int] = mapped_column(Integer, default=0)
    pause_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_ms_total: Mapped[int] = mapped_column(Integer, default=0)
    """Raw disconnected time. The clock only honours it up to a cap."""
    disconnect_count: Mapped[int] = mapped_column(Integer, default=0)

    current_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id"), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    assignment: Mapped[Assignment] = relationship()
    student: Mapped[User] = relationship()
    responses: Mapped[list[Response]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_response"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)

    value: Mapped[list] = mapped_column(JSON, default=list)
    is_marked: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[ResponseState] = mapped_column(
        enum_column(ResponseState), default=ResponseState.VISITED
    )

    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    marks_awarded: Mapped[float | None] = mapped_column(Float, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    attempt: Mapped[Attempt] = relationship(back_populates="responses")
    question: Mapped[Question] = relationship()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("attempt_id", "seq", name="uq_event_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32), index=True)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)

    client_ts: Mapped[int] = mapped_column(Integer)
    """Epoch milliseconds from the browser. Authoritative for durations."""
    server_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
