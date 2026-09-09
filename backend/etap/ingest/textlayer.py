"""Segmenting a digital PDF's text layer into questions.

The hard part is not reading the text, it is deciding where one question ends and the
next begins. A bare number followed by a full stop appears constantly inside question
bodies (dates, quantities, equation numbering), so a candidate marker is only accepted
when it continues a plausible numbering sequence.
"""

from __future__ import annotations

import re

from .document import Document, Line, Page
from .schema import (
    BBox,
    Difficulty,
    ExtractionRoute,
    Option,
    Passage,
    Question,
    QuestionType,
    RenderMode,
)

QUESTION_MARKER = re.compile(
    # The negative lookahead keeps decimals such as "0.1 M acetic acid" at the start
    # of a wrapped line from being read as question number 0.
    r"^\s*(?:Q(?:ues(?:tion)?)?[\s.\-:]*)?[\(\[]?(\d{1,3})[\)\].:](?!\d)\s*(.*)$",
    re.IGNORECASE,
)
OPTION_MARKER = re.compile(r"^\s*[\(\[]?([A-Da-d])[\)\].:]\s*(.*)$")
NUMERIC_OPTION_MARKER = re.compile(r"^\s*[\(\[]?([1-4])[\)\].:]\s*(.*)$")

SECTION_KEYWORDS = (
    "physics",
    "chemistry",
    "mathematics",
    "maths",
    "biology",
    "botany",
    "zoology",
    "general aptitude",
    "verbal ability",
    "reading comprehension",
    "data interpretation",
    "logical reasoning",
    "quantitative ability",
    "varc",
    "dilr",
)
SECTION_HEADER = re.compile(
    r"^\s*(?:section|part)\s*[-–:]?\s*([A-Z0-9]{1,3})\b(.*)$", re.IGNORECASE
)

DIRECTIONS = re.compile(
    r"(?:directions?|instructions?)\s*[:\(]?\s*(?:for\s+)?"
    r"(?:questions?|Q\.?)?\s*(\d{1,3})\s*(?:to|-|–|—)\s*(\d{1,3})",
    re.IGNORECASE,
)
PASSAGE_HEADER = re.compile(
    # Deliberately strict and case-sensitive: a standalone heading such as
    # "Passage II" or "Comprehension:". Matching the word anywhere would fire on a
    # wrapped stem line that happens to begin "passage, the quantity ...", which
    # then swallows the rest of that question.
    r"^\s*(Passage|Comprehension|Paragraph)\b\s*[-–—:]?\s*(?:[IVXLC]+|\d{1,2})?\s*[:.]?\s*$"
)

INSTRUCTION_LINE = re.compile(
    r"this section contains|each (?:correct|incorrect) answer|carries\s*[+\-−]?\s*\d|"
    r"directions?\s+for|options? (?:are|is) (?:not )?provided|the following questions?\b|"
    r"numerical value as the answer|round(?:ed)?\s+off|"
    r"marking scheme|attempt all questions",
    re.IGNORECASE,
)
INSTRUCTION_MIN_LENGTH = 30
"""Instruction prose runs long. A short line matching the pattern is more likely an
option or a stem fragment, so length guards against over-eager truncation."""

MULTI_CORRECT_HINT = re.compile(
    r"one\s+or\s+more|more\s+than\s+one\s+(?:option|choice|answer)|"
    r"multiple\s+(?:correct|options?)",
    re.IGNORECASE,
)
NUMERICAL_HINT = re.compile(
    r"numerical\s+value|integer\s+(?:type|value|answer)|"
    r"round(?:ed)?\s+off\s+to|nearest\s+integer",
    re.IGNORECASE,
)

MAX_NUMBER_GAP = 3
"""How far the numbering may jump before we assume the marker is a false positive."""


class _Candidate:
    __slots__ = ("number", "index", "remainder")

    def __init__(self, number: int, index: int, remainder: str):
        self.number = number
        self.index = index
        self.remainder = remainder


def extract_questions(
    pages: list[Page], document: Document
) -> tuple[list[Question], list[Passage], list[str], list[str]]:
    """Returns questions, passages, section names and warnings."""

    text_pages = [
        page
        for page in pages
        if page.info.route is ExtractionRoute.TEXT_LAYER and not page.info.blank
    ]
    if not text_pages:
        return [], [], [], []

    Document.strip_furniture(text_pages)

    flat: list[Line] = []
    for page in text_pages:
        flat.extend(Document.reading_order(page))

    sections, section_at, section_indices = _detect_sections(flat)
    starts, warnings = _question_starts(flat)

    if not starts:
        return [], [], sections, ["No question markers found in the text layer."]

    passages, passage_lines = _detect_passages(flat, starts)

    questions: list[Question] = []
    for position, candidate in enumerate(starts):
        end = starts[position + 1].index if position + 1 < len(starts) else len(flat)
        # A section banner ends the previous question, otherwise the last question of
        # a section swallows the next section's header and instruction block.
        boundaries = [index for index in section_indices if candidate.index < index < end]
        if boundaries:
            end = min(boundaries)
        block = flat[candidate.index : end]
        block = [line for line in block if id(line) not in passage_lines]
        question = _build_question(candidate, block, section_at)
        _attach_passage(question, passages)
        questions.append(question)

    _infer_types(questions, flat, starts)
    return questions, passages, sections, warnings


def _detect_sections(
    lines: list[Line],
) -> tuple[list[str], dict[int, str], set[int]]:
    """Map each line index to the section in force at that point.

    Also returns the indices of the header lines themselves, which act as hard
    boundaries when slicing question blocks.
    """
    sections: list[str] = []
    section_at: dict[int, str] = {}
    header_indices: set[int] = set()
    current: str | None = None

    for index, line in enumerate(lines):
        text = line.stripped
        name: str | None = None

        if len(text) <= 60:
            match = SECTION_HEADER.match(text)
            if match:
                trailing = match.group(2).strip(" -–:")
                name = trailing.title() if trailing else f"Section {match.group(1).upper()}"
            else:
                lowered = text.lower().strip(" :-–()[]")
                if lowered in SECTION_KEYWORDS and (line.bold or text.isupper()):
                    name = lowered.title()

        if name:
            current = name
            header_indices.add(index)
            if name not in sections:
                sections.append(name)
        if current:
            section_at[index] = current

    return sections, section_at, header_indices


def _question_starts(lines: list[Line]) -> tuple[list[_Candidate], list[str]]:
    """Accept markers that keep the numbering monotonic and roughly contiguous."""
    warnings: list[str] = []
    accepted: list[_Candidate] = []
    last = 0

    for index, line in enumerate(lines):
        match = QUESTION_MARKER.match(line.stripped)
        if not match:
            continue
        number = int(match.group(1))
        remainder = match.group(2).strip()

        if number <= last:
            continue
        if number > last + MAX_NUMBER_GAP and accepted:
            continue
        # A marker with nothing after it and nothing below is a stray page number.
        if not remainder and index + 1 >= len(lines):
            continue
        if accepted and number > last + 1:
            missing = ", ".join(str(n) for n in range(last + 1, number))
            warnings.append(f"Question numbering jumps from {last} to {number} (missing {missing}).")

        accepted.append(_Candidate(number, index, remainder))
        last = number

    return accepted, warnings


def _detect_passages(
    lines: list[Line], starts: list[_Candidate]
) -> tuple[list[Passage], set[int]]:
    """Find shared stimuli and the range of questions they serve."""
    passages: list[Passage] = []
    consumed: set[int] = set()
    start_indices = {candidate.index for candidate in starts}

    for index, line in enumerate(lines):
        text = line.stripped
        match = DIRECTIONS.search(text)
        is_header = bool(match) or bool(PASSAGE_HEADER.match(text))
        if not is_header or index in start_indices:
            continue

        cursor = index
        body: list[str] = []
        while cursor < len(lines) and cursor not in start_indices:
            body.append(lines[cursor].stripped)
            consumed.add(id(lines[cursor]))
            cursor += 1

        if match:
            first, last = int(match.group(1)), int(match.group(2))
            numbers = list(range(first, last + 1))
        else:
            following = [c.number for c in starts if c.index >= cursor]
            numbers = following[:1]

        if not numbers:
            continue

        passages.append(
            Passage(
                id=f"p{numbers[0]}-{numbers[-1]}",
                text=" ".join(body).strip(),
                page=line.bbox.page,
                question_numbers=numbers,
            )
        )

    return passages, consumed


def _build_question(
    candidate: _Candidate, block: list[Line], section_at: dict[int, str]
) -> Question:
    if not block:
        return Question(
            number=candidate.number,
            source_page=0,
            confidence=0.0,
            notes=["Empty question block."],
        )

    page = block[0].bbox.page
    bbox = block[0].bbox
    spans_pages = False
    for line in block:
        if line.bbox.page != page:
            spans_pages = True
            continue
        bbox = bbox.union(line.bbox)

    stem_parts: list[str] = []
    if candidate.remainder:
        stem_parts.append(candidate.remainder)

    options: list[Option] = []
    numeric_style = False
    current: Option | None = None

    for line in block[1:]:
        text = line.stripped
        if not text:
            continue

        # Instruction prose after the final option belongs to the next block, not to
        # this question. Before any option appears it may genuinely be part of the
        # stem, so only trailing instructions are cut.
        if (
            options
            and len(text) >= INSTRUCTION_MIN_LENGTH
            and INSTRUCTION_LINE.search(text)
        ):
            break

        letter = OPTION_MARKER.match(text)
        expected_letter = chr(ord("A") + len(options))
        if letter and letter.group(1).upper() == expected_letter and not numeric_style:
            current = Option(label=letter.group(1).upper(), text=letter.group(2).strip())
            options.append(current)
            continue

        numeric = NUMERIC_OPTION_MARKER.match(text)
        expected_number = str(len(options) + 1)
        if numeric and numeric.group(1) == expected_number and (numeric_style or not options):
            numeric_style = True
            current = Option(label=numeric.group(1), text=numeric.group(2).strip())
            options.append(current)
            continue

        if current is not None:
            current.text = f"{current.text} {text}".strip()
        else:
            stem_parts.append(text)

    question = Question(
        number=candidate.number,
        stem_text=" ".join(stem_parts).strip(),
        options=options,
        source_page=page,
        bbox=bbox,
        section=section_at.get(candidate.index),
        route=ExtractionRoute.TEXT_LAYER,
        render_mode=RenderMode.TEXT,
        difficulty=Difficulty.UNKNOWN,
        confidence=1.0,
    )

    if spans_pages:
        question.flag("Question continues onto the next page; crop shows the first page only.", 0.05)
    if not question.stem_text:
        question.flag("Empty question stem.", 0.4)
    elif len(question.stem_text) < 12:
        question.flag("Question stem looks too short.", 0.25)
    if options and len(options) not in (2, 4, 5):
        question.flag(f"Unusual option count ({len(options)}).", 0.2)
    if any(not option.text for option in options):
        question.flag("One or more options have no text.", 0.2)

    return question


def _attach_passage(question: Question, passages: list[Passage]) -> None:
    for passage in passages:
        if question.number in passage.question_numbers:
            question.passage_id = passage.id
            return


def _infer_types(
    questions: list[Question], lines: list[Line], starts: list[_Candidate]
) -> None:
    """Decide question type from option count plus nearby instruction text.

    Multiple-correct and numerical questions are usually announced by an instruction
    paragraph above the block rather than by anything in the question itself.
    """
    instruction_windows: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        text = line.stripped
        if MULTI_CORRECT_HINT.search(text):
            instruction_windows.append((index, "multi"))
        elif NUMERICAL_HINT.search(text):
            instruction_windows.append((index, "numerical"))

    def hint_for(line_index: int) -> str | None:
        active: str | None = None
        for index, kind in instruction_windows:
            if index <= line_index:
                active = kind
            else:
                break
        return active

    for candidate, question in zip(starts, questions):
        hint = hint_for(candidate.index)

        if not question.options:
            question.type = QuestionType.NUMERICAL
            if hint != "numerical" and not NUMERICAL_HINT.search(question.stem_text):
                question.flag(
                    "No options found; assumed numerical entry. Confirm this is not a parsing failure.",
                    0.3,
                )
            continue

        if hint == "multi" or MULTI_CORRECT_HINT.search(question.stem_text):
            question.type = QuestionType.MCQ_MULTI
        else:
            question.type = QuestionType.MCQ_SINGLE
