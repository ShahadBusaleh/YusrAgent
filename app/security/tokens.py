"""JWT access + refresh tokens. Revocation is in-memory (dev scaffolding)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

_REVOKED_JTI: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_token(claims: dict, *, token_type: str, ttl: timedelta) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        **claims,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def issue_access_token(user: dict) -> str:
    settings = get_settings()
    return issue_token(
        {
            "sub": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "employee_id": user["employee_id"],
        },
        token_type="access",
        ttl=timedelta(minutes=settings.jwt_access_minutes),
    )


def issue_refresh_token(user: dict) -> str:
    settings = get_settings()
    return issue_token(
        {
            "sub": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "employee_id": user["employee_id"],
        },
        token_type="refresh",
        ttl=timedelta(days=settings.jwt_refresh_days),
    )


def decode_token(token: str, *, expected_type: str) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Unexpected token type")
    jti = payload.get("jti")
    if jti and jti in _REVOKED_JTI:
        raise jwt.InvalidTokenError("Token revoked")
    return payload


def revoke_token(token: str) -> None:
    try:
        settings = get_settings()
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return
    jti = payload.get("jti")
    if jti:
        _REVOKED_JTI.add(jti)
