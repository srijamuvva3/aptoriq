"""End-to-end ingestion: PDF in, verified-pending ParsedPaper out.

Nothing here decides that an extraction is correct. The job is to produce the best
draft it can and to be loud about everything it is unsure of, so the review editor
can put the doubtful questions in front of the teacher first.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..profiles import ExamProfile, get_profile
from . import answer_key as key_module
from . import figures, textlayer, vision
from .document import Document
from .providers import ProviderError, VisionProvider, build_provider
from .schema import ExtractionRoute, ParsedPaper, Question, QuestionType

LOW_CONFIDENCE = 0.7


@dataclass
class IngestOptions:
    dpi: int = 200
    profile_id: str | None = None
    provider_name: str | None = None
    model: str | None = None
    use_vision: bool = True
    tag: bool = True
    title: str | None = None


def parse_paper(
    pdf_path: str | Path,
    output_dir: str | Path,
    key_path: str | Path | None = None,
    options: IngestOptions | None = None,
) -> ParsedPaper:
    options = options or IngestOptions()
    pdf_path = Path(pdf_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Re-parsing a paper must not leave renders or crops from the previous run
    # behind; a stale crop silently attached to the wrong question is worse than
    # a missing one.
    for stale in (out / "pages", out / "crops"):
        if stale.exists():
            shutil.rmtree(stale)

    profile = get_profile(options.profile_id)
    paper = ParsedPaper(
        title=options.title or pdf_path.stem,
        profile=profile.id if profile else None,
        source_pdf=str(pdf_path),
        key_source=str(key_path) if key_path else None,
    )

    with Document(pdf_path, dpi=options.dpi) as document:
        pages = document.analyse(render_dir=out / "pages")
        paper.pages = [page.info for page in pages]

        questions, passages, sections, warnings = textlayer.extract_questions(pages, document)
        for warning in warnings:
            paper.warn(warning)
        paper.passages.extend(passages)
        paper.sections.extend(sections)

        provider = _maybe_provider(paper, options)
        scanned = [page for page in pages if page.info.route is ExtractionRoute.VISION]

        if scanned and provider is not None:
            existing = {question.number for question in questions}
            for page in scanned:
                try:
                    found, page_passages, section = vision.extract_page(
                        provider, document.doc, page.index, options.dpi
                    )
                except ProviderError as error:
                    paper.warn(f"Vision extraction failed on page {page.index + 1}: {error}")
                    continue

                if section and section not in paper.sections:
                    paper.sections.append(section)
                paper.passages.extend(page_passages)
                for question in found:
                    if question.number in existing:
                        paper.warn(
                            f"Question {question.number} was found on both a text page and a "
                            f"scanned page; kept the text-layer version."
                        )
                        continue
                    existing.add(question.number)
                    questions.append(question)
        elif scanned and provider is None:
            paper.warn(
                f"{len(scanned)} page(s) have no text layer and were skipped because no vision "
                f"provider is configured. Questions on those pages are missing."
            )

        questions.sort(key=lambda question: question.number)
        paper.questions = questions

        page_lookup = {page.index: page for page in pages}
        figures.detect_figures(paper.questions, page_lookup)
        figures.generate_crops(document.doc, paper.questions, out / "crops", options.dpi)

    if profile:
        _apply_profile(paper, profile)

    if key_path:
        _merge_key(paper, key_path)

    if options.tag and provider is not None:
        for warning in vision.tag_questions(
            provider, paper.questions, exam=profile.name if profile else None
        ):
            paper.warn(warning)

    _validate(paper)
    paper.recompute_stats(LOW_CONFIDENCE)

    (out / "paper.json").write_text(paper.model_dump_json(indent=2), encoding="utf-8")
    return paper


def _maybe_provider(paper: ParsedPaper, options: IngestOptions) -> VisionProvider | None:
    if not options.use_vision:
        return None
    try:
        return build_provider(options.provider_name, options.model)
    except ProviderError as error:
        paper.warn(str(error))
        return None


def _apply_profile(paper: ParsedPaper, profile: ExamProfile) -> None:
    fallback_marks, fallback_negative = profile.default_marks
    for question in paper.questions:
        template = profile.section_for(question.section)
        question.marks = template.marks if template else fallback_marks
        question.negative_marks = (
            template.negative_marks if template else fallback_negative
        )
        # Numerical questions frequently escape negative marking even where MCQs do not.
        if question.type is QuestionType.NUMERICAL and profile.id in {"gate", "cat"}:
            question.negative_marks = 0.0


def _merge_key(paper: ParsedPaper, key_path: str | Path) -> None:
    try:
        key, warnings = key_module.load_answer_key(key_path)
    except (FileNotFoundError, ValueError) as error:
        paper.warn(f"Answer key not loaded: {error}")
        return

    for warning in warnings:
        paper.warn(warning)

    if not key:
        return

    for question in paper.questions:
        values = key.get(question.number)
        if values is None:
            question.flag("No entry for this question in the answer key.", 0.1)
            continue
        if not values:
            question.flag("Answer key marks this question as dropped or bonus.")
            continue

        labels = [option.label for option in question.options]
        question.correct_values = key_module.align_to_options(values, labels)

        if labels and any(value not in labels for value in question.correct_values):
            question.flag(
                f"Answer key value {question.correct_values} does not match this question's "
                f"option labels {labels}.",
                0.3,
            )
        if question.type is QuestionType.MCQ_SINGLE and len(question.correct_values) > 1:
            question.type = QuestionType.MCQ_MULTI
            question.flag("Answer key lists several correct options; type changed to multiple-correct.")

    extra = sorted(set(key) - {question.number for question in paper.questions})
    if extra:
        preview = ", ".join(str(number) for number in extra[:10])
        suffix = "..." if len(extra) > 10 else ""
        paper.warn(
            f"Answer key contains {len(extra)} question number(s) not found in the paper: {preview}{suffix}"
        )


def _validate(paper: ParsedPaper) -> None:
    numbers = [question.number for question in paper.questions]
    if not numbers:
        paper.warn("No questions were extracted from this PDF.")
        return

    duplicates = {number for number in numbers if numbers.count(number) > 1}
    if duplicates:
        paper.warn(f"Duplicate question numbers: {sorted(duplicates)}")

    missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
    if missing:
        preview = ", ".join(str(number) for number in missing[:15])
        suffix = "..." if len(missing) > 15 else ""
        paper.warn(f"Gaps in question numbering: {preview}{suffix}")

    for question in paper.questions:
        if question.type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI) and len(question.options) < 2:
            question.flag("Fewer than two options for a multiple-choice question.", 0.3)

    orphan_passages = [
        passage.id
        for passage in paper.passages
        if not any(question.passage_id == passage.id for question in paper.questions)
    ]
    if orphan_passages:
        paper.warn(f"Passages with no questions attached: {orphan_passages}")


def load_paper(path: str | Path) -> ParsedPaper:
    return ParsedPaper.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
