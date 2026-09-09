"""Serving question crops and passage images.

Paths come from the database rather than the request, but they are still resolved and
checked against the data directory: a paper imported from a hand-edited JSON file could
otherwise point at any file the server process can read.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..models import Passage, Question, User
from .deps import current_user, get_db

router = APIRouter(prefix="/api", tags=["media"])

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"


def allowed_roots() -> list[Path]:
    """Directories images may be served from.

    Papers can legitimately live outside the repository (an external drive, or a pytest
    temporary directory), so extra roots are configurable rather than the check being
    loosened.
    """
    roots = [DATA_ROOT.resolve()]
    for entry in os.getenv("ETAP_MEDIA_ROOTS", "").split(os.pathsep):
        if entry.strip():
            roots.append(Path(entry.strip()).resolve())
    return roots


def _safe_file(raw: str | None) -> Path:
    if not raw:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No image for this item.")

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate

    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(root) for root in allowed_roots()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Image path is outside the permitted directories."
        )
    if not resolved.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image file is missing on disk.")
    return resolved


@router.get("/questions/{question_id}/image")
def question_image(
    question_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")

    path = _safe_file(question.stem_image_path or question.crop_path)
    return FileResponse(path, media_type="image/png")


@router.get("/passages/{passage_id}/image")
def passage_image(
    passage_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    passage = db.get(Passage, passage_id)
    if passage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passage not found.")
    return FileResponse(_safe_file(passage.image_path), media_type="image/png")
