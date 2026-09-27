from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, require_role, roles_at_least
from app.db.connection import get_db
from app.db.growth_opportunities import employees_with_growth_plan
from app.db.skills import (
    get_department_experience_gap,
    list_departments,
    summarize_candidate_titles,
)


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
    """Return read-only skill coverage insights for a department.

    Each MISSING skill carries `internal_candidates`: the same employees
    who see it on their Growth Opportunities page, so the manager view
    and the employee view always agree on who can grow into a gap.
    """

    skills = get_department_experience_gap(
        conn,
        department_id,
    )

    for item in skills:
        candidates = item.pop("_candidate_employees", None) or []
        if item["status"] != "MISSING":
            continue
        with_plan = employees_with_growth_plan(conn, item["skill_id"])
        employees = [
            {**c, "has_growth_plan": c["employee_id"] in with_plan}
            for c in candidates
        ]
        item["internal_candidates"] = {
            "count": len(employees),
            "titles": summarize_candidate_titles(candidates),
            "plans_started": sum(e["has_growth_plan"] for e in employees),
            "employees": employees,
        }

    return {
        "department_id": department_id,
        "skills": skills,
    }
