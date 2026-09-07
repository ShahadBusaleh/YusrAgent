from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.schemas import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.config import get_settings
from app.db import employees as employees_db
from app.db import users as users_db
from app.db.audit import write_audit
from app.security.passwords import verify_password
from app.security.tokens import (
    decode_token,
    issue_access_token,
    issue_refresh_token,
    revoke_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, conn: sqlite3.Connection = Depends(get_db)) -> TokenResponse:
    user = users_db.get_user_by_username(conn, body.username)
    if user is None or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    users_db.update_last_login(conn, user["user_id"])
    write_audit(
        conn,
        actor=user["username"],
        event_type="login",
        employee_id=user["employee_id"],
        details="Successful login",
    )
    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.jwt_access_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, conn: sqlite3.Connection = Depends(get_db)) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from exc
    user = users_db.get_user_auth_by_id(conn, payload["sub"])
    if user is None or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive or unknown user",
        )
    settings = get_settings()
    revoke_token(body.refresh_token)
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.jwt_access_minutes * 60,
    )


@router.post("/logout")
def logout(
    body: LogoutRequest | None = Body(default=None),
    authorization: str | None = Header(default=None),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    if authorization and authorization.lower().startswith("bearer "):
        revoke_token(authorization.split(" ", 1)[1].strip())
    if body and body.refresh_token:
        revoke_token(body.refresh_token)
    return {"ok": True, "username": user.username}


@router.get("/me", response_model=MeResponse)
def me(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> MeResponse:
    employee = employees_db.get_employee(conn, user.employee_id)
    return MeResponse(
        user_id=user.user_id,
        employee_id=user.employee_id,
        username=user.username,
        role=user.role,
        is_active=True,
        full_name=(employee or {}).get("full_name"),
        job_title=(employee or {}).get("job_title"),
    )
