"""Answer keys arrive in inconsistent notation; these pin down the normalisation."""

from __future__ import annotations

from pathlib import Path

import pytest

from etap.ingest.answer_key import align_to_options, load_answer_key, normalise_values


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A", ["A"]),
        ("(b)", ["B"]),
        (" c ", ["C"]),
        ("A,C", ["A", "C"]),
        ("AC", ["A", "C"]),
        ("B & D", ["B", "D"]),
        ("2", ["2"]),
        ("12.5", ["12.5"]),
        ("-3", ["-3"]),
        ("Bonus", []),
        ("dropped", []),
        ("", []),
        ("A, A", ["A"]),
    ],
)
def test_normalise_values(raw: str, expected: list[str]) -> None:
    assert normalise_values(raw) == expected


def test_align_letter_key_to_numeric_options() -> None:
    assert align_to_options(["B"], ["1", "2", "3", "4"]) == ["2"]


def test_align_numeric_key_to_letter_options() -> None:
    assert align_to_options(["3"], ["A", "B", "C", "D"]) == ["C"]


def test_align_leaves_numerical_answers_alone() -> None:
    assert align_to_options(["12.5"], []) == ["12.5"]


def test_csv_without_header(tmp_path: Path) -> None:
    path = tmp_path / "key.csv"
    path.write_text("1,A\n2,B\n3,C\n", encoding="utf-8")
    key, warnings = load_answer_key(path)
    assert key == {1: ["A"], 2: ["B"], 3: ["C"]}
    assert any("no recognisable header" in warning for warning in warnings)


def test_csv_with_header(tmp_path: Path) -> None:
    path = tmp_path / "key.csv"
    path.write_text("Question No.,Correct Answer\n1,A\n2,(B)\n", encoding="utf-8")
    key, warnings = load_answer_key(path)
    assert key == {1: ["A"], 2: ["B"]}
    assert warnings == []


def test_csv_with_reordered_columns(tmp_path: Path) -> None:
    path = tmp_path / "key.csv"
    path.write_text("Answer,Q.No\nA,1\nD,2\n", encoding="utf-8")
    key, _ = load_answer_key(path)
    assert key == {1: ["A"], 2: ["D"]}


def test_unsupported_format_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "key.txt"
    path.write_text("1 A", encoding="utf-8")
    with pytest.raises(ValueError):
        load_answer_key(path)
