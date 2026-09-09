"""Built-in exam profiles.

These are starting values, not rules. Every field is overridable per paper in the
review editor, because boards change patterns between sessions and a profile that
silently imposes last year's marking scheme is worse than no profile at all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionTemplate(BaseModel):
    name: str
    duration_min: int | None = None
    """Only set when the exam runs a separate clock per section."""
    question_count: int | None = None
    marks: float = 4.0
    negative_marks: float = 1.0
    partial_credit: bool = False
    """JEE Advanced awards partial marks on multiple-correct questions."""


class ExamProfile(BaseModel):
    id: str
    name: str
    total_duration_min: int
    sectional_lock: bool = False
    calculator: bool = False
    sections: list[SectionTemplate] = Field(default_factory=list)
    notes: str | None = None

    def section_for(self, name: str | None) -> SectionTemplate | None:
        if not name:
            return None
        target = name.strip().lower()
        for section in self.sections:
            if section.name.lower() == target or target in section.name.lower():
                return section
        return None

    @property
    def default_marks(self) -> tuple[float, float]:
        if not self.sections:
            return 4.0, 1.0
        first = self.sections[0]
        return first.marks, first.negative_marks


def _mcq(name: str, count: int | None = None, marks: float = 4.0, negative: float = 1.0, **kwargs) -> SectionTemplate:
    return SectionTemplate(name=name, question_count=count, marks=marks, negative_marks=negative, **kwargs)


BUILTIN_PROFILES: dict[str, ExamProfile] = {
    "neet": ExamProfile(
        id="neet",
        name="NEET UG",
        total_duration_min=180,
        sections=[_mcq("Physics", 45), _mcq("Chemistry", 45), _mcq("Biology", 90)],
    ),
    "neet_pg": ExamProfile(
        id="neet_pg",
        name="NEET PG",
        total_duration_min=210,
        sections=[_mcq("General", 200, marks=4.0, negative=1.0)],
    ),
    "jee_main": ExamProfile(
        id="jee_main",
        name="JEE Main",
        total_duration_min=180,
        sections=[_mcq("Physics", 25), _mcq("Chemistry", 25), _mcq("Mathematics", 25)],
        notes="Numerical-value questions carry +4 with negative marking in current sessions.",
    ),
    "jee_advanced": ExamProfile(
        id="jee_advanced",
        name="JEE Advanced",
        total_duration_min=180,
        sections=[
            _mcq("Physics", partial_credit=True),
            _mcq("Chemistry", partial_credit=True),
            _mcq("Mathematics", partial_credit=True),
        ],
        notes="Marking varies by question block within a single paper; expect to override per section.",
    ),
    "gate": ExamProfile(
        id="gate",
        name="GATE",
        total_duration_min=180,
        calculator=True,
        sections=[
            _mcq("General Aptitude", 10, marks=1.0, negative=0.33),
            _mcq("Core Subject", 55, marks=1.0, negative=0.33),
        ],
        notes="Mixed 1-mark and 2-mark questions; numerical-answer questions carry no negative marking.",
    ),
    "upsc_prelims": ExamProfile(
        id="upsc_prelims",
        name="UPSC Prelims (GS Paper I)",
        total_duration_min=120,
        sections=[_mcq("General Studies", 100, marks=2.0, negative=0.66)],
    ),
    "cat": ExamProfile(
        id="cat",
        name="CAT",
        total_duration_min=120,
        sectional_lock=True,
        calculator=True,
        sections=[
            SectionTemplate(name="VARC", duration_min=40, question_count=24, marks=3.0, negative_marks=1.0),
            SectionTemplate(name="DILR", duration_min=40, question_count=22, marks=3.0, negative_marks=1.0),
            SectionTemplate(name="QA", duration_min=40, question_count=22, marks=3.0, negative_marks=1.0),
        ],
        notes="Sections lock on expiry. Non-MCQ (TITA) questions carry no negative marking.",
    ),
    "custom": ExamProfile(
        id="custom",
        name="Custom",
        total_duration_min=180,
        sections=[],
        notes="Define sections, timing and marking in the review editor.",
    ),
}


def get_profile(identifier: str | None) -> ExamProfile | None:
    if not identifier:
        return None
    return BUILTIN_PROFILES.get(identifier.strip().lower())
