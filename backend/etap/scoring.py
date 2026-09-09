"""Marking a response and totalling an attempt.

Unanswered questions never attract a negative mark, which is what every real exam does
and is also what makes the risk metrics meaningful: skipping must be a genuinely
different choice from guessing.
"""

from __future__ import annotations

from .models import Attempt, Question, Response

NUMERIC_DEFAULT_TOLERANCE = 0.0


def _as_float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def grade(question: Question, value: list) -> tuple[bool | None, float]:
    """Returns (is_correct, marks_awarded). is_correct is None when ungradable."""
    chosen = [str(item).strip() for item in (value or []) if str(item).strip()]
    correct = [str(item).strip() for item in (question.correct_values or []) if str(item).strip()]

    if not chosen:
        return None, 0.0
    if not correct:
        # Dropped or unkeyed question: never penalise a student for it.
        return None, 0.0

    qtype = question.type

    if qtype == "numerical":
        tolerance = question.numeric_tolerance
        if tolerance is None:
            tolerance = NUMERIC_DEFAULT_TOLERANCE
        given = _as_float(chosen[0])
        for expected_raw in correct:
            expected = _as_float(expected_raw)
            if given is not None and expected is not None:
                if abs(given - expected) <= tolerance:
                    return True, question.marks
            elif chosen[0].lower() == expected_raw.lower():
                return True, question.marks
        return False, -abs(question.negative_marks)

    if qtype == "mcq_multi":
        chosen_set, correct_set = set(chosen), set(correct)
        if chosen_set - correct_set:
            return False, -abs(question.negative_marks)
        if chosen_set == correct_set:
            return True, question.marks
        if question.partial_credit and chosen_set:
            share = len(chosen_set & correct_set) / len(correct_set)
            return False, round(question.marks * share, 2)
        return False, -abs(question.negative_marks)

    # Single-correct, and anything unclassified is treated the same way.
    if chosen[0] in correct:
        return True, question.marks
    return False, -abs(question.negative_marks)


def apply_grade(response: Response, question: Question) -> None:
    is_correct, marks = grade(question, response.value)
    response.is_correct = is_correct
    response.marks_awarded = marks


def score_attempt(attempt: Attempt, questions: list[Question]) -> tuple[float, float]:
    by_id = {question.id: question for question in questions}
    total = 0.0
    for response in attempt.responses:
        question = by_id.get(response.question_id)
        if question is None:
            continue
        apply_grade(response, question)
        total += response.marks_awarded or 0.0

    max_score = sum(question.marks for question in questions)
    attempt.score = round(total, 2)
    attempt.max_score = round(max_score, 2)
    return attempt.score, attempt.max_score
