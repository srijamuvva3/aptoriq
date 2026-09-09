"""Regression tests for the ingestion pipeline.

Each assertion here corresponds to a failure mode that actually occurred while the
parser was being built, not to a hypothetical one.
"""

from __future__ import annotations

import json
from pathlib import Path

from etap.ingest.schema import ParsedPaper, QuestionType


def test_extracts_every_question(parsed_digital: ParsedPaper, sample_dir: Path) -> None:
    truth = json.loads((sample_dir / "sample_paper_truth.json").read_text())
    expected = {question["number"] for question in truth["questions"]}
    assert {question.number for question in parsed_digital.questions} == expected


def test_two_column_page_is_detected(parsed_digital: ParsedPaper) -> None:
    """Falling back to single-column reading interleaves the two columns, which is
    how questions 1 to 6 originally went missing. Pages whose content happens to fit
    in the left column alone are correctly reported as single-column."""
    content_pages = [page for page in parsed_digital.pages if not page.blank]
    assert content_pages[0].column_count == 2
    assert all(page.column_count in (1, 2) for page in content_pages)


def test_question_numbering_is_contiguous(parsed_digital: ParsedPaper) -> None:
    numbers = sorted(question.number for question in parsed_digital.questions)
    assert numbers == list(range(numbers[0], numbers[-1] + 1))


def test_sections_are_detected(parsed_digital: ParsedPaper) -> None:
    assert parsed_digital.sections == ["Physics", "Chemistry", "Mathematics"]
    assert all(question.section for question in parsed_digital.questions)


def test_options_are_clean(parsed_digital: ParsedPaper, sample_dir: Path) -> None:
    """Instruction prose following the final option must not be absorbed into it."""
    truth = {
        question["number"]: question
        for question in json.loads((sample_dir / "sample_paper_truth.json").read_text())["questions"]
    }
    for question in parsed_digital.questions:
        expected = truth[question.number]["options"]
        assert [option.text for option in question.options] == expected


def test_numerical_questions_have_no_options(parsed_digital: ParsedPaper) -> None:
    numerical = [
        question
        for question in parsed_digital.questions
        if question.type is QuestionType.NUMERICAL
    ]
    assert numerical
    assert all(not question.options for question in numerical)


def test_passage_is_attached_to_its_questions(parsed_digital: ParsedPaper) -> None:
    """A wrapped stem line beginning with the word 'passage' once created a bogus
    passage that swallowed the following question's options."""
    assert len(parsed_digital.passages) == 1
    passage = parsed_digital.passages[0]
    attached = {
        question.number
        for question in parsed_digital.questions
        if question.passage_id == passage.id
    }
    assert attached == set(passage.question_numbers)
    assert len(attached) == 2


def test_figures_are_detected_and_cropped(parsed_digital: ParsedPaper) -> None:
    """A plotted curve is many tiny strokes; without clustering it looks like no
    figure at all."""
    with_figures = [question for question in parsed_digital.questions if question.has_figure]
    assert len(with_figures) == 2
    for question in with_figures:
        assert question.stem_image_path and Path(question.stem_image_path).exists()


def test_every_question_gets_a_crop(parsed_digital: ParsedPaper) -> None:
    for question in parsed_digital.questions:
        assert question.crop_path and Path(question.crop_path).exists()


def test_answer_key_is_merged(parsed_digital: ParsedPaper) -> None:
    assert all(question.correct_values for question in parsed_digital.questions)
    assert parsed_digital.stats.questions_missing_key == 0


def test_profile_marking_is_applied(parsed_digital: ParsedPaper) -> None:
    assert all(question.marks == 4.0 for question in parsed_digital.questions)
    assert all(question.negative_marks == 1.0 for question in parsed_digital.questions)


def test_running_header_is_stripped(parsed_digital: ParsedPaper) -> None:
    assert not any(
        "SAMPLE ENTRANCE EXAMINATION" in question.stem_text
        for question in parsed_digital.questions
    )


def test_clean_parse_raises_no_warnings(parsed_digital: ParsedPaper) -> None:
    assert parsed_digital.warnings == []
    assert parsed_digital.stats.low_confidence_count == 0
