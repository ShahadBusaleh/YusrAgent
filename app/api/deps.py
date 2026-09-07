"""FastAPI dependencies: JWT user + RBAC."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWTError

from app.db.connection import get_db
from app.db import users as users_db
from app.security.tokens import decode_token

ROLES = ("employee", "hr_specialist", "hr_manager", "admin")


@dataclass
class CurrentUser:
    user_id: str
    employee_id: str
    username: str
    role: str


def roles_at_least(min_role: str) -> tuple[str, ...]:
    idx = ROLES.index(min_role)
    return ROLES[idx:]


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return authorization.split(" ", 1)[1].strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> CurrentUser:
    token = _bearer_token(authorization)
    try:
        payload = decode_token(token, expected_type="access")
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    user = users_db.get_user_by_id(conn, payload["sub"])
    if user is None or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive or unknown user",
        )
    return CurrentUser(
        user_id=user["user_id"],
        employee_id=user["employee_id"],
        username=user["username"],
        role=user["role"],
    )


def require_role(*roles: str):
    """403 unless JWT role is in the allowed set. Apply per-route."""

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    return dependency


def owned_employee_id(user: CurrentUser, requested: str | None = None) -> str:
    """Employees may only act on their JWT employee_id. Higher roles may pass one."""
    if user.role == "employee":
        return user.employee_id
    return requested or user.employee_id
