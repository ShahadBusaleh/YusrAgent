"""Regulation update agent (Phase 4): versions, duplicate protection, the
simulate-mode guard, and applying an approved regulation_update.

regulation_versions is the one new table. It keeps every version of every
text this feature touches — never deleted:

- kind='article'      one Labor Law article as published (Arabic), per source
- kind='law_row'      a saudi_labor_law row (JSON of its text columns)
- kind='policy'       a company_policies rule
- kind='announcement' an HRSD news item that mentions the Labor Law

Existing tables keep their columns. Rows are updated only by
apply_regulation_decision (called from approvals._sync_regulation_update)
after a human approved the request.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import ROOT, get_settings
from app.db.audit import write_audit

SHARED_COLLECTION = "yusor_policies"
TRACKED_POLICY_DIR = ROOT / "policy_texts"
_REAL_DBS = {(ROOT / "agentic_hr.db").resolve(), (ROOT / "agentic_hr.backup.db").resolve()}

ARTICLE_STATUSES = ("current", "pending", "superseded", "repealed", "rejected", "sent_back")
LAW_TEXT_FIELDS = ("category", "article", "title", "rule", "conditions", "exceptions")


class RegulationGuardError(RuntimeError):
    """Simulated data would touch the real DB, files or shared collection."""


class FourEyesError(PermissionError):
    """The approver also edited the proposed text."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Simulate-mode guard (TEAM.md Phase 4)
# ---------------------------------------------------------------------------

def source_mode() -> str:
    return "simulated" if os.getenv("REGULATION_SOURCE", "live").strip().lower() == "simulated" else "live"


def demo_policy_dir() -> Path | None:
    raw = os.getenv("REGULATION_DEMO_POLICY_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def demo_guard_problems() -> list[str]:
    """Why simulated data may NOT be written here (empty list = safe):
    a demo DB copy, a demo Qdrant collection, a demo text folder."""
    settings = get_settings()
    problems = []
    if Path(settings.sqlite_path).resolve() in _REAL_DBS:
        problems.append("SQLITE_PATH points to the real database; use a demo copy.")
    collection = settings.qdrant_collection or ""
    if collection == SHARED_COLLECTION or "demo" not in collection.lower():
        problems.append("QDRANT_COLLECTION must be a demo collection (name containing 'demo'), not the shared one.")
    folder = demo_policy_dir()
    if folder is None:
        problems.append("REGULATION_DEMO_POLICY_DIR is not set.")
    elif folder == TRACKED_POLICY_DIR.resolve() or TRACKED_POLICY_DIR.resolve() in folder.parents \
            or folder in TRACKED_POLICY_DIR.resolve().parents:
        problems.append("REGULATION_DEMO_POLICY_DIR must be outside policy_texts/.")
    return problems


def require_demo_environment() -> None:
    problems = demo_guard_problems()
    if problems:
        raise RegulationGuardError("Simulate mode refused: " + " ".join(problems))


def text_dir(simulated: bool) -> Path:
    """Where law/policy text files are written: the tracked policy_texts/
    for real changes, the demo folder for simulated ones."""
    if simulated:
        require_demo_environment()
        return demo_policy_dir()  # type: ignore[return-value]
    return TRACKED_POLICY_DIR


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

def ensure_table(conn: sqlite3.Connection) -> None:
    """New additive table (TEAM.md Phase 4). The partial unique index makes
    duplicate protection hold even when two checks run at once."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS regulation_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            label TEXT,
            text TEXT,
            text_hash TEXT,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT,
            fetched_at TEXT,
            change_key TEXT,
            proposal_id TEXT,
            created_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_regulation_pending_change "
        "ON regulation_versions(change_key) WHERE status = 'pending'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_regulation_ref ON regulation_versions(kind, source, ref_id)"
    )


def add_version(
    conn: sqlite3.Connection,
    *,
    kind: str,
    ref_id: str,
    text: str | None,
    status: str,
    source: str,
    label: str | None = None,
    text_hash: str | None = None,
    source_url: str | None = None,
    fetched_at: str | None = None,
    change_key: str | None = None,
    proposal_id: str | None = None,
    decided_by: str | None = None,
    decided_at: str | None = None,
) -> int:
    ensure_table(conn)
    version_no = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM regulation_versions WHERE kind = ? AND source = ? AND ref_id = ?",
        (kind, source, ref_id),
    ).fetchone()[0]
    cursor = conn.execute(
        """
        INSERT INTO regulation_versions (
            kind, ref_id, version_no, label, text, text_hash, status, source, source_url,
            fetched_at, change_key, proposal_id, created_at, decided_by, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind, ref_id, version_no, label, text, text_hash, status, source, source_url,
            fetched_at, change_key, proposal_id, now_iso(), decided_by, decided_at,
        ),
    )
    return int(cursor.lastrowid)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    ensure_table(conn)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def current_articles(conn: sqlite3.Connection, source: str) -> dict[str, dict]:
    """ref_id ('art:98') -> the current version of each article."""
    return {
        r["ref_id"]: r
        for r in _rows(
            conn,
            "SELECT * FROM regulation_versions WHERE kind = 'article' AND source = ? AND status = 'current'",
            (source,),
        )
    }


def versions_for_change(conn: sqlite3.Connection, change_key: str) -> list[dict]:
    return _rows(
        conn,
        "SELECT * FROM regulation_versions WHERE kind = 'article' AND change_key = ? ORDER BY version_id",
        (change_key,),
    )


def pending_for_article(conn: sqlite3.Connection, source: str, ref_id: str) -> dict | None:
    rows = _rows(
        conn,
        "SELECT * FROM regulation_versions WHERE kind = 'article' AND source = ? AND ref_id = ? AND status = 'pending'",
        (source, ref_id),
    )
    return rows[0] if rows else None


def list_articles(conn: sqlite3.Connection, source: str) -> list[dict]:
    """Current version per article, with its version count and any
    pending change."""
    rows = _rows(
        conn,
        """
        SELECT v.*,
               (SELECT COUNT(*) FROM regulation_versions h
                 WHERE h.kind = 'article' AND h.source = v.source AND h.ref_id = v.ref_id) AS versions,
               (SELECT p.proposal_id FROM regulation_versions p
                 WHERE p.kind = 'article' AND p.source = v.source AND p.ref_id = v.ref_id
                   AND p.status = 'pending' LIMIT 1) AS pending_proposal_id
        FROM regulation_versions v
        WHERE v.kind = 'article' AND v.source = ? AND v.status IN ('current', 'repealed')
          AND v.version_id = (SELECT MAX(x.version_id) FROM regulation_versions x
                               WHERE x.kind = 'article' AND x.source = v.source AND x.ref_id = v.ref_id
                                 AND x.status IN ('current', 'repealed'))
        """,
        (source,),
    )
    def order(row: dict) -> tuple[int, str]:
        key = str(row["ref_id"]).split(":", 1)[-1]
        digits = "".join(ch for ch in key if ch.isdigit())
        return (int(digits or 0), key)

    return sorted(rows, key=order)


def history(conn: sqlite3.Connection, kind: str, ref_id: str, source: str | None = None) -> list[dict]:
    if source:
        return _rows(
            conn,
            "SELECT * FROM regulation_versions WHERE kind = ? AND ref_id = ? AND source = ? ORDER BY version_id DESC",
            (kind, ref_id, source),
        )
    return _rows(
        conn,
        "SELECT * FROM regulation_versions WHERE kind = ? AND ref_id = ? ORDER BY version_id DESC",
        (kind, ref_id),
    )


def announcements(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _rows(
        conn,
        "SELECT * FROM regulation_versions WHERE kind = 'announcement' ORDER BY fetched_at DESC, version_id DESC LIMIT ?",
        (limit,),
    )


def announcement_known(conn: sqlite3.Connection, ref_id: str) -> bool:
    return bool(_rows(conn, "SELECT 1 FROM regulation_versions WHERE kind = 'announcement' AND ref_id = ?", (ref_id,)))


# ---------------------------------------------------------------------------
# Check runs (audit_log; no extra table)
# ---------------------------------------------------------------------------

def log_check(conn: sqlite3.Connection, result: dict, actor: str) -> None:
    write_audit(
        conn,
        actor=actor,
        event_type="regulation_check",
        employee_id=None,
        details=json.dumps(result, ensure_ascii=False),
    )


def last_check(conn: sqlite3.Connection, mode: str | None = None) -> dict | None:
    """The latest check, or the latest one run in `mode` (live/simulated)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, actor, details FROM audit_log WHERE event_type = 'regulation_check' "
        "ORDER BY log_id DESC LIMIT 200"
    ).fetchall()
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except ValueError:
            details = {}
        if mode is None or details.get("mode") == mode:
            return {"at": row["timestamp"], "actor": row["actor"], **details}
    return None


# ---------------------------------------------------------------------------
# Applying a decision (called from approvals._sync_regulation_update)
# ---------------------------------------------------------------------------

def law_row(conn: sqlite3.Connection, law_id: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM saudi_labor_law WHERE id = ?", (law_id,)).fetchone()
    return dict(row) if row else None


def policy_row(conn: sqlite3.Connection, policy_id: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM company_policies WHERE id = ?", (policy_id,)).fetchone()
    return dict(row) if row else None


def _next_law_id(conn: sqlite3.Connection) -> str:
    numbers = [
        int(r[0][3:]) for r in conn.execute("SELECT id FROM saudi_labor_law WHERE id LIKE 'LAW%'")
        if r[0][3:].isdigit()
    ]
    return f"LAW{(max(numbers) if numbers else 0) + 1:03d}"


def _law_json(row: dict) -> str:
    return json.dumps({k: row.get(k) for k in LAW_TEXT_FIELDS}, ensure_ascii=False)


def _mark_article(conn: sqlite3.Connection, proposal_id: str, status: str, decided_by: str | None, decided_at: str) -> None:
    conn.execute(
        "UPDATE regulation_versions SET status = ?, decided_by = ?, decided_at = ? "
        "WHERE kind = 'article' AND proposal_id = ? AND status = 'pending'",
        (status, decided_by, decided_at, proposal_id),
    )


def apply_regulation_decision(
    conn: sqlite3.Connection,
    proposal: dict,
    *,
    status: str,
    decided_at: str,
    decided_by: str | None,
) -> dict:
    """Record the decision; on approval apply the (possibly edited) texts.
    Raises FourEyesError / RegulationGuardError — the caller's transaction
    then rolls back, so nothing is half-applied."""
    ensure_table(conn)
    proposal_id = proposal["proposal_id"]
    payload = proposal.get("payload_json")
    if not isinstance(payload, dict):
        payload = json.loads(payload or "{}")
    source = payload.get("source") or "boe_live"
    if payload.get("simulated"):
        require_demo_environment()

    if status != "approved":
        outcome = "sent_back" if payload.pop("pending_outcome", None) == "sent_back" else "rejected"
        _mark_article(conn, proposal_id, outcome, decided_by, decided_at)
        payload["final"] = {"outcome": outcome, "decided_by": decided_by, "decided_at": decided_at}
        write_audit(
            conn, actor=decided_by or "system", event_type=f"regulation_update_{outcome}",
            employee_id=None, details=proposal_id,
        )
        return payload

    if decided_by and decided_by in {e.get("by") for e in payload.get("edits") or []}:
        raise FourEyesError("You edited the proposed text, so another HR manager or admin must approve it.")

    applied_law, applied_policies, version_ids = [], [], []
    for item in payload.get("law_rows") or []:
        proposed = item.get("proposed") or {}
        if item.get("new_row"):
            new_id = _next_law_id(conn)
            row = {**{k: proposed.get(k) for k in LAW_TEXT_FIELDS}, "id": new_id}
            row["article"] = row.get("article") or str(payload.get("article_no"))
            conn.execute(
                "INSERT INTO saudi_labor_law (id, category, article, title, rule, conditions, exceptions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, row["category"], row["article"], row["title"], row["rule"], row["conditions"], row["exceptions"]),
            )
            version_ids.append(add_version(
                conn, kind="law_row", ref_id=new_id, text=_law_json(row), status="current", source=source,
                label=row.get("title"), proposal_id=proposal_id, decided_by=decided_by, decided_at=decided_at,
            ))
            item["law_id"] = new_id
            applied_law.append({"law_id": new_id, "old": None, "new": {k: row[k] for k in LAW_TEXT_FIELDS}})
            continue
        law_id = item.get("law_id")
        old = law_row(conn, law_id) if law_id else None
        if not old:
            continue
        new = {**{k: old.get(k) for k in LAW_TEXT_FIELDS}, **{k: v for k, v in proposed.items() if k in LAW_TEXT_FIELDS and v}}
        if all(new[k] == old.get(k) for k in LAW_TEXT_FIELDS):
            continue
        version_ids.append(add_version(
            conn, kind="law_row", ref_id=law_id, text=_law_json(old), status="superseded", source=source,
            label=old.get("title"), proposal_id=proposal_id, decided_by=decided_by, decided_at=decided_at,
        ))
        conn.execute(
            "UPDATE saudi_labor_law SET category = ?, article = ?, title = ?, rule = ?, conditions = ?, exceptions = ? WHERE id = ?",
            (new["category"], new["article"], new["title"], new["rule"], new["conditions"], new["exceptions"], law_id),
        )
        version_ids.append(add_version(
            conn, kind="law_row", ref_id=law_id, text=_law_json(new), status="current", source=source,
            label=new.get("title"), proposal_id=proposal_id, decided_by=decided_by, decided_at=decided_at,
        ))
        applied_law.append({"law_id": law_id, "old": {k: old.get(k) for k in LAW_TEXT_FIELDS}, "new": new})

    for item in payload.get("policies") or []:
        policy_id = item.get("policy_id")
        old = policy_row(conn, policy_id) if policy_id else None
        new_rule = (item.get("proposed_rule") or "").strip()
        if not old or not new_rule or new_rule == (old.get("rule") or "").strip():
            continue
        version_ids.append(add_version(
            conn, kind="policy", ref_id=policy_id, text=old.get("rule"), status="superseded", source=source,
            label=old.get("policy_name"), proposal_id=proposal_id, decided_by=decided_by, decided_at=decided_at,
        ))
        conn.execute("UPDATE company_policies SET rule = ? WHERE id = ?", (new_rule, policy_id))
        version_ids.append(add_version(
            conn, kind="policy", ref_id=policy_id, text=new_rule, status="current", source=source,
            label=old.get("policy_name"), proposal_id=proposal_id, decided_by=decided_by, decided_at=decided_at,
        ))
        applied_policies.append({"policy_id": policy_id, "old_rule": old.get("rule"), "new_rule": new_rule})

    ref_id = payload.get("ref_id")
    conn.execute(
        "UPDATE regulation_versions SET status = 'superseded' "
        "WHERE kind = 'article' AND source = ? AND ref_id = ? AND status = 'current'",
        (source, ref_id),
    )
    _mark_article(
        conn, proposal_id, "repealed" if payload.get("change_kind") == "removed" else "current",
        decided_by, decided_at,
    )

    payload["final"] = {
        "outcome": "approved",
        "decided_by": decided_by,
        "decided_at": decided_at,
        "applied_law_rows": applied_law,
        "applied_policies": applied_policies,
        "version_ids": version_ids,
        "reindex": {
            "status": "pending" if (applied_law or applied_policies) else "not_needed",
            "law_ids": [a["law_id"] for a in applied_law],
            "policy_ids": [a["policy_id"] for a in applied_policies],
        },
    }
    write_audit(
        conn,
        actor=decided_by or "system",
        event_type="regulation_update_applied",
        employee_id=None,
        details=json.dumps(
            {
                "proposal_id": proposal_id,
                "article": payload.get("article_no"),
                "law_rows": [a["law_id"] for a in applied_law],
                "policies": [a["policy_id"] for a in applied_policies],
                "simulated": bool(payload.get("simulated")),
            },
            ensure_ascii=False,
        ),
    )
    return payload


def save_payload(conn: sqlite3.Connection, proposal_id: str, payload: dict) -> None:
    conn.execute(
        "UPDATE proposed_actions SET payload_json = ? WHERE proposal_id = ?",
        (json.dumps(payload, ensure_ascii=False), proposal_id),
    )
