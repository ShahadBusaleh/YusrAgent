"""Read/write helpers for users and roles. Never return password_hash to callers that serialize HTTP."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db.connection import row_to_dict, rows_to_dicts

USER_PUBLIC_COLUMNS = (
    "user_id, employee_id, username, role, is_active, created_at, last_login_at"
)


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute(
        "SELECT user_id, employee_id, username, password_hash, role, is_active, "
        "created_at, last_login_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return row_to_dict(row)


def get_user_by_id(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {USER_PUBLIC_COLUMNS} FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row_to_dict(row)


def get_user_auth_by_id(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT user_id, employee_id, username, password_hash, role, is_active, "
        "created_at, last_login_at FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row_to_dict(row)


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {USER_PUBLIC_COLUMNS} FROM users ORDER BY username"
    ).fetchall()
    return rows_to_dicts(rows)


def list_roles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT role_name, description FROM roles ORDER BY role_name"
    ).fetchall()
    return rows_to_dicts(rows)


def update_last_login(conn: sqlite3.Connection, user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET last_login_at = ? WHERE user_id = ?",
        (now, user_id),
    )


def update_user(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    role: str | None = None,
    is_active: int | None = None,
    password_hash: str | None = None,
) -> dict | None:
    current = get_user_by_id(conn, user_id)
    if current is None:
        return None
    if role is not None:
        conn.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
    if is_active is not None:
        conn.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?", (is_active, user_id)
        )
    if password_hash is not None:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (password_hash, user_id),
        )
    return get_user_by_id(conn, user_id)
