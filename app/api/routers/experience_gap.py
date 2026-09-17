from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, require_role, roles_at_least
from app.db.connection import get_db
from app.db.skills import get_department_experience_gap, list_departments


router = APIRouter(
    prefix="/experience-gap",
    tags=["experience-gap"],
)


@router.get("/departments")
def get_departments(
    user: CurrentUser = Depends(
        require_role(*roles_at_least("hr_manager"))
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return all departments for the Team Insights department picker."""

    return {"departments": list_departments(conn)}


@router.get("/{department_id}")
def get_experience_gap(
    department_id: str,
    user: CurrentUser = Depends(
        require_role(*roles_at_least("hr_manager"))
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return read-only skill coverage insights for a department."""

    skills = get_department_experience_gap(
        conn,
        department_id,
    )

    return {
        "department_id": department_id,
        "skills": skills,
    }