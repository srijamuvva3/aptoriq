"""Model to DTO conversion."""

from __future__ import annotations

from ..clock import ClockState
from ..models import Attempt, Paper, Passage, Question, Response, Section, User
from .schemas import (
    ClockOut,
    OptionOut,
    PaperMetaOut,
    PassageOut,
    QuestionOut,
    ResponseOut,
    SectionClockOut,
    SectionOut,
    UserOut,
)


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role.value,
        cohort=user.cohort.name if user.cohort else None,
    )


def question_out(question: Question) -> QuestionOut:
    image_url = None
    if question.render_mode == "image" and (question.stem_image_path or question.crop_path):
        image_url = f"/api/questions/{question.id}/image"

    return QuestionOut(
        id=question.id,
        number=question.number,
        type=question.type,
        section_id=question.section_id,
        passage_key=question.passage_id and _passage_key(question),
        stem_text=question.stem_text,
        options=[
            OptionOut(
                label=option.get("label", ""),
                text=option.get("text", ""),
                image_path=option.get("image_path"),
            )
            for option in (question.options or [])
        ],
        render_mode=question.render_mode,
        image_url=image_url,
        marks=question.marks,
        negative_marks=question.negative_marks,
    )


def _passage_key(question: Question) -> str | None:
    # Set by the caller when passages are loaded; falls back to None rather than
    # triggering a lazy load per question.
    return getattr(question, "_passage_key", None)


def section_out(section: Section, questions: list[Question]) -> SectionOut:
    return SectionOut(
        id=section.id,
        name=section.name,
        order_index=section.order_index,
        duration_min=section.duration_min,
        question_ids=[q.id for q in questions if q.section_id == section.id],
    )


def passage_out(passage: Passage) -> PassageOut:
    return PassageOut(
        key=passage.key,
        text=passage.text,
        image_url=f"/api/passages/{passage.id}/image" if passage.image_path else None,
    )


def response_out(response: Response) -> ResponseOut:
    return ResponseOut(
        question_id=response.question_id,
        value=[str(item) for item in (response.value or [])],
        is_marked=response.is_marked,
        state=response.state.value,
    )


def clock_out(state: ClockState) -> ClockOut:
    return ClockOut(
        elapsed_ms=state.elapsed_ms,
        remaining_ms=state.remaining_ms,
        total_ms=state.total_ms,
        expired=state.expired,
        paused=state.paused,
        freeze_remaining_ms=state.freeze_remaining_ms,
        sectional_lock=state.sectional_lock,
        sections=[
            SectionClockOut(
                section_id=section.section_id,
                name=section.name,
                remaining_ms=section.remaining_ms,
                consumed_ms=section.consumed_ms,
                locked=section.locked,
                active=section.active,
            )
            for section in state.sections
        ],
    )


def paper_meta_out(paper: Paper) -> PaperMetaOut:
    return PaperMetaOut(
        id=paper.id,
        title=paper.title,
        profile_id=paper.profile_id,
        calculator=paper.calculator,
        sectional_lock=paper.sectional_lock,
        total_duration_min=paper.total_duration_min,
    )


def attempt_status(attempt: Attempt) -> str:
    return attempt.status.value


def attempt_mode(attempt: Attempt) -> str:
    return attempt.mode.value
