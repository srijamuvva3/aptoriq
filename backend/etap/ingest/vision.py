"""Reading scanned pages with a vision model, and tagging questions with topic data.

Model-reported bounding boxes are treated as hints, never as truth. They are good
enough to drive a crop the teacher can nudge in the review editor, and every question
that arrives via this route is marked lower confidence for exactly that reason.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from .providers import VisionProvider
from .schema import (
    BBox,
    Difficulty,
    ExtractionRoute,
    Option,
    Passage,
    Question,
    QuestionType,
)

VISION_CONFIDENCE_CEILING = 0.75
"""No vision-extracted question is ever treated as fully trustworthy."""

EXTRACTION_PROMPT = """You are reading one page of a printed examination question paper.

Extract every question that STARTS on this page. Return strict JSON only.

{
  "section": "subject or section name printed on this page, else null",
  "instructions": "any instruction text about marking or answer format, else null",
  "passages": [
    {"text": "shared passage or 'Directions for Q.x to Q.y' stimulus",
     "question_numbers": [12, 13, 14]}
  ],
  "questions": [
    {
      "number": 12,
      "type": "mcq_single | mcq_multi | numerical",
      "stem": "full question text, with mathematics written as LaTeX between $...$",
      "options": [{"label": "A", "text": "option text"}],
      "has_figure": true,
      "bbox_pct": [x0, y0, x1, y1],
      "confidence": 0.0 to 1.0,
      "note": "anything unclear, cut off, or illegible; else null"
    }
  ]
}

Rules:
- Preserve the paper's own option labels. If it prints (1)(2)(3)(4), use "1","2","3","4".
- A question with no options is "numerical".
- Use "mcq_multi" only when the paper states more than one option may be correct.
- has_figure is true if the question contains a diagram, graph, circuit, chemical
  structure, map or table that cannot be conveyed as plain text.
- bbox_pct is the rectangle enclosing the whole question including its options,
  expressed as percentages of page width and height, origin at the top-left.
- If a question is cut off at the page edge, still return it and say so in "note".
- Do not invent questions, options or answers. Do not solve anything.
"""

TAGGING_PROMPT = """You are labelling questions from an Indian competitive examination paper.

For each question below, return the subject, the specific chapter or topic, and a
difficulty estimate for a well-prepared candidate.

Return strict JSON: {"tags": [{"number": 1, "subject": "...", "chapter": "...",
"difficulty": "easy|medium|hard"}]}

Use the conventional syllabus chapter names for the exam. If a question is unreadable
or you cannot tell, use null for subject and chapter and "medium" for difficulty.

Questions:
"""


def extract_page(
    provider: VisionProvider,
    doc: pymupdf.Document,
    page_index: int,
    dpi: int = 200,
) -> tuple[list[Question], list[Passage], str | None]:
    page = doc[page_index]
    pixmap = page.get_pixmap(dpi=dpi)
    payload = provider.extract(pixmap.tobytes("png"), EXTRACTION_PROMPT)

    section = payload.get("section") or None
    width, height = page.rect.width, page.rect.height

    passages: list[Passage] = []
    for raw in payload.get("passages") or []:
        numbers = [int(n) for n in (raw.get("question_numbers") or []) if _is_int(n)]
        if not numbers:
            continue
        passages.append(
            Passage(
                id=f"p{numbers[0]}-{numbers[-1]}",
                text=(raw.get("text") or "").strip(),
                page=page_index,
                question_numbers=numbers,
            )
        )

    questions: list[Question] = []
    for raw in payload.get("questions") or []:
        question = _build(raw, page_index, width, height, section)
        if question is not None:
            _attach_passage(question, passages)
            questions.append(question)

    return questions, passages, section


def _build(
    raw: dict[str, Any],
    page_index: int,
    width: float,
    height: float,
    section: str | None,
) -> Question | None:
    if not _is_int(raw.get("number")):
        return None

    options = [
        Option(label=str(item.get("label", "")).strip(), text=str(item.get("text", "")).strip())
        for item in (raw.get("options") or [])
        if isinstance(item, dict) and str(item.get("label", "")).strip()
    ]

    reported = raw.get("confidence")
    confidence = float(reported) if isinstance(reported, (int, float)) else 0.6

    question = Question(
        number=int(raw["number"]),
        type=_question_type(raw.get("type"), options),
        section=section,
        stem_text=str(raw.get("stem") or "").strip(),
        options=options,
        source_page=page_index,
        bbox=_bbox(raw.get("bbox_pct"), page_index, width, height),
        route=ExtractionRoute.VISION,
        has_figure=bool(raw.get("has_figure")),
        difficulty=Difficulty.UNKNOWN,
        confidence=min(confidence, VISION_CONFIDENCE_CEILING),
    )

    question.flag("Extracted from a scanned page; verify against the original.")
    if raw.get("note"):
        question.flag(str(raw["note"]), 0.1)
    if not question.stem_text:
        question.flag("Empty question stem.", 0.4)
    if question.bbox is None:
        question.flag("No usable bounding box; crop unavailable.", 0.1)

    return question


def _question_type(raw: Any, options: list[Option]) -> QuestionType:
    value = str(raw or "").strip().lower()
    if value in {item.value for item in QuestionType}:
        return QuestionType(value)
    return QuestionType.MCQ_SINGLE if options else QuestionType.NUMERICAL


def _bbox(raw: Any, page_index: int, width: float, height: float) -> BBox | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None

    if x1 <= x0 or y1 <= y0:
        return None

    return BBox(
        page=page_index,
        x0=max(0.0, x0 / 100.0 * width),
        y0=max(0.0, y0 / 100.0 * height),
        x1=min(width, x1 / 100.0 * width),
        y1=min(height, y1 / 100.0 * height),
    )


def _attach_passage(question: Question, passages: list[Passage]) -> None:
    for passage in passages:
        if question.number in passage.question_numbers:
            question.passage_id = passage.id
            return


def _is_int(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def tag_questions(
    provider: VisionProvider,
    questions: list[Question],
    exam: str | None = None,
    batch_size: int = 25,
) -> list[str]:
    """Fill in subject, chapter and difficulty. Returns warnings."""
    warnings: list[str] = []
    taggable = [q for q in questions if q.stem_text]
    if not taggable:
        return ["No question text available to tag."]

    by_number = {question.number: question for question in questions}

    for start in range(0, len(taggable), batch_size):
        batch = taggable[start : start + batch_size]
        lines = [
            f"{question.number}. [{question.section or 'unknown section'}] "
            f"{question.stem_text[:400]}"
            for question in batch
        ]
        prompt = TAGGING_PROMPT
        if exam:
            prompt = prompt.replace("an Indian competitive examination", f"the {exam} examination")

        try:
            payload = provider.complete(prompt + "\n".join(lines))
        except Exception as error:  # provider failures must not abort ingestion
            warnings.append(f"Tagging failed for questions {batch[0].number}-{batch[-1].number}: {error}")
            continue

        for tag in payload.get("tags") or []:
            if not _is_int(tag.get("number")):
                continue
            question = by_number.get(int(tag["number"]))
            if question is None:
                continue
            question.topic = (tag.get("subject") or None) or question.topic
            question.chapter = (tag.get("chapter") or None) or question.chapter
            difficulty = str(tag.get("difficulty") or "").lower()
            if difficulty in {item.value for item in Difficulty}:
                question.difficulty = Difficulty(difficulty)

    return warnings
