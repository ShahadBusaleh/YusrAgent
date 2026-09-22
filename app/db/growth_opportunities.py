"""
Database helpers for the employee-facing Growth Opportunities feature.

Built on top of Experience Gap (app/db/skills.py): an employee is a
"candidate" for a department's MISSING experience title when their
own job title is one of the "closest experience" titles Experience Gap
already ranks for that gap. Candidates may upload a CV to receive an
LLM-generated development plan; gaps with no internal candidate at all
are left to Experience Gap's "hire_recommended" flag instead.
"""

from __future__ import annotations

import sqlite3

from app.db.employees import get_employee
from app.db.skills import get_department_experience_gap


def ensure_growth_plan_table(conn: sqlite3.Connection) -> None:
    """Create the additive candidate_growth_plans table."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_growth_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,

            employee_id TEXT NOT NULL,
            department_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,

            cv_filename TEXT,
            cv_text TEXT,
            plan_text TEXT NOT NULL,

            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (employee_id)
                REFERENCES employees(employee_id),

            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.commit()


def get_latest_growth_plan(
    conn: sqlite3.Connection,
    employee_id: str,
    skill_id: str,
) -> dict | None:
    """Most recent stored plan for this employee/skill, if any."""

    row = conn.execute(
        """
        SELECT plan_id, plan_text, cv_filename, created_at
        FROM candidate_growth_plans
        WHERE employee_id = ? AND skill_id = ?
        ORDER BY plan_id DESC
        LIMIT 1
        """,
        (employee_id, skill_id),
    ).fetchone()

    return dict(row) if row else None


def save_growth_plan(
    conn: sqlite3.Connection,
    employee_id: str,
    department_id: str,
    skill_id: str,
    cv_filename: str | None,
    cv_text: str,
    plan_text: str,
) -> dict:
    """Insert a new plan row. History is kept; the latest row wins on read."""

    ensure_growth_plan_table(conn)

    cursor = conn.execute(
        """
        INSERT INTO candidate_growth_plans (
            employee_id,
            department_id,
            skill_id,
            cv_filename,
            cv_text,
            plan_text
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            employee_id,
            department_id,
            skill_id,
            cv_filename,
            cv_text,
            plan_text,
        ),
    )

    conn.commit()

    return {
        "plan_id": cursor.lastrowid,
        "employee_id": employee_id,
        "skill_id": skill_id,
        "plan_text": plan_text,
    }


def list_opportunities_for_employee(
    conn: sqlite3.Connection,
    employee_id: str,
) -> list[dict]:
    """
    MISSING department gaps this employee is a "closest experience"
    candidate for, each with any previously generated plan attached.

    Reuses the candidate list `get_department_experience_gap` already
    computed per skill (via its internal "_candidate_employees") rather
    than re-running the same closest-experience lookup a second time.
    """

    ensure_growth_plan_table(conn)

    employee = get_employee(conn, employee_id)

    if not employee or not employee.get("department_id"):
        return []

    department_id = employee["department_id"]

    gap_items = [
        item
        for item in get_department_experience_gap(conn, department_id)
        if item["status"] == "MISSING"
    ]

    opportunities = []

    for item in gap_items:
        candidates = item.get("_candidate_employees") or []

        if not any(c["employee_id"] == employee_id for c in candidates):
            continue

        plan = get_latest_growth_plan(conn, employee_id, item["skill_id"])

        opportunities.append(
            {
                "department_id": department_id,
                "skill_id": item["skill_id"],
                "skill_name": item["skill_name"],
                "category": item["category"],
                "status": item["status"],
                "current_headcount": item["current_headcount"],
                "current_job_title": employee.get("job_title"),
                "plan": plan,
            }
        )

    return opportunities
