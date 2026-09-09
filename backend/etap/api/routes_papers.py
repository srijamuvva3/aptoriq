"""Teacher-side paper listing, publishing and assignment.

The review editor itself is milestone two; what exists here is the minimum needed to get
a parsed paper in front of students, plus the verification gate that stops an unreviewed
paper being published.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Assignment,
    AttemptMode,
    Cohort,
    Paper,
    PaperStatus,
    Question,
    User,
)
from ..clock import utcnow
from .deps import current_teacher, get_db
from .schemas import AssignmentCreateIn, AssignmentOut, CohortOut, PaperSummaryOut

router = APIRouter(prefix="/api/papers", tags=["papers"])

LOW_CONFIDENCE = 0.7


def _summary(db: Session, paper: Paper) -> PaperSummaryOut:
    total = db.scalar(
        select(func.count()).select_from(Question).where(Question.paper_id == paper.id)
    )
    verified = db.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.paper_id == paper.id, Question.verified.is_(True))
    )
    low = db.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.paper_id == paper.id, Question.confidence < LOW_CONFIDENCE)
    )
    missing = db.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.paper_id == paper.id, Question.correct_values == [])
    )
    return PaperSummaryOut(
        id=paper.id,
        title=paper.title,
        profile_id=paper.profile_id,
        status=paper.status.value,
        question_count=total or 0,
        verified_count=verified or 0,
        low_confidence_count=low or 0,
        missing_key_count=missing or 0,
        warnings=list(paper.parse_warnings or []),
    )


@router.get("", response_model=list[PaperSummaryOut])
def list_papers(
    _teacher: User = Depends(current_teacher), db: Session = Depends(get_db)
) -> list[PaperSummaryOut]:
    papers = db.scalars(select(Paper).order_by(Paper.created_at.desc()))
    return [_summary(db, paper) for paper in papers]


@router.get("/cohorts", response_model=list[CohortOut])
def list_cohorts(
    _teacher: User = Depends(current_teacher), db: Session = Depends(get_db)
) -> list[CohortOut]:
    rows = db.execute(
        select(Cohort.id, Cohort.name, func.count(User.id))
        .outerjoin(User, (User.cohort_id == Cohort.id) & (User.role == Role.STUDENT))
        .group_by(Cohort.id, Cohort.name)
        .order_by(Cohort.name)
    ).all()
    return [
        CohortOut(id=cohort_id, name=name, student_count=count)
        for cohort_id, name, count in rows
    ]


@router.get("/{paper_id}", response_model=PaperSummaryOut)
def get_paper(
    paper_id: int,
    _teacher: User = Depends(current_teacher),
    db: Session = Depends(get_db),
) -> PaperSummaryOut:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found.")
    return _summary(db, paper)


@router.post("/{paper_id}/publish", response_model=PaperSummaryOut)
def publish(
    paper_id: int,
    force: bool = False,
    _teacher: User = Depends(current_teacher),
    db: Session = Depends(get_db),
) -> PaperSummaryOut:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found.")

    unverified = db.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.paper_id == paper.id, Question.verified.is_(False))
    )
    if unverified and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{unverified} question(s) are still unverified. Review them first, or publish with force=true.",
        )

    paper.status = PaperStatus.PUBLISHED
    paper.published_at = utcnow()
    db.flush()
    return _summary(db, paper)


@router.post("/assignments", response_model=AssignmentOut)
def create_assignment(
    payload: AssignmentCreateIn,
    _teacher: User = Depends(current_teacher),
    db: Session = Depends(get_db),
) -> AssignmentOut:
    paper = db.get(Paper, payload.paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found.")

    if payload.cohort_id is not None and db.get(Cohort, payload.cohort_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cohort not found.")

    try:
        mode = AttemptMode(payload.mode)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Mode must be test or practice.")

    assignment = Assignment(
        paper_id=paper.id, cohort_id=payload.cohort_id, mode=mode, published_at=utcnow()
    )
    db.add(assignment)
    db.flush()

    count = db.scalar(
        select(func.count()).select_from(Question).where(Question.paper_id == paper.id)
    )
    return AssignmentOut(
        id=assignment.id,
        paper_id=paper.id,
        title=paper.title,
        profile_id=paper.profile_id,
        mode=mode.value,
        question_count=count or 0,
        total_duration_min=paper.total_duration_min,
    )
