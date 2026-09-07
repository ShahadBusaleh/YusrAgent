from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_current_user
from app.api.schemas import ProposedActionOut
from app.db.connection import get_db
from app.db import proposed_actions as actions_db

router = APIRouter(prefix="/proposed-actions", tags=["proposed-actions"])


@router.get("", response_model=list[ProposedActionOut])
def list_actions(
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    if user.role == "employee":
        return actions_db.list_proposed_actions(conn, employee_id=user.employee_id)
    return actions_db.list_proposed_actions(conn)


@router.get("/{proposal_id}", response_model=ProposedActionOut)
def get_action(
    proposal_id: str,
    user: CurrentUser = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    item = actions_db.get_proposed_action(conn, proposal_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user.role == "employee" and item.get("employee_id") != user.employee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Own records only")
    return item
