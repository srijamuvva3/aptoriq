from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the synthetic paper once per test session."""
    from make_fixture import write_outputs

    directory = tmp_path_factory.mktemp("samples")
    write_outputs(directory, seed=7)
    return directory


@pytest.fixture(scope="session")
def parsed_digital(sample_dir: Path, tmp_path_factory: pytest.TempPathFactory):
    from etap.ingest import IngestOptions, parse_paper

    output = tmp_path_factory.mktemp("output")
    # Crops live under the pytest temp directory, which the media route would otherwise
    # refuse to serve.
    os.environ["ETAP_MEDIA_ROOTS"] = str(output)
    return parse_paper(
        sample_dir / "sample_paper_digital.pdf",
        output / "digital",
        key_path=sample_dir / "sample_paper_key.csv",
        options=IngestOptions(profile_id="jee_main", use_vision=False, tag=False),
    )


@pytest.fixture
def db_session(tmp_path: Path):
    """A fresh database per test, wired into the module-level session factory."""
    from etap import db as db_module

    engine = db_module.build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db_module.configure(engine)
    db_module.create_all()

    session = db_module.get_session_factory()()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def seeded(db_session, parsed_digital):
    """Teacher, students, an imported and published paper, and an assignment."""
    from etap.importer import import_parsed_paper
    from etap.models import Assignment, AttemptMode, Cohort, PaperStatus, Role, User
    from etap.security import hash_password

    cohort = Cohort(name="Test Batch")
    db_session.add(cohort)
    db_session.flush()

    teacher = User(
        username="teacher",
        display_name="Teacher",
        role=Role.TEACHER,
        password_hash=hash_password("secret123"),
    )
    student = User(
        username="student",
        display_name="Student One",
        role=Role.STUDENT,
        cohort_id=cohort.id,
        password_hash=hash_password("secret123"),
    )
    other = User(
        username="other",
        display_name="Student Two",
        role=Role.STUDENT,
        cohort_id=cohort.id,
        password_hash=hash_password("secret123"),
    )
    db_session.add_all([teacher, student, other])
    db_session.flush()

    paper = import_parsed_paper(
        db_session, parsed_digital, profile_id="jee_main", created_by_id=teacher.id
    )
    for question in paper.questions:
        question.verified = True
    paper.status = PaperStatus.PUBLISHED

    assignment = Assignment(paper_id=paper.id, cohort_id=cohort.id, mode=AttemptMode.TEST)
    db_session.add(assignment)
    db_session.commit()

    return {
        "cohort": cohort,
        "teacher": teacher,
        "student": student,
        "other": other,
        "paper": paper,
        "assignment": assignment,
    }


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from etap.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def auth(client):
    def _login(username: str, password: str = "secret123") -> dict[str, str]:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return _login
