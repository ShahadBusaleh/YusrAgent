from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.api.deps import CurrentUser, require_role, roles_at_least
from app.agents.payroll_report import generate_monthly_payroll_pdf
from app.db.connection import get_db
from app.db.payroll import list_payroll_for_period, list_payroll_periods

router = APIRouter(prefix="/payroll", tags=["payroll"])


@router.get("/periods")
def get_periods(
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Available pay periods, newest first, for the Payroll month picker."""

    return {"periods": list_payroll_periods(conn)}


@router.get("/monthly/{period}/pdf")
def get_monthly_payroll_pdf(
    period: str,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """All-employee payroll report for one month, as a downloadable PDF."""

    rows = list_payroll_for_period(conn, period)

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No payroll records found for {period}.",
        )

    pdf_bytes = generate_monthly_payroll_pdf(rows, period)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="payroll_{period}.pdf"'
        },
    )
