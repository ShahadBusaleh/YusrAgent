from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.translation import localize_many
from app.api.deps import CurrentUser, get_request_lang, require_role, roles_at_least
from app.db.audit import write_audit
from app.db.connection import get_db
from app.db.growth_opportunities import (
    employees_with_growth_plan,
    get_latest_growth_plan,
)
from app.db.translation_cache import database_path
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


@router.get("/{department_id}/skills/{skill_id}/candidates/{employee_id}/plan")
def get_candidate_growth_plan(
    department_id: str,
    skill_id: str,
    employee_id: str,
    user: CurrentUser = Depends(
        require_role(*roles_at_least("hr_manager"))
    ),
    conn: sqlite3.Connection = Depends(get_db),
    lang: str = Depends(get_request_lang),
) -> dict:
    """Latest growth plan of one internal candidate for one open gap.

    Only employees listed as candidates for this MISSING gap are
    readable, so this cannot be used to browse arbitrary employees'
    plans. The stored CV text is never returned.
    """

    gap = next(
        (
            item
            for item in get_department_experience_gap(conn, department_id)
            if item["skill_id"] == skill_id and item["status"] == "MISSING"
        ),
        None,
    )
    candidates = (gap or {}).get("_candidate_employees") or []
    candidate = next((c for c in candidates if c["employee_id"] == employee_id), None)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This employee is not a candidate for this gap.",
        )

    plan = get_latest_growth_plan(conn, employee_id, skill_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This candidate has not generated a growth plan yet.",
        )

    # Stored in the language it was generated in; translated for display.
    plan_text = localize_many([plan["plan_text"]], lang, database_path(conn))[0]

    write_audit(
        conn,
        actor=user.username,
        event_type="growth_plan_view",
        employee_id=employee_id,
        details=f"department={department_id} skill={skill_id} plan_id={plan['plan_id']}",
    )

    return {
        "employee_id": employee_id,
        "full_name": candidate.get("full_name"),
        "job_title": candidate.get("job_title"),
        "skill_id": skill_id,
        "skill_name": gap.get("skill_name"),
        "plan_text": plan_text,
        "cv_filename": plan.get("cv_filename"),
        "created_at": plan.get("created_at"),
    }
