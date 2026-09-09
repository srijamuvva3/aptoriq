"""Opening a PDF, deciding how each page must be read, and rasterising it.

Every page is rasterised regardless of route. The images are the source for figure
crops and the reference the teacher sees in the review editor, so they are needed
even when the text layer is perfect.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .schema import BBox, ExtractionRoute, PageInfo

TEXT_LAYER_MIN_CHARS = 180
"""Below this a page is treated as scanned. A cover page with a logo and a title can
legitimately sit under it, which is why image coverage is also considered."""

SCANNED_IMAGE_COVERAGE = 0.6
BLANK_PAGE_MAX_CHARS = 60
"""A page with only a running header carries no questions and needs no vision call."""

COLUMN_GUTTER_MIN_PT = 8.0
"""Minimum clear vertical channel, in points, before two columns are believed."""

DRAWING_CLUSTER_PAD = 6.0
"""Strokes within this distance of each other are treated as one figure."""
MAX_DRAWINGS = 4000
"""Clustering is quadratic; pathological vector pages are truncated rather than hung on."""


def _cluster_boxes(boxes: list[BBox], pad: float) -> list[BBox]:
    """Merge boxes that touch or nearly touch into their enclosing rectangles."""
    if not boxes:
        return []

    remaining = sorted(boxes[:MAX_DRAWINGS], key=lambda box: (box.y0, box.x0))
    clusters: list[BBox] = []

    for box in remaining:
        merged = box
        index = 0
        while index < len(clusters):
            other = clusters[index]
            near = (
                merged.x0 - pad <= other.x1
                and other.x0 - pad <= merged.x1
                and merged.y0 - pad <= other.y1
                and other.y0 - pad <= merged.y1
            )
            if near:
                merged = merged.union(other)
                clusters.pop(index)
                index = 0
            else:
                index += 1
        clusters.append(merged)

    return clusters


@dataclass
class Line:
    text: str
    bbox: BBox
    size: float
    bold: bool
    column: int = 0

    @property
    def stripped(self) -> str:
        return self.text.strip()


@dataclass
class Page:
    index: int
    info: PageInfo
    lines: list[Line] = field(default_factory=list)
    image_rects: list[BBox] = field(default_factory=list)
    drawing_rects: list[BBox] = field(default_factory=list)

    @property
    def has_graphics(self) -> bool:
        return bool(self.image_rects or self.drawing_rects)


class Document:
    def __init__(self, pdf_path: str | Path, dpi: int = 200):
        self.path = Path(pdf_path)
        if not self.path.exists():
            raise FileNotFoundError(f"PDF not found: {self.path}")
        self.dpi = dpi
        self.scale = dpi / 72.0
        self.doc = pymupdf.open(self.path)
        self.pages: list[Page] = []

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.doc.close()

    def analyse(self, render_dir: str | Path | None = None) -> list[Page]:
        render_path = Path(render_dir) if render_dir else None
        if render_path:
            render_path.mkdir(parents=True, exist_ok=True)

        self.pages = []
        for index, fitz_page in enumerate(self.doc):
            page = self._analyse_page(index, fitz_page, render_path)
            self.pages.append(page)
        return self.pages

    def _analyse_page(
        self, index: int, fitz_page: pymupdf.Page, render_dir: Path | None
    ) -> Page:
        rect = fitz_page.rect
        raw = fitz_page.get_text("dict")

        lines: list[Line] = []
        char_count = 0
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans)
                if not text.strip():
                    continue
                char_count += len(text.strip())
                sizes = [span.get("size", 0.0) for span in spans] or [0.0]
                flags = [span.get("flags", 0) for span in spans] or [0]
                x0, y0, x1, y1 = line["bbox"]
                lines.append(
                    Line(
                        text=text,
                        bbox=BBox(page=index, x0=x0, y0=y0, x1=x1, y1=y1),
                        size=max(sizes),
                        # bit 4 of PyMuPDF span flags marks a bold face
                        bold=any(flag & 2**4 for flag in flags),
                    )
                )

        image_rects = self._image_rects(fitz_page, index)
        drawing_rects = self._drawing_rects(fitz_page, index)

        page_area = max(rect.width * rect.height, 1.0)
        image_area = sum((b.x1 - b.x0) * (b.y1 - b.y0) for b in image_rects)
        image_ratio = min(image_area / page_area, 1.0)

        blank = char_count < BLANK_PAGE_MAX_CHARS and image_ratio < 0.05 and not drawing_rects
        scanned = not blank and (
            char_count < TEXT_LAYER_MIN_CHARS
            or (
                image_ratio > SCANNED_IMAGE_COVERAGE
                and char_count < TEXT_LAYER_MIN_CHARS * 3
            )
        )
        route = ExtractionRoute.VISION if scanned else ExtractionRoute.TEXT_LAYER

        columns = (
            self._assign_columns(lines, rect.width, rect.height) if not scanned else 1
        )

        render_file = None
        if render_dir is not None:
            render_file = render_dir / f"page-{index + 1:03d}.png"
            pixmap = fitz_page.get_pixmap(dpi=self.dpi)
            pixmap.save(render_file)

        info = PageInfo(
            index=index,
            width=rect.width,
            height=rect.height,
            char_count=char_count,
            has_text_layer=not scanned,
            image_area_ratio=round(image_ratio, 4),
            column_count=columns,
            blank=blank,
            route=route,
            render_path=str(render_file) if render_file else None,
        )
        return Page(
            index=index,
            info=info,
            lines=lines,
            image_rects=image_rects,
            drawing_rects=drawing_rects,
        )

    @staticmethod
    def _image_rects(fitz_page: pymupdf.Page, index: int) -> list[BBox]:
        rects: list[BBox] = []
        for info in fitz_page.get_images(full=True):
            xref = info[0]
            try:
                for rect in fitz_page.get_image_rects(xref):
                    rects.append(
                        BBox(page=index, x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)
                    )
            except (ValueError, RuntimeError):
                continue
        return rects

    @staticmethod
    def _drawing_rects(fitz_page: pymupdf.Page, index: int) -> list[BBox]:
        """Vector drawings: circuit diagrams, graphs, chemical structures.

        A plotted curve is hundreds of individual short strokes, none of them large
        enough to look like a figure on its own, so nearby strokes are clustered
        first and the size test is applied to the cluster. Thin rules and underlines
        survive as their own tiny clusters and are then discarded.
        """
        raw: list[BBox] = []
        for drawing in fitz_page.get_drawings():
            rect = drawing.get("rect")
            if rect is None or rect.is_empty:
                continue
            raw.append(BBox(page=index, x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1))

        clusters = _cluster_boxes(raw, pad=DRAWING_CLUSTER_PAD)
        page_area = max(fitz_page.rect.width * fitz_page.rect.height, 1.0)

        kept: list[BBox] = []
        for box in clusters:
            width, height = box.x1 - box.x0, box.y1 - box.y0
            area = width * height
            if width < 20 or height < 20 or area < 400:
                continue
            # A page border or full-page watermark is not a per-question figure.
            if area > page_area * 0.75:
                continue
            kept.append(box)
        return kept

    @staticmethod
    def _assign_columns(lines: list[Line], page_width: float, page_height: float) -> int:
        """Detect a two-column layout by locating the gutter.

        Reading a two-column page in raw y-order interleaves the columns into
        nonsense, and the question numbering then looks wildly out of sequence. The
        gutter is found by sweeping the middle of the page for the widest band that
        no text line crosses, rather than assuming a centred split — papers are
        frequently asymmetric.

        Column membership is judged by how far each side reaches down the page, not
        by how many lines it holds. A column occupied largely by diagrams contains
        few lines while still being a full column.
        """
        if len(lines) < 12:
            return 1

        total = len(lines)
        step = 2.0
        candidates: list[tuple[float, int]] = []
        candidate = page_width * 0.3
        limit = page_width * 0.7

        while candidate <= limit:
            crossing = sum(
                1
                for line in lines
                if line.bbox.x0 < candidate - 2 and line.bbox.x1 > candidate + 2
            )
            candidates.append((candidate, crossing))
            candidate += step

        # Running headers and section banners legitimately span both columns.
        tolerance = max(2, int(total * 0.05))
        clear = [x for x, crossing in candidates if crossing <= tolerance]
        if not clear:
            return 1

        runs: list[list[float]] = [[clear[0]]]
        for value in clear[1:]:
            if value - runs[-1][-1] <= step + 0.01:
                runs[-1].append(value)
            else:
                runs.append([value])

        widest = max(runs, key=len)
        split = (widest[0] + widest[-1]) / 2.0

        left = [line for line in lines if line.bbox.x1 <= split + 2]
        right = [line for line in lines if line.bbox.x0 >= split - 2]
        if len(left) < 5 or len(right) < 5:
            return 1

        def vertical_span(group: list[Line]) -> float:
            return max(line.bbox.y1 for line in group) - min(line.bbox.y0 for line in group)

        if min(vertical_span(left), vertical_span(right)) < page_height * 0.25:
            return 1

        gutter = min(line.bbox.x0 for line in right) - max(line.bbox.x1 for line in left)
        if gutter < COLUMN_GUTTER_MIN_PT:
            return 1

        for line in lines:
            line.column = 0 if line.bbox.x1 <= split + 2 else 1
        return 2

    @staticmethod
    def reading_order(page: Page) -> list[Line]:
        """Lines in the order a human would read them.

        On a two-column page, any line spanning most of the page width (a running
        header, a section banner, a directions block) acts as a horizontal divider.
        Columns are read top to bottom within each band between dividers, which keeps
        a mid-page section header from scrambling the columns around it.
        """
        by_position = sorted(
            page.lines, key=lambda line: (round(line.bbox.y0, 1), line.bbox.x0)
        )
        if page.info.column_count < 2:
            return by_position

        span_threshold = page.info.width * 0.7
        ordered: list[Line] = []
        band: list[Line] = []

        def flush() -> None:
            for column in (0, 1):
                ordered.extend(
                    sorted(
                        (line for line in band if line.column == column),
                        key=lambda line: (line.bbox.y0, line.bbox.x0),
                    )
                )
            band.clear()

        for line in by_position:
            if line.bbox.x1 - line.bbox.x0 > span_threshold:
                flush()
                ordered.append(line)
            else:
                band.append(line)
        flush()
        return ordered

    @staticmethod
    def strip_furniture(pages: list[Page]) -> int:
        """Drop running headers, footers and page numbers.

        Left in place they get absorbed into whichever question happens to straddle
        the page break, which corrupts that question's stem and its bounding box.
        Only the top and bottom margins are considered, and a line must recur across
        most of the document to be removed.
        """
        text_pages = [page for page in pages if page.lines]
        if len(text_pages) < 3:
            return 0

        def normalise(text: str) -> str:
            return re.sub(r"\d+", "#", " ".join(text.split())).lower()

        occurrences: dict[str, set[int]] = {}
        for page in text_pages:
            height = page.info.height
            for line in page.lines:
                in_margin = (
                    line.bbox.y1 <= height * 0.12 or line.bbox.y0 >= height * 0.88
                )
                if in_margin and line.stripped:
                    occurrences.setdefault(normalise(line.stripped), set()).add(page.index)

        threshold = max(3, int(len(text_pages) * 0.6))
        furniture = {key for key, seen in occurrences.items() if len(seen) >= threshold}
        if not furniture:
            return 0

        removed = 0
        for page in text_pages:
            height = page.info.height
            kept: list[Line] = []
            for line in page.lines:
                in_margin = (
                    line.bbox.y1 <= height * 0.12 or line.bbox.y0 >= height * 0.88
                )
                if in_margin and normalise(line.stripped) in furniture:
                    removed += 1
                    continue
                kept.append(line)
            page.lines = kept
        return removed

    @staticmethod
    def body_font_size(pages: list[Page]) -> float:
        sizes = [line.size for page in pages for line in page.lines if line.size > 0]
        return statistics.median(sizes) if sizes else 10.0
