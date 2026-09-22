"""Attendance facts (attendance_leave_monthly): reads, plus the one write
path — crediting approved leave days onto the employee's monthly row.

Baseline assumption (per business rule): an employee is fully present and
on time for every working day of the month unless something on record says
otherwise — an approved leave (credited here) or a reported lateness. There
is no lateness-reporting path yet, so late_arrivals/early_departures always
default to 0 until one exists.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import date

from app.db.connection import next_id, row_to_dict

_LEAVE_TYPE_COLUMNS = {
    "annual": "annual_leave_days",
    "sick": "sick_leave_days",
    "unpaid": "unpaid_leave_days",
    # No dedicated column for emergency leave — folded into "other".
    "emergency": "other_leave_days",
}
_DEFAULT_LEAVE_COLUMN = "other_leave_days"

_WEEKEND = {4, 5}  # Friday, Saturday (KSA work week is Sunday-Thursday)


def get_latest_attendance(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM attendance_leave_monthly
        WHERE employee_id = ?
        ORDER BY attendance_month DESC
        LIMIT 1
        """,
        (employee_id,),
    ).fetchone()
    return row_to_dict(row)


def get_attendance_for_period(
    conn: sqlite3.Connection, employee_id: str, period: str
) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM attendance_leave_monthly
        WHERE employee_id = ? AND attendance_month = ?
        """,
        (employee_id, period),
    ).fetchone()
    return row_to_dict(row)


def _working_days_for_period(conn: sqlite3.Connection, period: str) -> int:
    """working_days is uniform company-wide per month in this dataset, so
    reuse whatever another employee's row already has for `period`. Falls
    back to a Sun-Thu business-day count when no row exists yet at all."""

    row = conn.execute(
        """
        SELECT working_days FROM attendance_leave_monthly
        WHERE attendance_month = ? AND working_days IS NOT NULL
        LIMIT 1
        """,
        (period,),
    ).fetchone()

    if row and row["working_days"] is not None:
        return int(row["working_days"])

    year, month = (int(part) for part in period.split("-"))
    days_in_month = monthrange(year, month)[1]
    return sum(
        1
        for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() not in _WEEKEND
    )


def add_leave_days(
    conn: sqlite3.Connection,
    employee_id: str,
    period: str,
    leave_type: str,
    days: float,
) -> None:
    """Credit approved leave days onto the employee's attendance_leave_monthly
    row for `period` (YYYY-MM), creating the row (fully-present baseline)
    if this is the first attendance fact recorded for that month. No schema
    change: unrecognized leave types fall into other_leave_days. Matches
    this dataset's existing convention where days_present excludes leave
    days (working_days = days_present + days_absent + total_leave_days)."""

    if not days:
        return

    column = _LEAVE_TYPE_COLUMNS.get(
        str(leave_type or "").strip().lower(), _DEFAULT_LEAVE_COLUMN
    )

    existing = conn.execute(
        "SELECT * FROM attendance_leave_monthly "
        "WHERE employee_id = ? AND attendance_month = ?",
        (employee_id, period),
    ).fetchone()

    if existing:
        updates = [
            f"{column} = COALESCE({column}, 0) + ?",
            "total_leave_days = COALESCE(total_leave_days, 0) + ?",
        ]
        params: list[object] = [days, days]

        working_days = existing["working_days"]
        current_present = existing["days_present"]

        if current_present is not None:
            new_present = max(0.0, current_present - days)
            updates.append("days_present = ?")
            params.append(new_present)

            if working_days:
                updates.append("attendance_rate_pct = ?")
                params.append(round(new_present / working_days * 100, 2))

        params.append(existing["attendance_id"])
        conn.execute(
            f"UPDATE attendance_leave_monthly SET {', '.join(updates)} "
            "WHERE attendance_id = ?",
            params,
        )
        return

    working_days = _working_days_for_period(conn, period)
    present = max(0.0, working_days - days)
    rate = round(present / working_days * 100, 2) if working_days else None

    attendance_id = next_id(
        conn, "attendance_leave_monthly", "attendance_id", "ATT-"
    )
    conn.execute(
        """
        INSERT INTO attendance_leave_monthly (
            attendance_id, employee_id, attendance_month, month_start_date,
            working_days, days_present, days_absent,
            annual_leave_days, sick_leave_days, unpaid_leave_days,
            other_leave_days, total_leave_days,
            late_arrivals, early_departures, attendance_rate_pct
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, 0, 0, ?)
        """,
        (
            attendance_id,
            employee_id,
            period,
            f"{period}-01",
            working_days,
            present,
            days,
            rate,
        ),
    )
    conn.execute(
        f"UPDATE attendance_leave_monthly SET {column} = ? WHERE attendance_id = ?",
        (days, attendance_id),
    )
