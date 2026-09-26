"""Regulation update agent (Phase 4): check, review, decide.

hr_manager and admin only. A regulation_update is decided here rather than
through /approvals so that "send back" is distinct from "reject", the
four-eyes rule (the editor of the proposed text cannot approve it) returns a
clean 403, and the changed texts are re-indexed right after the commit.

The check runs on API startup (skipped if one ran in the last 20 hours) and
daily, the same pattern as the separation check in onboarding.py; set
REGULATION_AUTO_CHECK=0 to turn the automatic check off.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.agents import regulation_agent
from app.api.deps import CurrentUser, require_role, roles_at_least
from app.db import records as records_db
from app.db import regulations as reg_db
from app.db.approvals import decide_approval
from app.db.audit import write_audit
from app.db.connection import get_connection, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/regulations", tags=["regulations"])
_MANAGERS = require_role(*roles_at_least("hr_manager"))


def _current_source() -> str:
    return "simulated" if reg_db.source_mode() == "simulated" else "boe_live"


def _load_request(conn: sqlite3.Connection, proposal_id: str) -> tuple[dict, dict]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM proposed_actions WHERE proposal_id = ? AND action_type = 'regulation_update'",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Regulation request not found")
    proposal = dict(row)
    return proposal, json.loads(proposal.get("payload_json") or "{}")


@router.get("/status")
def get_status(user: CurrentUser = Depends(_MANAGERS), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    reg_db.ensure_table(conn)
    mode = reg_db.source_mode()
    return {
        "mode": mode,
        "simulated": mode == "simulated",
        "source": _current_source(),
        "source_url": regulation_agent.BOE_URL if mode == "live" else None,
        "demo_problems": reg_db.demo_guard_problems() if mode == "simulated" else [],
        "last_check": reg_db.last_check(conn, mode),
        "auto_check": os.getenv("REGULATION_AUTO_CHECK", "1") != "0",
    }


@router.post("/check")
def check_now(user: CurrentUser = Depends(_MANAGERS)) -> dict:
    """Run one check now; returns what was checked, found and created."""
    result = regulation_agent.run_check(actor=user.username)
    if result.get("status") == "busy":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
    return result


@router.get("/articles")
def list_articles(
    source: str | None = Query(default=None),
    user: CurrentUser = Depends(_MANAGERS),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    source = source or _current_source()
    return {"source": source, "simulated": source == "simulated", "items": reg_db.list_articles(conn, source)}


@router.get("/history")
def get_history(
    kind: Literal["article", "law_row", "policy"],
    ref_id: str,
    source: str | None = Query(default=None),
    user: CurrentUser = Depends(_MANAGERS),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"items": reg_db.history(conn, kind, ref_id, source)}


@router.get("/alerts")
def get_alerts(user: CurrentUser = Depends(_MANAGERS), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"items": reg_db.announcements(conn)}


@router.get("/pending")
def list_pending(user: CurrentUser = Depends(_MANAGERS), conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    """Pending regulation_update approvals with their proposal. /approvals
    filters `employee_id != <viewer>`, which in SQL never matches the NULL
    requester of a system request, so "Waiting on you" reads them here."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ap.*, pa.payload_json AS proposal_payload, pa.status AS proposal_status,
               pa.created_at AS proposal_created_at
        FROM pending_approvals ap
        JOIN proposed_actions pa ON pa.proposal_id = ap.proposal_id
        WHERE pa.action_type = 'regulation_update' AND ap.status = 'pending'
        ORDER BY ap.created_at DESC
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop("proposal_payload") or "{}")
        item["proposal"] = {
            "proposal_id": item["proposal_id"], "action_type": "regulation_update", "payload_json": payload,
            "status": item.pop("proposal_status"), "created_at": item.pop("proposal_created_at"),
        }
        out.append(item)
    return out


@router.get("/requests/{proposal_id}")
def get_request(
    proposal_id: str, user: CurrentUser = Depends(_MANAGERS), conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    _load_request(conn, proposal_id)
    record = records_db.get_record(conn, proposal_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Regulation request not found")
    return record


class ProposedTextEdit(BaseModel):
    target: Literal["policy", "law_row"]
    id: str = Field(min_length=1)
    field: Literal["rule", "title", "conditions", "exceptions"] = "rule"
    text: str = Field(min_length=1, max_length=4000)


@router.put("/requests/{proposal_id}/proposed-text")
def edit_proposed_text(
    proposal_id: str,
    body: ProposedTextEdit,
    user: CurrentUser = Depends(_MANAGERS),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Edit the proposed policy / law-row text while the request is pending.
    The editor is recorded and can no longer approve this request."""
    proposal, payload = _load_request(conn, proposal_id)
    if proposal["status"] != "pending_approval":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This request was already decided.")
    if payload.get("simulated") and reg_db.demo_guard_problems():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Simulate mode refused: " + " ".join(reg_db.demo_guard_problems()))
    text = body.text.strip()
    if body.target == "policy":
        item = next((p for p in payload.get("policies") or [] if p.get("policy_id") == body.id), None)
        if item is None or body.field != "rule":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not in this request")
        item["proposed_rule"] = text
    else:
        item = next((r for r in payload.get("law_rows") or [] if (r.get("law_id") or "NEW") == body.id), None)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Law row not in this request")
        item["proposed"] = {**(item.get("proposed") or {}), body.field: text}
        item["changed"] = True
    payload.setdefault("edits", []).append(
        {"by": user.employee_id, "at": reg_db.now_iso(), "target": body.target, "id": body.id, "field": body.field, "text": text}
    )
    reg_db.save_payload(conn, proposal_id, payload)
    write_audit(conn, actor=user.username, event_type="regulation_update_edited", employee_id=None,
                details=json.dumps({"proposal_id": proposal_id, "target": body.target, "id": body.id}))
    return {"proposal_id": proposal_id, "edits": len(payload["edits"])}


class RegulationDecision(BaseModel):
    decision: Literal["approve", "send_back", "reject"]
    decision_note: str | None = Field(default=None, max_length=2000)


@router.post("/requests/{proposal_id}/decide")
def decide_request(
    proposal_id: str, body: RegulationDecision, user: CurrentUser = Depends(_MANAGERS)
) -> dict:
    conn = get_connection()
    try:
        proposal, payload = _load_request(conn, proposal_id)
        approval = conn.execute(
            "SELECT approval_id FROM pending_approvals WHERE proposal_id = ? AND status = 'pending'", (proposal_id,)
        ).fetchone()
        if proposal["status"] != "pending_approval" or approval is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This request was already decided.")
        if body.decision == "approve" and user.employee_id in {e.get("by") for e in payload.get("edits") or []}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You edited the proposed text, so another HR manager or admin must approve it.",
            )
        if payload.get("simulated"):
            problems = reg_db.demo_guard_problems()
            if problems:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Simulate mode refused: " + " ".join(problems))
        if body.decision == "send_back":
            payload["pending_outcome"] = "sent_back"
            reg_db.save_payload(conn, proposal_id, payload)
        try:
            decide_approval(
                conn, approval["approval_id"],
                decision="approve" if body.decision == "approve" else "reject",
                decided_by=user.employee_id, decision_note=body.decision_note,
            )
        except reg_db.FourEyesError as exc:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except reg_db.RegulationGuardError as exc:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        write_audit(conn, actor=user.username, event_type=f"approval_{body.decision}", employee_id=None,
                    details=approval["approval_id"])
        conn.commit()
        reindex = regulation_agent.reindex_proposal(conn, proposal_id) if body.decision == "approve" else None
        _, payload = _load_request(conn, proposal_id)
        return {"proposal_id": proposal_id, "outcome": (payload.get("final") or {}).get("outcome"), "reindex": reindex}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/reindex")
def retry_reindex(user: CurrentUser = Depends(_MANAGERS)) -> dict:
    conn = get_connection()
    try:
        return {"items": regulation_agent.reindex_pending(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Startup + daily check (same pattern as the separation check)
# ---------------------------------------------------------------------------

_DAILY_RUN_AT = time(3, 0)  # 03:00 server time
_RECENT = timedelta(hours=20)
_daily_started = False
_stop_daily = threading.Event()


def run_scheduled_check(skip_if_recent: bool = False) -> None:
    try:
        if skip_if_recent:
            conn = get_connection()
            try:
                reg_db.ensure_table(conn)
                last = reg_db.last_check(conn, reg_db.source_mode())
                conn.commit()
            finally:
                conn.close()
            if last and last.get("at"):
                if datetime.now(timezone.utc) - datetime.fromisoformat(last["at"]) < _RECENT:
                    conn = get_connection()
                    try:
                        regulation_agent.reindex_pending(conn)
                    finally:
                        conn.close()
                    return
        regulation_agent.run_check(actor="system")
    except Exception:
        logger.exception("Regulation check failed")


def _seconds_until_next_run(now: datetime) -> float:
    target = datetime.combine(now.date(), _DAILY_RUN_AT)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _daily_loop(stop: threading.Event) -> None:
    while not stop.wait(_seconds_until_next_run(datetime.now())):
        run_scheduled_check()


@router.on_event("startup")
def _start_regulation_checks() -> None:
    global _daily_started
    if os.getenv("REGULATION_AUTO_CHECK", "1") == "0" or _daily_started:
        return
    _daily_started = True
    # In the background: a live fetch + analysis must not delay startup.
    threading.Thread(target=run_scheduled_check, kwargs={"skip_if_recent": True},
                     name="regulation-check-startup", daemon=True).start()
    threading.Thread(target=_daily_loop, args=(_stop_daily,), name="regulation-check", daemon=True).start()


@router.on_event("shutdown")
def _stop_regulation_checks() -> None:
    _stop_daily.set()
