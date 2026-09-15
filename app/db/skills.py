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
        CREATE TABLE IF NOT EXISTS employee_skills (
            employee_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            current_level INTEGER NOT NULL,
            assessed_by TEXT,
            assessed_date TEXT,
            PRIMARY KEY (employee_id, skill_id),
            FOREIGN KEY (employee_id)
                REFERENCES employees(employee_id),
            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.commit()


def get_department_experience_gap(
    conn: sqlite3.Connection,
    department_id: str,
) -> list[dict]:
    """
    Return required skills and current qualified headcount.

    A skill is qualified when the employee's current_level is
    greater than or equal to the required skill proficiency.

    Status:
        MISSING -> current_headcount == 0
        LOW     -> current_headcount < required_headcount
        OK      -> current_headcount >= required_headcount
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
            COUNT(
                CASE
                    WHEN es.current_level >= 3
                    THEN 1
                END
            ) AS current_headcount
        FROM department_requirements dr
        JOIN skills s
            ON s.skill_id = dr.skill_id
        LEFT JOIN employees e
            ON e.department_id = dr.department_id
        LEFT JOIN employee_skills es
            ON es.employee_id = e.employee_id
            AND es.skill_id = dr.skill_id
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