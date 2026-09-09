from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from etap.clock import utcnow
from etap.models import Attempt, Question


def start_attempt(client, headers, assignment_id: int) -> dict:
    response = client.post(f"/api/assignments/{assignment_id}/attempts", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_login_rejects_a_bad_password(client, seeded):
    response = client.post(
        "/api/auth/login", json={"username": "student", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_returns_a_usable_token(client, seeded, auth):
    headers = auth("student")
    me = client.get("/api/auth/me", headers=headers)

    assert me.status_code == 200
    assert me.json()["role"] == "student"
    assert me.json()["cohort"] == "Test Batch"


def test_student_sees_the_published_assignment(client, seeded, auth):
    response = client.get("/api/assignments", headers=auth("student"))

    assert response.status_code == 200
    listing = response.json()
    assert len(listing) == 1
    assert listing[0]["question_count"] == 20
    assert listing[0]["attempt_id"] is None


def test_starting_an_attempt_returns_the_paper_without_the_answer_key(client, seeded, auth):
    payload = start_attempt(client, auth("student"), seeded["assignment"].id)

    assert payload["status"] == "in_progress"
    assert len(payload["questions"]) == 20
    assert payload["clock"]["remaining_ms"] > 0

    serialised = repr(payload["questions"])
    for field in ("correct_values", "correct", "is_correct"):
        assert field not in serialised


def test_starting_twice_resumes_the_same_attempt(client, seeded, auth):
    headers = auth("student")
    first = start_attempt(client, headers, seeded["assignment"].id)
    second = start_attempt(client, headers, seeded["assignment"].id)

    assert first["id"] == second["id"]


def test_saving_an_answer_sets_the_palette_state(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)
    question = attempt["questions"][0]

    response = client.put(
        f"/api/attempts/{attempt['id']}/responses/{question['id']}",
        headers=headers,
        json={"value": ["A"], "is_marked": False},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "answered"


def test_marking_an_answered_question_is_a_distinct_state(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)
    question = attempt["questions"][0]

    marked = client.put(
        f"/api/attempts/{attempt['id']}/responses/{question['id']}",
        headers=headers,
        json={"value": ["A"], "is_marked": True},
    )
    assert marked.json()["state"] == "answered_marked"

    cleared = client.put(
        f"/api/attempts/{attempt['id']}/responses/{question['id']}",
        headers=headers,
        json={"value": [], "is_marked": True},
    )
    assert cleared.json()["state"] == "marked"


def test_another_student_cannot_read_the_attempt(client, seeded, auth):
    attempt = start_attempt(client, auth("student"), seeded["assignment"].id)

    response = client.get(f"/api/attempts/{attempt['id']}", headers=auth("other"))

    assert response.status_code == 403


def test_event_batches_are_idempotent(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)
    question = attempt["questions"][0]

    batch = {
        "events": [
            {"seq": 1, "type": "EXAM_START", "client_ts": 1000, "payload": {}},
            {
                "seq": 2,
                "type": "QUESTION_ENTER",
                "client_ts": 1200,
                "question_id": question["id"],
                "payload": {},
            },
        ]
    }

    first = client.post(f"/api/attempts/{attempt['id']}/events", headers=headers, json=batch)
    assert first.json()["accepted"] == 2
    assert first.json()["duplicates"] == 0

    # A reconnect replays the buffer; the log must not gain duplicates.
    second = client.post(f"/api/attempts/{attempt['id']}/events", headers=headers, json=batch)
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 2
    assert second.json()["last_event_seq"] == 2


def test_results_are_withheld_until_submission(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    response = client.get(f"/api/attempts/{attempt['id']}/result", headers=headers)

    assert response.status_code == 409


def test_submitting_scores_the_attempt(client, seeded, auth, db_session):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    keys = {
        question.id: list(question.correct_values)
        for question in db_session.scalars(
            select(Question).where(Question.paper_id == seeded["paper"].id)
        )
    }

    answered = 0
    for question in attempt["questions"][:5]:
        correct = keys.get(question["id"]) or []
        if not correct:
            continue
        client.put(
            f"/api/attempts/{attempt['id']}/responses/{question['id']}",
            headers=headers,
            json={"value": correct, "is_marked": False},
        )
        answered += 1

    result = client.post(f"/api/attempts/{attempt['id']}/submit", headers=headers)

    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "submitted"
    assert body["attempted"] == answered
    assert body["correct"] == answered
    assert body["score"] == answered * 4.0
    assert body["unattempted"] == 20 - answered


def test_a_wrong_answer_attracts_negative_marking(client, seeded, auth, db_session):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    question = attempt["questions"][0]
    correct = db_session.scalars(
        select(Question).where(Question.id == question["id"])
    ).one().correct_values
    wrong = next(
        option["label"] for option in question["options"] if option["label"] not in correct
    )

    client.put(
        f"/api/attempts/{attempt['id']}/responses/{question['id']}",
        headers=headers,
        json={"value": [wrong], "is_marked": False},
    )
    body = client.post(f"/api/attempts/{attempt['id']}/submit", headers=headers).json()

    assert body["correct"] == 0
    assert body["score"] == -1.0


def test_answering_after_submission_is_refused(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)
    client.post(f"/api/attempts/{attempt['id']}/submit", headers=headers)

    response = client.put(
        f"/api/attempts/{attempt['id']}/responses/{attempt['questions'][0]['id']}",
        headers=headers,
        json={"value": ["A"], "is_marked": False},
    )

    assert response.status_code == 409


def test_an_overdue_attempt_expires_on_next_contact(client, seeded, auth, db_session):
    """No sweeper runs on the LAN box, so expiry is detected when the client reappears."""
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    stored = db_session.get(Attempt, attempt["id"])
    stored.started_at = utcnow() - timedelta(hours=5)
    stored.last_seen_at = utcnow()
    db_session.commit()

    state = client.get(f"/api/attempts/{attempt['id']}/state", headers=headers)

    assert state.status_code == 200
    assert state.json()["status"] == "expired"
    assert state.json()["clock"]["remaining_ms"] == 0


def test_pause_is_rejected_in_test_mode(client, seeded, auth):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    response = client.post(f"/api/attempts/{attempt['id']}/pause", headers=headers)

    assert response.status_code == 409


def test_students_cannot_reach_teacher_routes(client, seeded, auth):
    assert client.get("/api/papers", headers=auth("student")).status_code == 403
    assert client.get("/api/papers", headers=auth("teacher")).status_code == 200


def test_teacher_can_list_cohorts_for_assignment(client, seeded, auth):
    response = client.get("/api/papers/cohorts", headers=auth("teacher"))

    assert response.status_code == 200
    assert response.json() == [
        {"id": seeded["cohort"].id, "name": "Test Batch", "student_count": 2}
    ]


def test_publishing_is_blocked_while_questions_are_unverified(client, seeded, auth, db_session):
    paper = seeded["paper"]
    first = db_session.scalars(
        select(Question).where(Question.paper_id == paper.id).limit(1)
    ).one()
    first.verified = False
    db_session.commit()

    headers = auth("teacher")
    blocked = client.post(f"/api/papers/{paper.id}/publish", headers=headers)
    assert blocked.status_code == 409

    forced = client.post(f"/api/papers/{paper.id}/publish?force=true", headers=headers)
    assert forced.status_code == 200
    assert forced.json()["status"] == "published"


def test_question_image_is_served_for_figure_questions(client, seeded, auth, db_session):
    headers = auth("student")
    attempt = start_attempt(client, headers, seeded["assignment"].id)

    with_image = [question for question in attempt["questions"] if question["image_url"]]
    assert with_image, "The fixture should contain at least one figure question."

    response = client.get(with_image[0]["image_url"], headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
