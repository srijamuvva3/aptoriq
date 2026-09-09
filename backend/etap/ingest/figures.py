"""Deciding which questions carry a figure, and cutting crops out of the source PDF.

Crops are generated for every question, not only figure-bearing ones, because the
review editor shows the original alongside the parsed text so the teacher can verify
the extraction without opening the PDF separately.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .document import Page
from .schema import BBox, Question, RenderMode

CROP_PAD = 6.0
MIN_OVERLAP_AREA = 400.0
"""Square points. Smaller intersections are usually a stray rule or a bullet glyph."""


def _area(box: BBox) -> float:
    return max(0.0, box.x1 - box.x0) * max(0.0, box.y1 - box.y0)


def _intersection_area(a: BBox, b: BBox) -> float:
    if a.page != b.page:
        return 0.0
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
        0.0, min(a.y1, b.y1) - max(a.y0, b.y0)
    )


def detect_figures(questions: list[Question], pages: dict[int, Page]) -> None:
    for question in questions:
        if question.bbox is None:
            continue
        page = pages.get(question.bbox.page)
        if page is None:
            continue

        graphics = page.image_rects + page.drawing_rects
        overlap = sum(_intersection_area(question.bbox, rect) for rect in graphics)
        if overlap >= MIN_OVERLAP_AREA:
            question.has_figure = True
            question.render_mode = RenderMode.IMAGE


def generate_crops(
    doc: pymupdf.Document,
    questions: list[Question],
    output_dir: str | Path,
    dpi: int = 200,
) -> None:
    crop_dir = Path(output_dir)
    crop_dir.mkdir(parents=True, exist_ok=True)

    for question in questions:
        if question.bbox is None:
            continue
        page = doc[question.bbox.page]
        padded = question.bbox.padded(CROP_PAD, page.rect.width, page.rect.height)
        clip = pymupdf.Rect(padded.x0, padded.y0, padded.x1, padded.y1)
        if clip.is_empty or clip.width < 4 or clip.height < 4:
            question.flag("Bounding box too small to crop.", 0.1)
            continue

        path = crop_dir / f"q{question.number:03d}.png"
        pixmap = page.get_pixmap(dpi=dpi, clip=clip)
        pixmap.save(path)
        question.crop_path = str(path)

        if question.render_mode is RenderMode.IMAGE:
            question.stem_image_path = str(path)
