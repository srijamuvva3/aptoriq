"""Password hashing and signed session tokens."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_TTL_SECONDS = 60 * 60 * 12
"""A token must outlive the longest exam plus overrun, hence half a day."""

_SECRET_FILE = Path(__file__).resolve().parents[2] / "data" / ".secret_key"
BCRYPT_MAX_BYTES = 72


def secret_key() -> str:
    configured = os.getenv("ETAP_SECRET_KEY")
    if configured:
        return configured
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text(encoding="utf-8").strip()

    generated = secrets.token_urlsafe(48)
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_FILE.write_text(generated, encoding="utf-8")
    _SECRET_FILE.chmod(0o600)
    return generated


def hash_password(password: str) -> str:
    # bcrypt silently ignores bytes past 72; truncating explicitly keeps verify honest.
    payload = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    payload = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(payload, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key(), salt="etap-session")


def issue_token(user_id: int, role: str) -> str:
    return _serializer().dumps({"uid": user_id, "role": role})


def read_token(token: str) -> dict | None:
    try:
        return _serializer().loads(token, max_age=TOKEN_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
