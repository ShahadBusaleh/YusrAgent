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
) -> list[dict]:
    """
    Job titles already present in the department that do not yet
    cover the given skill, ranked by how many employees hold that
    title (a bigger existing pool is a more realistic upskilling
    target). Used only to build a suggestion, not to change status.

    Returns [{"job_title": ..., "headcount": ...}, ...], capped at
    `limit` rows. The headcount is only summed across these displayed
    titles (not every non-matching title in the department) so the
    "how many people could realistically be trained" figure matches
    the roles actually named in the recommendation.
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

    return [dict(row) for row in rows]


_CATEGORY_TRAINING_HINTS: dict[str, str] = {
    "Analytics": "a data-analysis/BI certification (e.g. SQL, Power BI) or an internal data bootcamp",
    "Commercial": "commercial/contract-management training or a mentorship rotation with the commercial team",
    "Communication": "a communication and presentation-skills workshop",
    "Compliance": "a compliance certification course and shadowing the compliance team",
    "Customer Experience": "customer-experience/service-excellence training",
    "Engineering": "an engineering certification or a hands-on rotation with senior engineers",
    "Finance": "a finance/accounting certification (e.g. cost accounting, CFA foundations)",
    "General": "an internal cross-training rotation",
    "Human Resources": "an HR-practices certification (e.g. CIPD, SHRM) or shadowing the HR team",
    "Leadership": "a leadership/management development program",
    "Legal": "a legal and contracts fundamentals course",
    "Marketing": "a digital-marketing certification or a rotation with the marketing team",
    "Operations": "an operations-management training program",
    "Procurement": "a procurement/supply-chain certification (e.g. CIPS)",
    "Risk": "a risk-management certification course",
    "Safety": "a health & safety certification (e.g. NEBOSH, OSHA)",
    "Sales": "a sales-methodology training program",
    "Security": "a security certification (e.g. CompTIA Security+, CISSP foundations)",
    "Supply Chain": "a supply-chain management certification",
    "Technology": "a technical certification or bootcamp (e.g. cloud, software development)",
}

_DEFAULT_TRAINING_HINT = "a relevant internal or external training course"


def _build_recommendation(
    item: dict,
    candidates: list[dict],
) -> str | None:
    """
    Deterministic, template-based development suggestion for a gap.

    Frames every gap as an internal-growth question — which current
    employees are positioned to develop into this experience title,
    and how — never as a hiring recommendation. Not LLM-generated:
    the inputs are just headcount numbers and job titles already in
    hand, so a template avoids adding an LLM dependency to what is
    otherwise read-only SQL analytics.
    """

    if item["status"] == "OK":
        return None

    skill_name = item["skill_name"]
    training_hint = _CATEGORY_TRAINING_HINTS.get(
        item.get("category"), _DEFAULT_TRAINING_HINT
    )

    if item["status"] == "MISSING":
        lead = (
            f"{skill_name} is an experience gap — no employee in "
            f"this department currently holds a role built around it."
        )
    else:
        lead = (
            f"{skill_name} is under-represented — only "
            f"{item['current_headcount']} of the "
            f"{item['required_headcount']} employees the department "
            f"is targeting currently have it."
        )

    pool_size = sum(c["headcount"] for c in candidates)
    titles = " or ".join(c["job_title"] for c in candidates)

    if candidates:
        develop = (
            f" {pool_size} employees in roles such as {titles} are "
            f"the most natural fit to grow into it — supporting them "
            f"through {training_hint} builds a path to promotion "
            f"into a {skill_name}-focused role."
        )
    else:
        develop = (
            f" Encourage employees across the department to pursue "
            f"{training_hint} to build toward this experience over "
            f"time."
        )

    return (lead + develop).strip()


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


def list_departments(conn: sqlite3.Connection) -> list[dict]:
    """Return all departments as {department_id, department_name}, name-sorted."""

    rows = conn.execute(
        """
        SELECT department_id, department_name
        FROM departments
        ORDER BY department_name
        """
    ).fetchall()

    return [dict(row) for row in rows]
