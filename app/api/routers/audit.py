from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, require_role
from app.api.schemas import AuditLogOut
from app.db.connection import get_db
from app.db.audit import list_audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def get_audit(
    employee_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    user: CurrentUser = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return list_audit_log(conn, employee_id=employee_id, event_type=event_type)
