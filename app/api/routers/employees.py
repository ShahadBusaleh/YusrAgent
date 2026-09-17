from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, get_current_user, require_role, roles_at_least
from app.api.schemas import EmployeeOut, EmployeeSummary, EmployeeUpdate
from app.db.connection import get_db
from app.db import employees as employees_db
from app.db.audit import write_audit

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("/me", response_model=EmployeeOut)
def get_me(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    employee = employees_db.get_employee(conn, user.employee_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return employee


@router.get("", response_model=list[EmployeeSummary])
def list_employees(
    q: str | None = Query(default=None),
    department_id: str | None = Query(default=None),
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return employees_db.list_employees(conn, q=q, department_id=department_id)


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if user.role == "employee" and employee_id != user.employee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Own record only")
    employee = employees_db.get_employee(conn, employee_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return employee

"""
@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: str,
    body: EmployeeUpdate,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_specialist"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    current = employees_db.get_employee(conn, employee_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    updated = employees_db.update_employee_profile(
        conn, employee_id, body.model_dump(exclude_unset=True)
    )
    write_audit(
        conn,
        actor=user.username,
        event_type="employee_profile_update",
        employee_id=employee_id,
        details=str(body.model_dump(exclude_unset=True)),
    )
    return updated or current
"""

@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: str,
    body: EmployeeUpdate,
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    # Regular employees can only update their own record.
    if user.role == "employee" and employee_id != user.employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Own record only",
        )

    # Only employees and HR staff are allowed.
    if user.role not in {"employee", "hr_specialist", "hr_manager", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient role",
        )

    current = employees_db.get_employee(conn, employee_id)

    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    updated = employees_db.update_employee_profile(
        conn,
        employee_id,
        body.model_dump(exclude_unset=True),
    )

    write_audit(
        conn,
        actor=user.username,
        event_type="employee_profile_update",
        employee_id=employee_id,
        details=str(body.model_dump(exclude_unset=True)),
    )

    return updated or current