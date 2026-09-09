"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_session_factory
from ..models import Role, User
from ..security import read_token


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")

    data = read_token(authorization.split(" ", 1)[1].strip())
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")

    user = db.get(User, data.get("uid"))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists.")
    return user


def current_teacher(user: User = Depends(current_user)) -> User:
    if user.role is not Role.TEACHER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Teacher access required.")
    return user


def current_student(user: User = Depends(current_user)) -> User:
    if user.role is not Role.STUDENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Student access required.")
    return user
