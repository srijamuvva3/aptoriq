"""The exam runtime: starting, resuming, answering, instrumenting and submitting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock as clock_module
from ..models import (
    Assignment,
    Attempt,
    AttemptStatus,
    Event,
    Paper,
    PaperStatus,
    Passage,
    Question,
    Response,
    Role,
    Section,
    User,
)
from . import attempt_service as service
from .deps import current_user, get_db
from .schemas import (
    AssignmentOut,
    AttemptOut,
    AttemptStateOut,
    EventBatchIn,
    EventBatchOut,
    ResponseIn,
    ResponseOut,
    ResultOut,
    ResultQuestionOut,
)
from .serializers import (
    attempt_mode,
    attempt_status,
    clock_out,
    paper_meta_out,
    passage_out,
    question_out,
    response_out,
    section_out,
)

router = APIRouter(prefix="/api", tags=["attempts"])

MAX_EVENTS_PER_BATCH = 500


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[AssignmentOut]:
    query = select(Assignment).join(Paper).where(Paper.status == PaperStatus.PUBLISHED)

    if user.role is not Role.TEACHER:
        # A student sees papers set for their cohort, plus anything published to everyone.
        query = query.where(
            (Assignment.cohort_id == user.cohort_id) | (Assignment.cohort_id.is_(None))
        )

    out: list[AssignmentOut] = []
    for assignment in db.scalars(query.order_by(Assignment.published_at.desc())):
        paper = assignment.paper
        attempt = db.scalars(
            select(Attempt).where(
                Attempt.assignment_id == assignment.id, Attempt.student_id == user.id
            )
        ).first()
        count = db.scalar(
            select(func.count()).select_from(Question).where(Question.paper_id == paper.id)
        )
        out.append(
            AssignmentOut(
                id=assignment.id,
                paper_id=paper.id,
                title=paper.title,
                profile_id=paper.profile_id,
                mode=assignment.mode.value,
                question_count=count or 0,
                total_duration_min=paper.total_duration_min,
                attempt_id=attempt.id if attempt else None,
                attempt_status=attempt_status(attempt) if attempt else None,
            )
        )
    return out


@router.post("/assignments/{assignment_id}/attempts", response_model=AttemptOut)
def start_or_resume(
    assignment_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptOut:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found.")

    paper = assignment.paper
    if paper.status is not PaperStatus.PUBLISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This paper is not published yet.")

    existing = db.scalars(
        select(Attempt).where(
            Attempt.assignment_id == assignment_id, Attempt.student_id == user.id
        )
    ).first()

    if existing is None:
        sections = list(
            db.scalars(
                select(Section)
                .where(Section.paper_id == paper.id)
                .order_by(Section.order_index)
            )
        )
        existing = Attempt(
            assignment_id=assignment.id,
            student_id=user.id,
            mode=assignment.mode,
            status=AttemptStatus.IN_PROGRESS,
            started_at=clock_module.utcnow(),
            total_duration_ms=paper.total_duration_min * 60_000,
            sectional_lock=paper.sectional_lock,
            section_state=clock_module.build_section_state(sections, paper.sectional_lock),
            last_seen_at=clock_module.utcnow(),
        )
        db.add(existing)
        db.flush()

    return _attempt_payload(db, existing)


@router.get("/attempts/{attempt_id}", response_model=AttemptOut)
def get_attempt(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptOut:
    attempt = service.load_attempt(db, attempt_id, user)
    return _attempt_payload(db, attempt)


@router.get("/attempts/{attempt_id}/state", response_model=AttemptStateOut)
def get_state(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptStateOut:
    attempt = service.load_attempt(db, attempt_id, user)
    state = service.refresh(db, attempt)
    return AttemptStateOut(
        id=attempt.id,
        status=attempt_status(attempt),
        clock=clock_out(state),
        last_event_seq=attempt.last_event_seq,
        disconnect_count=attempt.disconnect_count,
        score=attempt.score,
        max_score=attempt.max_score,
    )


@router.put("/attempts/{attempt_id}/responses/{question_id}", response_model=ResponseOut)
def save_response(
    attempt_id: int,
    question_id: int,
    payload: ResponseIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ResponseOut:
    attempt = service.load_attempt(db, attempt_id, user)
    if attempt.student_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the student can answer.")

    state = service.refresh(db, attempt)
    service.require_in_progress(attempt)

    question = db.get(Question, question_id)
    if question is None or question.paper_id != attempt.assignment.paper_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question is not part of this paper.")

    if not clock_module.can_answer(attempt, question.section_id, state):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This section is closed." if state.sectional_lock else "The exam is not accepting answers.",
        )

    response = service.get_or_create_response(db, attempt, question)
    response.value = [str(item) for item in payload.value]
    response.is_marked = payload.is_marked
    response.state = service.derive_state(response.value, response.is_marked)

    attempt.current_question_id = question.id
    db.flush()
    return response_out(response)


@router.post("/attempts/{attempt_id}/events", response_model=EventBatchOut)
def ingest_events(
    attempt_id: int,
    batch: EventBatchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> EventBatchOut:
    attempt = service.load_attempt(db, attempt_id, user)
    if attempt.student_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the student can log events.")

    if len(batch.events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"At most {MAX_EVENTS_PER_BATCH} events per batch.",
        )

    incoming = {event.seq for event in batch.events}
    already: set[int] = set()
    if incoming:
        already = set(
            db.scalars(
                select(Event.seq).where(
                    Event.attempt_id == attempt.id, Event.seq.in_(incoming)
                )
            )
        )

    accepted = 0
    for event in sorted(batch.events, key=lambda item: item.seq):
        if event.seq in already:
            continue
        db.add(
            Event(
                attempt_id=attempt.id,
                seq=event.seq,
                type=event.type,
                question_id=event.question_id,
                client_ts=event.client_ts,
                payload=event.payload,
            )
        )
        already.add(event.seq)
        accepted += 1
        attempt.last_event_seq = max(attempt.last_event_seq, event.seq)

    # Refreshed after ingestion so a batch arriving from a reconnect is not thrown away
    # by an expiry detected in the same request.
    state = service.refresh(db, attempt)

    return EventBatchOut(
        accepted=accepted,
        duplicates=len(batch.events) - accepted,
        last_event_seq=attempt.last_event_seq,
        clock=clock_out(state),
        status=attempt_status(attempt),
    )


@router.post("/attempts/{attempt_id}/pause", response_model=AttemptStateOut)
def pause(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptStateOut:
    attempt = service.load_attempt(db, attempt_id, user)
    service.refresh(db, attempt)
    service.require_in_progress(attempt)

    if not clock_module.begin_pause(attempt):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Pausing is only available in practice mode."
        )
    db.flush()
    return get_state(attempt_id, user, db)


@router.post("/attempts/{attempt_id}/resume", response_model=AttemptStateOut)
def resume(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttemptStateOut:
    attempt = service.load_attempt(db, attempt_id, user)
    service.require_in_progress(attempt)
    clock_module.end_pause(attempt)
    attempt.last_seen_at = clock_module.utcnow()
    db.flush()
    return get_state(attempt_id, user, db)


@router.post("/attempts/{attempt_id}/submit", response_model=ResultOut)
def submit(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ResultOut:
    attempt = service.load_attempt(db, attempt_id, user)
    if attempt.student_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the student can submit.")

    service.refresh(db, attempt)
    if service.is_in_progress(attempt):
        service.finalize(db, attempt, AttemptStatus.SUBMITTED)

    return _result_payload(db, attempt)


@router.get("/attempts/{attempt_id}/result", response_model=ResultOut)
def result(
    attempt_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ResultOut:
    attempt = service.load_attempt(db, attempt_id, user)
    service.refresh(db, attempt)
    service.require_submitted(attempt)
    return _result_payload(db, attempt)


def _attempt_payload(db: Session, attempt: Attempt) -> AttemptOut:
    state = service.refresh(db, attempt)
    paper = service.paper_for(db, attempt)
    questions = service.paper_questions(db, paper.id)

    passages = list(db.scalars(select(Passage).where(Passage.paper_id == paper.id)))
    passage_keys = {passage.id: passage.key for passage in passages}
    for question in questions:
        setattr(question, "_passage_key", passage_keys.get(question.passage_id))

    sections = list(
        db.scalars(
            select(Section).where(Section.paper_id == paper.id).order_by(Section.order_index)
        )
    )
    responses = list(db.scalars(select(Response).where(Response.attempt_id == attempt.id)))

    return AttemptOut(
        id=attempt.id,
        status=attempt_status(attempt),
        mode=attempt_mode(attempt),
        paper=paper_meta_out(paper),
        sections=[section_out(section, questions) for section in sections],
        questions=[question_out(question) for question in questions],
        passages=[passage_out(passage) for passage in passages],
        responses=[response_out(response) for response in responses],
        clock=clock_out(state),
        current_question_id=attempt.current_question_id,
        last_event_seq=attempt.last_event_seq,
        score=attempt.score,
        max_score=attempt.max_score,
    )


def _result_payload(db: Session, attempt: Attempt) -> ResultOut:
    paper = service.paper_for(db, attempt)
    questions = service.paper_questions(db, paper.id)
    sections = {
        section.id: section.name
        for section in db.scalars(select(Section).where(Section.paper_id == paper.id))
    }
    responses = {
        response.question_id: response
        for response in db.scalars(select(Response).where(Response.attempt_id == attempt.id))
    }

    rows: list[ResultQuestionOut] = []
    correct = incorrect = attempted = 0

    for question in questions:
        response = responses.get(question.id)
        given = [str(item) for item in (response.value if response else [])]
        if given:
            attempted += 1
            if response and response.is_correct:
                correct += 1
            else:
                incorrect += 1

        rows.append(
            ResultQuestionOut(
                question_id=question.id,
                number=question.number,
                section=sections.get(question.section_id),
                type=question.type,
                given=given,
                correct=[str(item) for item in (question.correct_values or [])],
                is_correct=response.is_correct if response else None,
                marks_awarded=response.marks_awarded if response else None,
                marks=question.marks,
                topic=question.topic,
                difficulty=question.difficulty,
            )
        )

    return ResultOut(
        attempt_id=attempt.id,
        status=attempt_status(attempt),
        score=attempt.score,
        max_score=attempt.max_score,
        attempted=attempted,
        correct=correct,
        incorrect=incorrect,
        unattempted=len(questions) - attempted,
        questions=rows,
    )
