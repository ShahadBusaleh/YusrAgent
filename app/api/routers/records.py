"""Records archive (Phase 4): read-only access to every request.

HR specialists, HR managers and admins only; everyone else gets 403.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, require_role, roles_at_least
from app.db import records as records_db
from app.db.connection import get_db

router = APIRouter(prefix="/records", tags=["records"])

_STAFF = require_role(*roles_at_least("hr_specialist"))


def _filters(
    user: CurrentUser,
    type: str | None,
    status_: str | None,
    date_from: date | None,
    date_to: date | None,
    department_id: str | None,
    q: str | None,
    mine: bool,
) -> dict:
    if status_ and status_ not in records_db.STATUS_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of: {', '.join(records_db.STATUS_FILTERS)}",
        )
    return {
        "types": [t.strip() for t in (type or "").split(",") if t.strip()] or None,
        "status": status_,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "department_id": department_id or None,
        "q": q or None,
        "requested_by": user.employee_id if mine else None,
    }


@router.get("")
def list_records(
    type: str | None = Query(default=None, description="Comma-separated action types"),
    status_: str | None = Query(default=None, alias="status", description="pending | approved | rejected"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    department_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Employee name or ID, or request ID"),
    mine: bool = Query(default=False, description="Only requests I submitted"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=records_db.PAGE_SIZE, ge=1, le=100),
    user: CurrentUser = Depends(_STAFF),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    filters = _filters(user, type, status_, date_from, date_to, department_id, q, mine)
    return records_db.list_records(conn, page=page, page_size=page_size, **filters)


@router.get("/facets")
def get_facets(
    user: CurrentUser = Depends(_STAFF),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Request types present and all departments, for the filter bar."""
    return records_db.list_facets(conn)


@router.get("/export")
def export_records(
    type: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    department_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mine: bool = Query(default=False),
    user: CurrentUser = Depends(_STAFF),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """All records matching the filters (no paging), IBANs masked. The UI
    renders the Excel and PDF report from this."""
    filters = _filters(user, type, status_, date_from, date_to, department_id, q, mine)
    items = records_db.export_records(conn, **filters)
    return {"items": items, "total": len(items)}


@router.get("/{proposal_id}")
def get_record(
    proposal_id: str,
    user: CurrentUser = Depends(_STAFF),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    record = records_db.get_record(conn, proposal_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return record
