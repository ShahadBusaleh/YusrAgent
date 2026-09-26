"""Persistent cache for translated display text (additive table).

Stored free text (grievance complaints, Consultant assessments, HR notes,
growth plans, Decision Brief policy text) is translated for the reader's
interface language by app.agents.translation.localize_text. Translations
are kept here so they survive API restarts and each text is translated at
most once per target language. Keyed by a SHA-256 of the source text; the
source itself is not duplicated.
"""

from __future__ import annotations

import hashlib
import sqlite3


def ensure_translation_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_cache (
            text_hash TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (text_hash, target_lang)
        )
        """
    )


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def database_path(conn: sqlite3.Connection | None) -> str | None:
    """File path of `conn`'s main database; None for no connection or an
    in-memory DB. Lets callers hand the cache the *request's* DB."""
    if conn is None:
        return None
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    return (row[2] if row else None) or None


def get_cached_translation(conn: sqlite3.Connection, text: str, target_lang: str) -> str | None:
    # Reads never create the table (a read must not change the schema).
    try:
        row = conn.execute(
            "SELECT translated_text FROM translation_cache WHERE text_hash = ? AND target_lang = ?",
            (text_hash(text), target_lang),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def save_translation(conn: sqlite3.Connection, text: str, target_lang: str, translated: str) -> None:
    ensure_translation_cache_table(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO translation_cache (text_hash, target_lang, translated_text)
        VALUES (?, ?, ?)
        """,
        (text_hash(text), target_lang, translated),
    )
