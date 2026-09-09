from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from etap import clock as clock_module
from etap.clock import FREEZE_CAP_MS, build_section_state, evaluate, utcnow
from etap.models import Attempt, AttemptMode, AttemptStatus


def make_attempt(**overrides) -> Attempt:
    defaults = dict(
        assignment_id=1,
        student_id=1,
        mode=AttemptMode.TEST,
        status=AttemptStatus.IN_PROGRESS,
        started_at=utcnow(),
        total_duration_ms=60 * 60_000,
        sectional_lock=False,
        section_state={},
        paused_ms_total=0,
        frozen_ms_total=0,
        disconnect_count=0,
        last_event_seq=0,
    )
    defaults.update(overrides)
    return Attempt(**defaults)


def fake_sections(*specs: tuple[int, str, int | None]):
    return [
        SimpleNamespace(id=identifier, name=name, order_index=index, duration_min=duration)
        for index, (identifier, name, duration) in enumerate(specs)
    ]


def test_elapsed_and_remaining_track_wall_time():
    started = utcnow() - timedelta(minutes=10)
    attempt = make_attempt(started_at=started)

    state = evaluate(attempt)

    assert 9 * 60_000 < state.elapsed_ms < 11 * 60_000
    assert state.remaining_ms == state.total_ms - state.elapsed_ms
    assert not state.expired


def test_expiry_detected_once_duration_is_spent():
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=61), total_duration_ms=60 * 60_000
    )

    state = evaluate(attempt)

    assert state.expired
    assert state.remaining_ms == 0


def test_pause_is_excluded_from_elapsed_time():
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=30), paused_ms_total=10 * 60_000
    )

    state = evaluate(attempt)

    assert 19 * 60_000 < state.elapsed_ms < 21 * 60_000


def test_open_pause_keeps_the_clock_still():
    now = utcnow()
    attempt = make_attempt(
        started_at=now - timedelta(minutes=20), pause_started_at=now - timedelta(minutes=5)
    )

    state = evaluate(attempt, now)

    assert state.paused
    assert 14 * 60_000 < state.elapsed_ms < 16 * 60_000


def test_freeze_credit_is_capped_per_attempt():
    """Disconnecting for ten minutes must not buy ten minutes of thinking time."""
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=30), frozen_ms_total=10 * 60_000
    )

    state = evaluate(attempt)

    expected = 30 * 60_000 - FREEZE_CAP_MS
    assert abs(state.elapsed_ms - expected) < 2_000
    assert state.freeze_remaining_ms == 0


def test_absorb_gap_ignores_short_silences():
    now = utcnow()
    attempt = make_attempt(last_seen_at=now - timedelta(seconds=5))

    credited = clock_module.absorb_gap(attempt, now)

    assert credited == 0
    assert attempt.disconnect_count == 0


def test_absorb_gap_records_a_real_disconnect():
    now = utcnow()
    attempt = make_attempt(last_seen_at=now - timedelta(minutes=3))

    credited = clock_module.absorb_gap(attempt, now)

    assert credited > 0
    assert attempt.disconnect_count == 1
    assert attempt.frozen_ms_total == credited


def test_pause_only_allowed_in_practice_mode():
    test_attempt = make_attempt(mode=AttemptMode.TEST)
    practice = make_attempt(mode=AttemptMode.PRACTICE)

    assert clock_module.begin_pause(test_attempt) is False
    assert clock_module.begin_pause(practice) is True
    assert practice.pause_started_at is not None


def test_sections_stay_unbudgeted_without_sectional_lock():
    sections = fake_sections((1, "Physics", None), (2, "Chemistry", None))
    state = build_section_state(sections, sectional_lock=False)

    assert state["active_index"] == 0
    assert all(entry["budget_ms"] is None for entry in state["sections"])


def test_active_section_consumes_its_own_budget():
    sections = fake_sections((1, "VARC", 40), (2, "DILR", 40), (3, "QA", 40))
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=10),
        total_duration_ms=120 * 60_000,
        sectional_lock=True,
        section_state=build_section_state(sections, sectional_lock=True),
    )

    state = evaluate(attempt)
    active = state.active_section

    assert active is not None
    assert active.name == "VARC"
    assert 29 * 60_000 < (active.remaining_ms or 0) < 31 * 60_000
    assert not any(section.locked for section in state.sections)


def test_expired_sections_lock_and_cascade():
    """A student returning after two section budgets elapsed lands in the third."""
    sections = fake_sections((1, "VARC", 40), (2, "DILR", 40), (3, "QA", 40))
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=85),
        total_duration_ms=120 * 60_000,
        sectional_lock=True,
        section_state=build_section_state(sections, sectional_lock=True),
    )

    state = evaluate(attempt)

    assert [section.locked for section in state.sections] == [True, True, False]
    active = state.active_section
    assert active is not None and active.name == "QA"
    assert 34 * 60_000 < (active.remaining_ms or 0) < 36 * 60_000


def test_answering_a_locked_section_is_refused():
    sections = fake_sections((1, "VARC", 40), (2, "DILR", 40))
    attempt = make_attempt(
        started_at=utcnow() - timedelta(minutes=45),
        total_duration_ms=80 * 60_000,
        sectional_lock=True,
        section_state=build_section_state(sections, sectional_lock=True),
    )

    state = evaluate(attempt)

    assert clock_module.can_answer(attempt, section_id=1, state=state) is False
    assert clock_module.can_answer(attempt, section_id=2, state=state) is True
