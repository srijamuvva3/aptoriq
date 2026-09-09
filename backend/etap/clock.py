"""Server-authoritative exam timing.

The browser shows a countdown but never decides anything. Remaining time, section
locks and expiry are recomputed from stored timestamps on every request, so a student
who edits their clock, reloads, or hibernates the machine gains nothing.

Time excluded from the clock comes from two sources, deliberately kept separate:

* Pause, available in practice mode only, uncapped.
* Disconnect freeze, capped per attempt. Freezing without a cap turns "pull the network
  cable" into an unlimited thinking-time exploit, which would also corrupt the pacing
  metrics the whole product depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Attempt, AttemptMode, AttemptStatus, Section

FREEZE_CAP_MS = 120_000
"""Total wall time an attempt may have its clock frozen for across all disconnects."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as the UTC they were written as."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class SectionClock:
    section_id: int | None
    name: str
    order_index: int
    budget_ms: int | None
    consumed_ms: int
    remaining_ms: int | None
    locked: bool
    active: bool


@dataclass
class ClockState:
    elapsed_ms: int
    remaining_ms: int
    total_ms: int
    expired: bool
    paused: bool
    freeze_remaining_ms: int
    sectional_lock: bool
    active_section_index: int | None
    sections: list[SectionClock] = field(default_factory=list)

    @property
    def active_section(self) -> SectionClock | None:
        for section in self.sections:
            if section.active:
                return section
        return None


def build_section_state(sections: list[Section], sectional_lock: bool) -> dict:
    """Initial section bookkeeping, stored on the attempt at start."""
    entries = []
    for section in sections:
        budget = (section.duration_min or 0) * 60_000 if sectional_lock else None
        entries.append(
            {
                "section_id": section.id,
                "name": section.name,
                "order_index": section.order_index,
                "budget_ms": budget or None,
                "start_elapsed_ms": None,
                "consumed_ms": 0,
                "locked": False,
            }
        )
    entries.sort(key=lambda entry: entry["order_index"])
    if sectional_lock and entries:
        entries[0]["start_elapsed_ms"] = 0
    return {"active_index": 0 if entries else None, "sections": entries}


def _excluded_ms(attempt: Attempt, now: datetime) -> tuple[int, bool, int]:
    """Milliseconds to exclude from wall time, plus the pause flag."""
    paused = attempt.pause_started_at is not None

    pause_total = attempt.paused_ms_total
    if paused:
        started = as_utc(attempt.pause_started_at)
        assert started is not None
        pause_total += max(0, int((now - started).total_seconds() * 1000))

    capped_freeze = min(attempt.frozen_ms_total, FREEZE_CAP_MS)
    freeze_remaining = max(0, FREEZE_CAP_MS - attempt.frozen_ms_total)
    return pause_total + capped_freeze, paused, freeze_remaining


def evaluate(attempt: Attempt, now: datetime | None = None) -> ClockState:
    """Recompute the clock, advancing and locking sections as needed.

    Mutates `attempt.section_state` when a section expires. The caller commits.
    """
    now = now or utcnow()
    started = as_utc(attempt.started_at) or now

    if attempt.status is not AttemptStatus.IN_PROGRESS:
        finished = as_utc(attempt.submitted_at) or now
        reference = finished
    else:
        reference = now

    excluded, paused, freeze_remaining = _excluded_ms(attempt, reference)
    wall_ms = max(0, int((reference - started).total_seconds() * 1000))
    elapsed = max(0, wall_ms - excluded)
    total = attempt.total_duration_ms
    remaining = max(0, total - elapsed)

    sections = _advance_sections(attempt, elapsed)

    return ClockState(
        elapsed_ms=elapsed,
        remaining_ms=remaining,
        total_ms=total,
        expired=elapsed >= total > 0,
        paused=paused,
        freeze_remaining_ms=freeze_remaining,
        sectional_lock=attempt.sectional_lock,
        active_section_index=attempt.section_state.get("active_index"),
        sections=sections,
    )


def _advance_sections(attempt: Attempt, elapsed_ms: int) -> list[SectionClock]:
    state = attempt.section_state or {}
    entries: list[dict] = list(state.get("sections") or [])
    if not entries:
        return []

    if not attempt.sectional_lock:
        return [
            SectionClock(
                section_id=entry.get("section_id"),
                name=entry.get("name", ""),
                order_index=entry.get("order_index", 0),
                budget_ms=None,
                consumed_ms=0,
                remaining_ms=None,
                locked=False,
                active=False,
            )
            for entry in entries
        ]

    active_index = state.get("active_index")
    changed = False

    # A student may return after several section budgets have elapsed, so cascade.
    while active_index is not None and active_index < len(entries):
        entry = entries[active_index]
        budget = entry.get("budget_ms")
        if not budget:
            break
        start = entry.get("start_elapsed_ms")
        if start is None:
            start = elapsed_ms
            entry["start_elapsed_ms"] = start
            changed = True

        consumed = max(0, elapsed_ms - start)
        if consumed < budget:
            entry["consumed_ms"] = consumed
            break

        entry["consumed_ms"] = budget
        entry["locked"] = True
        changed = True
        next_index = active_index + 1
        if next_index < len(entries):
            entries[next_index]["start_elapsed_ms"] = start + budget
            active_index = next_index
        else:
            active_index = None
        state["active_index"] = active_index

    if changed:
        state["sections"] = entries
        # Reassigning the attribute is required for SQLAlchemy to detect a JSON change.
        attempt.section_state = dict(state)

    clocks: list[SectionClock] = []
    for index, entry in enumerate(entries):
        budget = entry.get("budget_ms")
        consumed = entry.get("consumed_ms", 0)
        clocks.append(
            SectionClock(
                section_id=entry.get("section_id"),
                name=entry.get("name", ""),
                order_index=entry.get("order_index", index),
                budget_ms=budget,
                consumed_ms=consumed,
                remaining_ms=max(0, budget - consumed) if budget else None,
                locked=bool(entry.get("locked")),
                active=index == active_index,
            )
        )
    return clocks


def can_answer(attempt: Attempt, section_id: int | None, state: ClockState) -> bool:
    """Whether a question in this section is currently open for input."""
    if attempt.status is not AttemptStatus.IN_PROGRESS or state.expired:
        return False
    if state.paused:
        return False
    if not attempt.sectional_lock:
        return True
    active = state.active_section
    if active is None:
        return False
    return active.section_id == section_id


def begin_pause(attempt: Attempt, now: datetime | None = None) -> bool:
    if attempt.mode is not AttemptMode.PRACTICE:
        return False
    if attempt.pause_started_at is not None:
        return True
    attempt.pause_started_at = now or utcnow()
    return True


def end_pause(attempt: Attempt, now: datetime | None = None) -> None:
    started = as_utc(attempt.pause_started_at)
    if started is None:
        return
    reference = now or utcnow()
    attempt.paused_ms_total += max(0, int((reference - started).total_seconds() * 1000))
    attempt.pause_started_at = None


HEARTBEAT_GAP_MS = 20_000
"""A silence longer than this is read as the client having gone away.

The client cannot tell us it lost the network, so the server infers disconnects from
missed heartbeats instead of trusting a signal that by definition cannot arrive.
"""


def absorb_gap(attempt: Attempt, now: datetime | None = None) -> int:
    """Credit a heartbeat gap to frozen time. Returns the milliseconds credited.

    The per-attempt cap is applied when the clock is evaluated, not here, so the true
    disconnected duration stays on record for the teacher even once it stops buying the
    student any time.
    """
    reference = now or utcnow()
    last_seen = as_utc(attempt.last_seen_at)
    attempt.last_seen_at = reference

    if last_seen is None or attempt.pause_started_at is not None:
        return 0

    gap = int((reference - last_seen).total_seconds() * 1000)
    if gap < HEARTBEAT_GAP_MS:
        return 0

    attempt.frozen_ms_total += gap
    attempt.disconnect_count += 1
    return gap
