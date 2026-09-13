from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_current_user, require_role, roles_at_least
from app.api.schemas import LeaveBalanceOut, LeaveRequestOut
from app.db.connection import get_db
from app.db import leave as leave_db

router = APIRouter(prefix="/leave", tags=["leave"])


@router.get("/balance", response_model=LeaveBalanceOut)
def own_balance(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    balance = leave_db.get_leave_balance(conn, user.employee_id)
    if balance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No leave balance")
    return balance


@router.get("/balance/{employee_id}", response_model=LeaveBalanceOut)
def employee_balance(
    employee_id: str,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    balance = leave_db.get_leave_balance(conn, employee_id)
    if balance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No leave balance")
    return balance


@router.get("/requests", response_model=list[LeaveRequestOut])
def own_requests(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return leave_db.list_leave_requests(conn, user.employee_id)


@router.get("/requests/{employee_id}", response_model=list[LeaveRequestOut])
def employee_requests(
    employee_id: str,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return leave_db.list_leave_requests(conn, employee_id)


# Leave requests are created only through the chat agent (Ask Yusor ->
# HRAgent's proposed_action -> ManagerAgent's approval submission), which
# routes through governance and attaches a suggested cover employee.
# There is deliberately no direct-write endpoint here.
