"""Reading answer keys.

Keys arrive as CSVs, as tables inside a PDF, or as something close enough to a table
that a regex can rescue it. All three paths converge on {question number: [values]}.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pymupdf

DROPPED_MARKERS = {"bonus", "dropped", "cancelled", "all", "any", "-", "--", "na", "n/a"}

NUMBER_HEADERS = ("question", "q.no", "qno", "q no", "no.", "number", "sno", "s.no", "srno")
ANSWER_HEADERS = ("answer", "correct", "key", "ans", "option", "response")

PAIR = re.compile(
    r"\b(\d{1,3})\s*[.):\-]?\s*[\(\[]?\s*"
    r"([A-Da-d](?:\s*[,/&]\s*[A-Da-d])*|[1-4](?:\s*[,/&]\s*[1-4])*|-?\d+(?:\.\d+)?)"
    r"\s*[\)\]]?(?=\s|$)"
)


def normalise_values(raw: str) -> list[str]:
    """Turn a key cell into a list of answer values.

    Handles 'A', '(b)', 'A,C', 'AC', 'B & D', '12.5' and dropped-question markers.
    """
    text = raw.strip().strip("()[]").strip()
    if not text:
        return []
    if text.lower() in DROPPED_MARKERS:
        return []

    parts = [part for part in re.split(r"[,/&;|\s]+", text) if part]
    values: list[str] = []
    for part in parts:
        cleaned = part.strip("()[].").strip()
        if not cleaned:
            continue
        if re.fullmatch(r"[A-Da-d]{2,}", cleaned):
            values.extend(char.upper() for char in cleaned)
        elif re.fullmatch(r"[A-Da-d]", cleaned):
            values.append(cleaned.upper())
        else:
            values.append(cleaned)

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def load_answer_key(path: str | Path) -> tuple[dict[int, list[str]], list[str]]:
    key_path = Path(path)
    if not key_path.exists():
        raise FileNotFoundError(f"Answer key not found: {key_path}")

    suffix = key_path.suffix.lower()
    if suffix == ".csv":
        return _from_csv(key_path)
    if suffix == ".pdf":
        return _from_pdf(key_path)
    raise ValueError(f"Unsupported answer key format: {suffix}. Use CSV or PDF.")


def _from_csv(path: Path) -> tuple[dict[int, list[str]], list[str]]:
    warnings: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [row for row in csv.reader(handle, dialect) if any(cell.strip() for cell in row)]

    if not rows:
        return {}, ["Answer key CSV is empty."]

    header = [cell.strip().lower() for cell in rows[0]]
    number_col, answer_col = 0, 1
    has_header = False

    for index, cell in enumerate(header):
        if any(token in cell for token in NUMBER_HEADERS):
            number_col, has_header = index, True
        elif any(token in cell for token in ANSWER_HEADERS):
            answer_col, has_header = index, True

    body = rows[1:] if has_header else rows
    if not has_header:
        warnings.append("Answer key CSV has no recognisable header; assumed column 1 = question, column 2 = answer.")

    key: dict[int, list[str]] = {}
    for row in body:
        if len(row) <= max(number_col, answer_col):
            continue
        raw_number = row[number_col].strip()
        match = re.search(r"\d{1,3}", raw_number)
        if not match:
            continue
        number = int(match.group())
        values = normalise_values(row[answer_col])
        if number in key:
            warnings.append(f"Answer key lists question {number} more than once; kept the first.")
            continue
        key[number] = values

    return key, warnings


def _from_pdf(path: Path) -> tuple[dict[int, list[str]], list[str]]:
    warnings: list[str] = []
    key: dict[int, list[str]] = {}

    with pymupdf.open(path) as doc:
        for page in doc:
            for table in page.find_tables().tables:
                _absorb_table(table.extract(), key)

        if not key:
            warnings.append("No tables detected in the key PDF; fell back to text pattern matching.")
            for page in doc:
                for line in page.get_text("text").splitlines():
                    for number, value in PAIR.findall(line):
                        index = int(number)
                        if index not in key:
                            key[index] = normalise_values(value)

    if not key:
        warnings.append("Could not read any answers from the key PDF.")
    return key, warnings


def _absorb_table(rows: list[list[str | None]], key: dict[int, list[str]]) -> None:
    """Read a table that may hold several question/answer column pairs side by side."""
    for row in rows:
        cells = [(cell or "").strip() for cell in row]
        for index in range(len(cells) - 1):
            left, right = cells[index], cells[index + 1]
            if not re.fullmatch(r"\d{1,3}", left):
                continue
            values = normalise_values(right)
            if not values:
                continue
            number = int(left)
            key.setdefault(number, values)


def align_to_options(values: list[str], option_labels: list[str]) -> list[str]:
    """Reconcile key notation with the paper's option labels.

    A key saying 'B' against a paper printing options as (1)(2)(3)(4) means the second
    option, and vice versa.
    """
    if not values or not option_labels:
        return values

    labels = set(option_labels)
    aligned: list[str] = []
    for value in values:
        if value in labels:
            aligned.append(value)
        elif re.fullmatch(r"[A-D]", value):
            position = ord(value) - ord("A")
            aligned.append(
                option_labels[position] if position < len(option_labels) else value
            )
        elif re.fullmatch(r"[1-9]", value):
            position = int(value) - 1
            aligned.append(
                option_labels[position] if position < len(option_labels) else value
            )
        else:
            aligned.append(value)
    return aligned
