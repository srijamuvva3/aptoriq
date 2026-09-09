"""Loading a parsed paper into the database.

Marking is resolved here rather than at exam time. Once an attempt starts it reads
marks straight off the question row, so a teacher editing the paper later cannot
retroactively change how an in-flight attempt is scored.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from .ingest.pipeline import load_paper
from .ingest.schema import ParsedPaper, QuestionType
from .models import Paper, PaperStatus, Passage, Question, Section
from .profiles import ExamProfile, get_profile

NO_PROFILE_MARKS = 1.0
NO_PROFILE_NEGATIVE = 0.0
"""Without a profile we refuse to invent negative marking; the teacher sets it."""


def import_parsed_paper(
    session: Session,
    parsed: ParsedPaper,
    *,
    profile_id: str | None = None,
    created_by_id: int | None = None,
    title: str | None = None,
) -> Paper:
    profile = get_profile(profile_id or parsed.profile)

    paper = Paper(
        title=title or parsed.title,
        profile_id=profile.id if profile else None,
        status=PaperStatus.DRAFT,
        total_duration_min=profile.total_duration_min if profile else 180,
        sectional_lock=profile.sectional_lock if profile else False,
        calculator=profile.calculator if profile else False,
        source_pdf=parsed.source_pdf,
        key_source=parsed.key_source,
        parse_warnings=list(parsed.warnings),
        created_by_id=created_by_id,
    )
    session.add(paper)
    session.flush()

    sections = _create_sections(session, paper, parsed, profile)
    passages = _create_passages(session, paper, parsed)
    _create_questions(session, paper, parsed, profile, sections, passages)

    session.flush()
    return paper


def import_parsed_file(
    session: Session,
    path: str | Path,
    *,
    profile_id: str | None = None,
    created_by_id: int | None = None,
    title: str | None = None,
) -> Paper:
    return import_parsed_paper(
        session,
        load_paper(path),
        profile_id=profile_id,
        created_by_id=created_by_id,
        title=title,
    )


def _create_sections(
    session: Session, paper: Paper, parsed: ParsedPaper, profile: ExamProfile | None
) -> dict[str, Section]:
    names = list(parsed.sections)

    # A paper whose section headers were not detected still needs somewhere to hang its
    # questions, and the profile is a better guess than a single unnamed bucket.
    if not names and profile and profile.sections:
        names = [template.name for template in profile.sections]
    if not names:
        names = ["General"]

    created: dict[str, Section] = {}
    for index, name in enumerate(names):
        template = profile.section_for(name) if profile else None
        section = Section(
            paper_id=paper.id,
            name=name,
            order_index=index,
            duration_min=template.duration_min if template else None,
        )
        session.add(section)
        created[name.lower()] = section

    session.flush()
    return created


def _create_passages(
    session: Session, paper: Paper, parsed: ParsedPaper
) -> dict[str, Passage]:
    created: dict[str, Passage] = {}
    for parsed_passage in parsed.passages:
        passage = Passage(
            paper_id=paper.id,
            key=parsed_passage.id,
            text=parsed_passage.text,
            image_path=parsed_passage.image_path,
        )
        session.add(passage)
        created[parsed_passage.id] = passage
    session.flush()
    return created


def _resolve_marks(
    question, profile: ExamProfile | None
) -> tuple[float, float, bool]:
    if question.marks is not None and question.negative_marks is not None:
        marks, negative = question.marks, question.negative_marks
    elif profile:
        template = profile.section_for(question.section)
        if template:
            marks, negative = template.marks, template.negative_marks
        else:
            marks, negative = profile.default_marks
    else:
        marks, negative = NO_PROFILE_MARKS, NO_PROFILE_NEGATIVE

    template = profile.section_for(question.section) if profile else None
    partial = bool(template.partial_credit) if template else False
    return marks, negative, partial


def _create_questions(
    session: Session,
    paper: Paper,
    parsed: ParsedPaper,
    profile: ExamProfile | None,
    sections: dict[str, Section],
    passages: dict[str, Passage],
) -> None:
    default_section = next(iter(sections.values()), None)

    for parsed_question in parsed.questions:
        section = None
        if parsed_question.section:
            section = sections.get(parsed_question.section.lower())
            if section is None:
                for name, candidate in sections.items():
                    if parsed_question.section.lower() in name or name in parsed_question.section.lower():
                        section = candidate
                        break
        section = section or default_section

        marks, negative, partial = _resolve_marks(parsed_question, profile)
        passage = passages.get(parsed_question.passage_id or "")

        session.add(
            Question(
                paper_id=paper.id,
                section_id=section.id if section else None,
                passage_id=passage.id if passage else None,
                number=parsed_question.number,
                type=parsed_question.type.value
                if isinstance(parsed_question.type, QuestionType)
                else str(parsed_question.type),
                stem_text=parsed_question.stem_text,
                options=[option.model_dump() for option in parsed_question.options],
                render_mode=parsed_question.render_mode.value,
                stem_image_path=parsed_question.stem_image_path,
                crop_path=parsed_question.crop_path,
                marks=marks,
                negative_marks=negative,
                partial_credit=partial,
                correct_values=list(parsed_question.correct_values),
                numeric_tolerance=parsed_question.numeric_tolerance,
                solution_text=parsed_question.solution_text,
                topic=parsed_question.topic,
                chapter=parsed_question.chapter,
                difficulty=parsed_question.difficulty.value,
                has_figure=parsed_question.has_figure,
                source_page=parsed_question.source_page,
                confidence=parsed_question.confidence,
                notes=list(parsed_question.notes),
                verified=parsed_question.verified,
            )
        )
