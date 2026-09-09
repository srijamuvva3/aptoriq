"""Login and identity."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User
from ..security import issue_token, verify_password
from .deps import current_user, get_db
from .schemas import LoginRequest, LoginResponse, UserOut
from .serializers import user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalars(
        select(User).where(User.username == payload.username.strip().lower())
    ).first()

    if user is None or not verify_password(payload.password, user.password_hash):
        # Same message either way, so the response cannot be used to enumerate accounts.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password.")

    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return LoginResponse(token=issue_token(user.id, role), user=user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return user_out(user)
