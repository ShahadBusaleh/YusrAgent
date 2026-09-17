"""
Backfill placeholder entitlement/remaining figures in `leave_balances`.

IMPORTANT:
- Every row in `leave_balances` was seeded with `annual_entitlement`,
  `annual_remaining`, `sick_entitlement`, `sick_remaining`,
  `emergency_entitlement`, `emergency_used`, and `emergency_remaining`
  left NULL. That breaks any "how many days do I have remaining"
  answer at the data layer, independent of application code.
- This script only fills those NULL fields with placeholder demo
  numbers derived from each employee's existing `hire_date` and the
  already-seeded `*_used` figures. It does not touch `annual_used`,
  `sick_used`, or any other existing column/row, and it does not
  change the schema.
- Idempotent: only ever writes where the target field is still NULL,
  so re-running after a reseed only fills gaps and never overwrites a
  value someone has since set deliberately.

Placeholder entitlement rules (demo data, not legal advice):
- Annual leave: 21 days/year, rising to 30 days/year once the
  employee has completed 5+ years of service (Saudi Labor Law
  Article 109 / LAW037, LAW038).
- Sick leave: 30 full-pay days/year (Saudi Labor Law Article 117 /
  LAW060 headline figure; the reduced-pay and unpaid extensions in
  LAW061/LAW062 are not folded into this single entitlement number).
- Emergency leave: 5 days/year flat allowance. No statutory citation
  for this one — it is a common private-sector benefit, kept as a
  simple placeholder since the source data has no company policy for
  it either.
"""

from __future__ import annotations

from datetime import date

from app.db.connection import get_connection

DATE_FMT_ROWS = "%d-%m-%Y"

ANNUAL_BASE_DAYS = 21
ANNUAL_SENIOR_DAYS = 30
ANNUAL_SENIOR_YEARS = 5

SICK_ENTITLEMENT_DAYS = 30
EMERGENCY_ENTITLEMENT_DAYS = 5

DEFAULT_AS_OF_DATE = "01-06-2026"


def _parse_ddmmyyyy(value: str | None) -> date | None:
    if not value:
        return None
    try:
        day, month, year = value.split("-")
        return date(int(year), int(month), int(day))
    except (ValueError, AttributeError):
        return None


def _years_of_service(hire_date: date | None, as_of: date) -> float:
    if hire_date is None:
        return 0.0
    return (as_of - hire_date).days / 365.25


def backfill_as_of_date(conn) -> int:
    cur = conn.execute(
        "UPDATE leave_balances SET as_of_date = ? WHERE as_of_date IS NULL",
        (DEFAULT_AS_OF_DATE,),
    )
    return cur.rowcount


def backfill_entitlements(conn) -> int:
    rows = conn.execute(
        """
        SELECT lb.employee_id, lb.annual_used, lb.sick_used, lb.as_of_date,
               e.hire_date
        FROM leave_balances lb
        JOIN employees e ON e.employee_id = lb.employee_id
        WHERE lb.annual_entitlement IS NULL
           OR lb.annual_remaining IS NULL
           OR lb.sick_entitlement IS NULL
           OR lb.sick_remaining IS NULL
           OR lb.emergency_entitlement IS NULL
           OR lb.emergency_used IS NULL
           OR lb.emergency_remaining IS NULL
        """
    ).fetchall()

    updated = 0
    for row in rows:
        employee_id, annual_used, sick_used, as_of_date, hire_date = row

        as_of = _parse_ddmmyyyy(as_of_date) or _parse_ddmmyyyy(DEFAULT_AS_OF_DATE)
        years = _years_of_service(_parse_ddmmyyyy(hire_date), as_of)

        annual_entitlement = (
            ANNUAL_SENIOR_DAYS if years >= ANNUAL_SENIOR_YEARS else ANNUAL_BASE_DAYS
        )
        annual_used = annual_used or 0.0
        annual_remaining = max(annual_entitlement - annual_used, 0)

        sick_used = sick_used or 0.0
        sick_remaining = max(SICK_ENTITLEMENT_DAYS - sick_used, 0)

        emergency_used = 0.0
        emergency_remaining = EMERGENCY_ENTITLEMENT_DAYS - emergency_used

        conn.execute(
            """
            UPDATE leave_balances
            SET annual_entitlement = COALESCE(annual_entitlement, ?),
                annual_remaining = COALESCE(annual_remaining, ?),
                sick_entitlement = COALESCE(sick_entitlement, ?),
                sick_remaining = COALESCE(sick_remaining, ?),
                emergency_entitlement = COALESCE(emergency_entitlement, ?),
                emergency_used = COALESCE(emergency_used, ?),
                emergency_remaining = COALESCE(emergency_remaining, ?)
            WHERE employee_id = ?
            """,
            (
                annual_entitlement,
                annual_remaining,
                SICK_ENTITLEMENT_DAYS,
                sick_remaining,
                EMERGENCY_ENTITLEMENT_DAYS,
                emergency_used,
                emergency_remaining,
                employee_id,
            ),
        )
        updated += 1

    return updated


def main() -> None:
    conn = get_connection()
    try:
        as_of_filled = backfill_as_of_date(conn)
        rows_updated = backfill_entitlements(conn)
        conn.commit()

        print("Leave balance placeholder backfill completed.")
        print(f" - as_of_date filled on {as_of_filled} row(s)")
        print(f" - entitlement/remaining fields backfilled on {rows_updated} row(s)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
