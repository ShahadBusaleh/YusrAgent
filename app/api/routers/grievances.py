"""Grievance API routes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_role
from app.db.connection import get_db
from app.db.grievances import (
    decide_grievance,
    get_grievance,
    list_grievances,
)


router = APIRouter(
    prefix="/grievances",
    tags=["grievances"],
)


class GrievanceDecisionRequest(BaseModel):
    decision: str
    response: str | None = None


@router.get("")
def get_grievances(
    conn: sqlite3.Connection = Depends(get_db),
    user: CurrentUser = Depends(
        require_role(
            "hr_specialist",
            "hr_manager",
            "admin",
        )
    ),
) -> list[dict]:
    return list_grievances(conn)


@router.get("/{grievance_id}")
def get_grievance_by_id(
    grievance_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: CurrentUser = Depends(
        require_role(
            "hr_specialist",
            "hr_manager",
            "admin",
        )
    ),
) -> dict:
    grievance = get_grievance(conn, grievance_id)

    if grievance is None:
        raise HTTPException(
            status_code=404,
            detail="Grievance not found.",
        )

    return grievance


@router.post("/{grievance_id}/decide")
def decide_grievance_route(
    grievance_id: str,
    body: GrievanceDecisionRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: CurrentUser = Depends(
        require_role(
            "hr_specialist",
            "hr_manager",
            "admin",
        )
    ),
) -> dict:
    decision = body.decision.strip().lower()

    if decision not in {"accept", "reject"}:
        raise HTTPException(
            status_code=400,
            detail="Decision must be 'accept' or 'reject'.",
        )

    grievance = get_grievance(conn, grievance_id)

    if grievance is None:
        raise HTTPException(
            status_code=404,
            detail="Grievance not found.",
        )

    if grievance.get("status") != "PENDING_HR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail="This grievance has already been reviewed.",
        )

    updated = decide_grievance(
        conn,
        grievance_id=grievance_id,
        decision=decision,
        decided_by=user.user_id,
        response=body.response,
    )

    conn.commit()

    return updated