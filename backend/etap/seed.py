"""Create a demo classroom: one teacher, a few students, and the fixture paper published.

Idempotent, so running it twice does not duplicate the roster.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .clock import utcnow
from .db import create_all, get_session_factory
from .importer import import_parsed_file
from .models import (
    Assignment,
    AttemptMode,
    Cohort,
    Paper,
    PaperStatus,
    Question,
    Role,
    User,
)
from .security import hash_password

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PARSED = REPO_ROOT / "data" / "output" / "sample_paper_digital" / "paper.json"

DEMO_PASSWORD = "exam1234"
STUDENTS = [
    ("asha", "Asha Rao"),
    ("vikram", "Vikram Nair"),
    ("meera", "Meera Iyer"),
]


def _get_or_create_cohort(db: Session, name: str) -> Cohort:
    cohort = db.scalars(select(Cohort).where(Cohort.name == name)).first()
    if cohort is None:
        cohort = Cohort(name=name)
        db.add(cohort)
        db.flush()
    return cohort


def _get_or_create_user(
    db: Session, username: str, display_name: str, role: Role, cohort_id: int | None
) -> User:
    user = db.scalars(select(User).where(User.username == username)).first()
    if user is None:
        user = User(
            username=username,
            display_name=display_name,
            role=role,
            cohort_id=cohort_id,
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        db.flush()
    return user


def seed(parsed_path: Path | None = None, profile_id: str = "jee_main") -> dict:
    create_all()
    source = parsed_path or FIXTURE_PARSED
    session = get_session_factory()()

    try:
        cohort = _get_or_create_cohort(session, "Batch 2026")
        teacher = _get_or_create_user(
            session, "teacher", "Head of Physics", Role.TEACHER, None
        )
        for username, name in STUDENTS:
            _get_or_create_user(session, username, name, Role.STUDENT, cohort.id)

        paper = session.scalars(
            select(Paper).where(Paper.source_pdf.like("%sample_paper%"))
        ).first()

        created = False
        if paper is None:
            if not source.exists():
                raise FileNotFoundError(
                    f"No parsed paper at {source}. Run the ingest CLI on a PDF first."
                )
            paper = import_parsed_file(
                session, source, profile_id=profile_id, created_by_id=teacher.id
            )
            created = True

        # The fixture is machine-generated, so marking it verified is honest here; a real
        # paper must go through the review editor instead.
        if created:
            for question in session.scalars(
                select(Question).where(Question.paper_id == paper.id)
            ):
                question.verified = True

        paper.status = PaperStatus.PUBLISHED
        paper.published_at = paper.published_at or utcnow()

        assignment = session.scalars(
            select(Assignment).where(Assignment.paper_id == paper.id)
        ).first()
        if assignment is None:
            assignment = Assignment(
                paper_id=paper.id, cohort_id=cohort.id, mode=AttemptMode.TEST
            )
            session.add(assignment)
            session.flush()

        question_count = session.scalar(
            select(func.count()).select_from(Question).where(Question.paper_id == paper.id)
        )
        session.commit()

        return {
            "paper_id": paper.id,
            "paper_title": paper.title,
            "assignment_id": assignment.id,
            "questions": question_count or 0,
            "teacher": teacher.username,
            "students": [username for username, _ in STUDENTS],
            "password": DEMO_PASSWORD,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
