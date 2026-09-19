"""
Career Development database helpers.

Tracks employee skill development progress.
Additive only - does not modify existing HR or Experience Gap tables.
"""

from __future__ import annotations

import sqlite3


def ensure_career_progress_tables(
    conn: sqlite3.Connection,
) -> None:
    """
    Create employee skill progress tracking table.
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_skill_progress (
            progress_id INTEGER PRIMARY KEY AUTOINCREMENT,

            employee_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,

            current_level INTEGER NOT NULL DEFAULT 0,
            target_level INTEGER NOT NULL,

            deadline TEXT,

            status TEXT NOT NULL DEFAULT 'RECOMMENDED',

            last_updated TEXT,

            FOREIGN KEY(employee_id)
                REFERENCES employees(employee_id),

            FOREIGN KEY(skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.commit()


def create_skill_progress(
    conn: sqlite3.Connection,
    employee_id: str,
    skill_id: str,
    target_level: int,
    current_level: int = 0,
    deadline: str | None = None,
) -> dict:
    """
    Create a skill development goal.
    """

    cursor = conn.execute(
        """
        INSERT INTO employee_skill_progress
        (
            employee_id,
            skill_id,
            current_level,
            target_level,
            deadline,
            status
        )

        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            employee_id,
            skill_id,
            current_level,
            target_level,
            deadline,
            "RECOMMENDED",
        ),
    )

    conn.commit()

    return {
        "progress_id": cursor.lastrowid,
        "employee_id": employee_id,
        "skill_id": skill_id,
        "current_level": current_level,
        "target_level": target_level,
        "deadline": deadline,
        "status": "RECOMMENDED",
    }


def update_skill_progress(
    conn: sqlite3.Connection,
    progress_id: int,
    current_level: int,
) -> dict | None:
    """
    Update employee skill progress level.
    """

    row = conn.execute(
        """
        SELECT target_level
        FROM employee_skill_progress
        WHERE progress_id = ?
        """,
        (progress_id,),
    ).fetchone()

    if row is None:
        return None

    target_level = int(row["target_level"])

    if current_level >= target_level:
        status = "COMPLETED"

    elif current_level > 0:
        status = "IN_PROGRESS"

    else:
        status = "RECOMMENDED"


    conn.execute(
        """
        UPDATE employee_skill_progress

        SET
            current_level = ?,
            status = ?,
            last_updated = CURRENT_TIMESTAMP

        WHERE progress_id = ?
        """,
        (
            current_level,
            status,
            progress_id,
        ),
    )

    conn.commit()


    return {
        "progress_id": progress_id,
        "current_level": current_level,
        "target_level": target_level,
        "status": status,
    }


def list_employee_skill_progress(
    conn: sqlite3.Connection,
    employee_id: str,
) -> list[dict]:
    """
    Return employee development progress.
    """

    rows = conn.execute(
        """
        SELECT
            p.progress_id,
            p.employee_id,
            p.skill_id,
            s.skill_name,
            s.category,

            p.current_level,
            p.target_level,

            p.deadline,
            p.status,
            p.last_updated

        FROM employee_skill_progress p

        JOIN skills s
            ON s.skill_id = p.skill_id

        WHERE p.employee_id = ?

        ORDER BY p.progress_id DESC
        """,
        (employee_id,),
    ).fetchall()


    results = []

    for row in rows:
        item = dict(row)

        current = int(
            item["current_level"] or 0
        )

        target = int(
            item["target_level"] or 0
        )


        if current >= target:
            item["status"] = "COMPLETED"

        elif current > 0:
            item["status"] = "IN_PROGRESS"

        else:
            item["status"] = "RECOMMENDED"


        item["progress_percentage"] = (
            round((current / target) * 100)
            if target
            else 0
        )


        results.append(item)


    return results