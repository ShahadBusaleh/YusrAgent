from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_role, roles_at_least
from app.api.schemas import ApprovalDecision, ApprovalOut
from app.db.connection import get_db
from app.db import approvals as approvals_db
from app.db.audit import write_audit

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return approvals_db.list_pending_approvals(conn, status=status_filter)


@router.post("/{approval_id}/decide", response_model=ApprovalOut)
def decide(
    approval_id: str,
    body: ApprovalDecision,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    updated = approvals_db.decide_approval(
        conn,
        approval_id,
        decision=body.decision,
        decided_by=user.employee_id,
        decision_note=body.decision_note,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    write_audit(
        conn,
        actor=user.username,
        event_type=f"approval_{body.decision}",
        employee_id=updated.get("employee_id"),
        details=approval_id,
    )
    return updated
