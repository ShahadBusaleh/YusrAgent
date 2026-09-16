"""Database helpers for Experience Gap Insight."""

from __future__ import annotations

import sqlite3


def ensure_experience_gap_tables(
    conn: sqlite3.Connection,
) -> None:
    """Create only the three additive Experience Gap tables."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            category TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS department_requirements (
            department_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            minimum_headcount INTEGER NOT NULL,
            is_critical INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (department_id, skill_id),
            FOREIGN KEY (department_id)
                REFERENCES departments(department_id),
            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_job_titles (
            skill_id TEXT NOT NULL,
            job_title TEXT NOT NULL,
            PRIMARY KEY (skill_id, job_title),
            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.commit()


def _candidate_job_titles(
    conn: sqlite3.Connection,
    department_id: str,
    skill_id: str,
    limit: int = 2,
) -> list[str]:
    """
    Job titles already present in the department that do not yet
    cover the given skill, ranked by how many employees hold that
    title (a bigger existing pool is a more realistic upskilling
    target). Used only to build a suggestion, not to change status.
    """

    rows = conn.execute(
        """
        SELECT e.job_title, COUNT(*) AS headcount
        FROM employees e
        WHERE e.department_id = ?
          AND e.job_title NOT IN (
              SELECT job_title FROM skill_job_titles WHERE skill_id = ?
          )
        GROUP BY e.job_title
        ORDER BY headcount DESC, e.job_title ASC
        LIMIT ?
        """,
        (department_id, skill_id, limit),
    ).fetchall()

    return [row["job_title"] for row in rows]


def _build_recommendation(
    item: dict,
    candidate_titles: list[str],
) -> str | None:
    """
    Deterministic, template-based suggestion for closing a gap.

    Not LLM-generated: the inputs are just headcount numbers and
    job titles already in hand, so a template avoids adding an LLM
    dependency to what is otherwise read-only SQL analytics.
    """

    if item["status"] == "OK":
        return None

    gap = max(item["required_headcount"] - item["current_headcount"], 0)

    if item["status"] == "MISSING":
        lead = (
            f"No employees in this department currently cover "
            f"{item['skill_name']}."
        )
    else:
        lead = (
            f"Only {item['current_headcount']} of the required "
            f"{item['required_headcount']} employees cover "
            f"{item['skill_name']}."
        )

    if candidate_titles:
        upskill = (
            f" Consider upskilling from an existing role such as "
            f"{' or '.join(candidate_titles)}."
        )
    else:
        upskill = ""

    hire_noun = "hire" if gap == 1 else "hires"
    hire = (
        f" If upskilling isn't feasible, recommend hiring {gap} "
        f"additional {item['skill_name']} {hire_noun}."
    )

    return (lead + upskill + hire).strip()


def get_department_experience_gap(
    conn: sqlite3.Connection,
    department_id: str,
) -> list[dict]:
    """
    Return required skills and current headcount per department.

    No per-employee proficiency score is used. An employee counts
    toward a skill's current_headcount when their job_title is one
    of the titles mapped to that skill in skill_job_titles.

    Status:
        MISSING -> current_headcount == 0
        LOW     -> current_headcount < required_headcount
        OK      -> current_headcount >= required_headcount

    Each MISSING/LOW item also carries a "recommendation" string
    (hire vs. upskill-from-role suggestion). OK items get None.
    """

    rows = conn.execute(
        """
        SELECT
            dr.department_id,
            dr.skill_id,
            s.skill_name,
            s.category,
            dr.minimum_headcount AS required_headcount,
            dr.is_critical,
            COUNT(DISTINCT e.employee_id) AS current_headcount
        FROM department_requirements dr
        JOIN skills s
            ON s.skill_id = dr.skill_id
        LEFT JOIN skill_job_titles sjt
            ON sjt.skill_id = dr.skill_id
        LEFT JOIN employees e
            ON e.department_id = dr.department_id
            AND e.job_title = sjt.job_title
        WHERE dr.department_id = ?
        GROUP BY
            dr.department_id,
            dr.skill_id,
            s.skill_name,
            s.category,
            dr.minimum_headcount,
            dr.is_critical
        """,
        (department_id,),
    ).fetchall()

    results = []

    for row in rows:
        item = dict(row)

        current = int(item["current_headcount"] or 0)
        required = int(item["required_headcount"] or 0)

        if current == 0:
            status = "MISSING"
        elif current < required:
            status = "LOW"
        else:
            status = "OK"

        item["current_headcount"] = current
        item["required_headcount"] = required
        item["status"] = status

        if status == "OK":
            item["recommendation"] = None
        else:
            candidates = _candidate_job_titles(
                conn,
                department_id,
                item["skill_id"],
            )
            item["recommendation"] = _build_recommendation(
                item,
                candidates,
            )

        results.append(item)

    status_order = {
        "MISSING": 0,
        "LOW": 1,
        "OK": 2,
    }

    results.sort(
        key=lambda item: (
            -int(item["is_critical"]),
            status_order[item["status"]],
            item["skill_name"],
        )
    )

    return results
