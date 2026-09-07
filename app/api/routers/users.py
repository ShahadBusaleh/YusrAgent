from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, require_role
from app.api.schemas import RoleOut, UserOut, UserUpdate
from app.db.connection import get_db
from app.db import users as users_db
from app.db.audit import write_audit
from app.security.passwords import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(
    user: CurrentUser = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return users_db.list_users(conn)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    user: CurrentUser = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return users_db.list_roles(conn)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    body: UserUpdate,
    user: CurrentUser = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    password_hash = hash_password(body.password) if body.password else None
    updated = users_db.update_user(
        conn,
        user_id,
        role=body.role,
        is_active=None if body.is_active is None else int(body.is_active),
        password_hash=password_hash,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    write_audit(
        conn,
        actor=user.username,
        event_type="user_update",
        employee_id=updated.get("employee_id"),
        details=user_id,
    )
    return updated
