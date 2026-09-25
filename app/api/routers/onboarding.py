"""Onboarding & Offboarding helpers (Phase 4) — HR staff only.

- POST /onboarding/parse-cv: read a CV PDF and return new-hire form fields.
  Stores nothing.
- POST /onboarding/proposals/{proposal_id}/cv: attach the CV text to the
  requester's own pending new_hire proposal payload. The CV can't travel in
  the /agent/query text (4000-character limit). The employee_cvs row itself is
  written only by _sync_new_hire after an HR manager approves.
- On API startup and once a day: finalize terminations whose last working
  day has been reached (app/db/separations.py).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from openai import OpenAIError

from app.agents.cv_parser import (
    CVParseError,
    CVParserUnavailableError,
    CVUnreadableError,
    parse_cv,
)
from app.agents.growth_plan import extract_pdf_text
from app.api.deps import CurrentUser, require_role, roles_at_least
from app.db.audit import write_audit
from app.db.connection import get_connection, get_db
from app.db.proposed_actions import get_proposed_action
from app.db.separations import finalize_due_separations

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
logger = logging.getLogger(__name__)

_MAX_CV_BYTES = 10 * 1024 * 1024  # 10 MB — same limit as routers/growth.py

_UNREADABLE_DETAIL = "We couldn't read any text from that PDF. Fill in the fields manually."


def _read_pdf(cv: UploadFile) -> bytes:
    if (cv.content_type or "").lower() != "application/pdf" and not (
        cv.filename or ""
    ).lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload the CV as a PDF file.",
        )
    file_bytes = cv.file.read(_MAX_CV_BYTES + 1)
    if len(file_bytes) > _MAX_CV_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That CV file is too large (10 MB limit).",
        )
    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload the CV as a PDF file.",
        )
    return file_bytes


@router.post("/parse-cv")
def parse_cv_fields(
    cv: UploadFile = File(...),
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
) -> dict:
    """Return full_name, email, mobile, nationality, gender and
    suggested_job_title (None when the CV doesn't state them)."""

    file_bytes = _read_pdf(cv)
    try:
        fields = parse_cv(file_bytes)
    except CVUnreadableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_UNREADABLE_DETAIL,
        ) from exc
    except CVParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The CV could not be read reliably. Please retry or fill in the fields manually.",
        ) from exc
    except (CVParserUnavailableError, OpenAIError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CV reading is temporarily unavailable. Please fill in the fields manually.",
        ) from exc

    return {"filename": cv.filename, "fields": fields}


@router.post("/proposals/{proposal_id}/cv")
def attach_cv(
    proposal_id: str,
    cv: UploadFile = File(...),
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Add the CV text to the requester's own pending new_hire proposal."""

    proposal = get_proposed_action(conn, proposal_id)
    if proposal is None or proposal.get("action_type") != "new_hire":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New-hire request not found.")
    try:
        payload = json.loads(proposal.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    if payload.get("requested_by") != user.employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the requester can attach a CV to this request.",
        )
    if proposal.get("status") != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has already been decided.",
        )

    cv_text = extract_pdf_text(_read_pdf(cv))
    if not cv_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_UNREADABLE_DETAIL,
        )

    payload.update(
        cv_filename=cv.filename,
        cv_text=cv_text,
        cv_uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    conn.execute(
        """
        UPDATE proposed_actions SET payload_json = ?
        WHERE proposal_id = ? AND status = 'pending_approval'
        """,
        (json.dumps(payload), proposal_id),
    )
    write_audit(
        conn,
        actor=user.username,
        event_type="onboarding_cv_attached",
        employee_id=user.employee_id,
        details=proposal_id,
    )
    return {"proposal_id": proposal_id, "filename": cv.filename, "attached": True}


# ---------------------------------------------------------------------------
# Separation check: startup + daily (TEAM.md Phase 4). APIRouter startup
# handlers are registered on the app by include_router, so main.py needs no
# extra line.
# ---------------------------------------------------------------------------

_DAILY_RUN_AT = time(0, 5)  # 00:05 server time, just after the date changes
_daily_started = False


def run_separation_check() -> list[str]:
    conn = get_connection()
    try:
        finalized = finalize_due_separations(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Separation check failed")
        return []
    finally:
        conn.close()
    if finalized:
        logger.info("Finalized separations: %s", ", ".join(finalized))
    return finalized


def _seconds_until_next_run(now: datetime) -> float:
    target = datetime.combine(now.date(), _DAILY_RUN_AT)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _daily_loop(stop: threading.Event) -> None:
    while not stop.wait(_seconds_until_next_run(datetime.now())):
        run_separation_check()


_stop_daily = threading.Event()


@router.on_event("startup")
def _start_separation_checks() -> None:
    global _daily_started
    run_separation_check()
    if not _daily_started:
        _daily_started = True
        threading.Thread(target=_daily_loop, args=(_stop_daily,), name="separation-check", daemon=True).start()


@router.on_event("shutdown")
def _stop_separation_checks() -> None:
    _stop_daily.set()
