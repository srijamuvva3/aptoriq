"""Attempt lifecycle logic shared by the routes.

Every request touching an attempt goes through `refresh`, which credits any heartbeat
gap, recomputes the clock, and finalises the attempt if time has run out. Expiry is
therefore detected on the next contact rather than requiring a background sweeper, and a
student who closes the laptop and returns tomorrow gets a correctly expired attempt.
"""

from __future__ import annotations

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock as clock_module
from ..models import (
    Attempt,
    AttemptStatus,
    Event,
    Paper,
    Question,
    Response,
    ResponseState,
    Role,
    User,
)
from ..scoring import score_attempt


def load_attempt(db: Session, attempt_id: int, user: User) -> Attempt:
    attempt = db.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Attempt not found.")

    if attempt.student_id != user.id and user.role is not Role.TEACHER:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Not your attempt.")
    return attempt


def paper_questions(db: Session, paper_id: int) -> list[Question]:
    return list(
        db.scalars(
            select(Question).where(Question.paper_id == paper_id).order_by(Question.number)
        )
    )


def refresh(db: Session, attempt: Attempt, *, touch: bool = True) -> clock_module.ClockState:
    """Recompute the clock and finalise the attempt if it is over."""
    if is_in_progress(attempt) and touch:
        gap = clock_module.absorb_gap(attempt)
        if gap:
            _record_gap_event(db, attempt, gap)

    state = clock_module.evaluate(attempt)

    if is_in_progress(attempt) and _is_over(state):
        finalize(db, attempt, AttemptStatus.EXPIRED)
        state = clock_module.evaluate(attempt)

    db.flush()
    return state


def _is_over(state: clock_module.ClockState) -> bool:
    if state.expired:
        return True
    # With sectional locking the paper ends when the last section locks, which can
    # happen before the notional global duration is spent.
    if state.sectional_lock and state.sections:
        return all(section.locked for section in state.sections)
    return False


def _record_gap_event(db: Session, attempt: Attempt, gap_ms: int) -> None:
    """Log an inferred disconnect so the teacher sees it and metrics can exclude it."""
    attempt.last_event_seq += 1
    db.add(
        Event(
            attempt_id=attempt.id,
            seq=attempt.last_event_seq,
            type="DISCONNECT_INFERRED",
            client_ts=0,
            payload={"gap_ms": gap_ms, "source": "server"},
        )
    )


def finalize(db: Session, attempt: Attempt, final_status: AttemptStatus) -> None:
    if attempt.pause_started_at is not None:
        clock_module.end_pause(attempt)

    questions = paper_questions(db, _paper_id_for(db, attempt))
    score_attempt(attempt, questions)

    attempt.status = final_status
    attempt.submitted_at = clock_module.utcnow()
    db.flush()


def _paper_id_for(db: Session, attempt: Attempt) -> int:
    assignment = attempt.assignment
    if assignment is not None:
        return assignment.paper_id
    raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "Attempt has no paper.")


def paper_for(db: Session, attempt: Attempt) -> Paper:
    paper = db.get(Paper, _paper_id_for(db, attempt))
    if paper is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Paper not found.")
    return paper


def get_or_create_response(db: Session, attempt: Attempt, question: Question) -> Response:
    existing = db.scalars(
        select(Response).where(
            Response.attempt_id == attempt.id, Response.question_id == question.id
        )
    ).first()
    if existing is not None:
        return existing

    created = Response(
        attempt_id=attempt.id,
        question_id=question.id,
        value=[],
        is_marked=False,
        state=ResponseState.VISITED,
    )
    db.add(created)
    db.flush()
    return created


def derive_state(value: list, is_marked: bool) -> ResponseState:
    answered = bool([item for item in (value or []) if str(item).strip()])
    if answered and is_marked:
        return ResponseState.ANSWERED_MARKED
    if answered:
        return ResponseState.ANSWERED
    if is_marked:
        return ResponseState.MARKED
    return ResponseState.VISITED


def is_in_progress(attempt: Attempt) -> bool:
    return attempt.status is AttemptStatus.IN_PROGRESS


def require_in_progress(attempt: Attempt) -> None:
    if not is_in_progress(attempt):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "This attempt has already been submitted."
        )


def require_submitted(attempt: Attempt) -> None:
    if is_in_progress(attempt):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            "Results are available only after the attempt is submitted.",
        )
