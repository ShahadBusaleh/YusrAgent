from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from openai import OpenAIError

from app.agents.growth_plan import (
    GrowthPlanError,
    GrowthPlanUnavailableError,
    InvalidCVError,
    extract_pdf_text,
    generate_growth_plan,
    prepare_cv_text,
)
from app.api.deps import CurrentUser, get_current_user
from app.db.connection import get_db
from app.db.employees import get_employee
from app.db.growth_opportunities import (
    list_opportunities_for_employee,
    save_growth_plan,
)


router = APIRouter(
    prefix="/growth",
    tags=["growth-opportunities"],
)

_MAX_CV_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/opportunities")
def get_opportunities(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Department experience gaps this employee is a close-fit candidate for."""

    return {
        "opportunities": list_opportunities_for_employee(
            conn,
            user.employee_id,
        )
    }


@router.post("/opportunities/{skill_id}/cv")
def upload_cv(
    skill_id: str,
    cv: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Upload a CV for one gap and receive a personalized growth plan."""

    opportunities = list_opportunities_for_employee(conn, user.employee_id)
    opportunity = next(
        (o for o in opportunities if o["skill_id"] == skill_id),
        None,
    )

    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This experience gap is not currently open to you.",
        )

    if (cv.content_type or "").lower() != "application/pdf" and not (
        cv.filename or ""
    ).lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload your CV as a PDF file.",
        )

    file_bytes = cv.file.read(_MAX_CV_BYTES + 1)

    if len(file_bytes) > _MAX_CV_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That CV file is too large (10 MB limit).",
        )

    cv_text = extract_pdf_text(file_bytes)

    if not cv_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "We couldn't read any text from that PDF. Try a "
                "text-based PDF export of your CV rather than a "
                "scanned image."
            ),
        )

    employee = get_employee(conn, user.employee_id)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee profile not found.")

    # Use the same employee-aware sanitized text for generation and persistence.
    cv_text = prepare_cv_text(cv_text, employee)
    try:
        if not cv_text:
            raise InvalidCVError("The CV contains no usable text.")
        plan_text = generate_growth_plan(employee, opportunity, cv_text)
    except InvalidCVError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No usable CV content remains. Please upload a CV describing your skills and experience.",
        ) from exc
    except GrowthPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't generate a complete growth plan. Please retry, or upload a clearer CV.",
        ) from exc
    except (GrowthPlanUnavailableError, OpenAIError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Growth plans are temporarily unavailable. Please try again later.",
        ) from exc

    saved = save_growth_plan(
        conn,
        employee_id=user.employee_id,
        department_id=opportunity["department_id"],
        skill_id=skill_id,
        cv_filename=cv.filename,
        cv_text=cv_text,
        plan_text=plan_text,
    )

    return {
        "skill_id": skill_id,
        "plan_text": saved["plan_text"],
    }
