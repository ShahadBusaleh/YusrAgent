"""Connection helper for the existing agentic_hr.db file. No migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator

from app.config import get_settings


def get_connection() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(settings.sqlite_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str) -> str:
    """Allocate the next prefixed id (e.g. LR00001) without changing schema."""
    sql = f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY {column} DESC LIMIT 1"
    row = conn.execute(sql, (f"{prefix}%",)).fetchone()
    if row is None:
        return f"{prefix}00001"
    current = str(row[0])
    digits = "".join(ch for ch in current[len(prefix) :] if ch.isdigit())
    n = int(digits or "0") + 1
    width = max(len(digits), 5)
    return f"{prefix}{n:0{width}d}"
