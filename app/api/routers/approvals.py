from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.agents.orchestrator import OrchestratorAgent
from app.agents.translation import localize_many
from app.api.deps import CurrentUser, get_request_lang, require_role, roles_at_least
from app.api.schemas import ApprovalDecision, ApprovalOut
from app.db.connection import get_db
from app.db.translation_cache import database_path
from app.db import approvals as approvals_db
from app.db.audit import write_audit

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return approvals_db.list_pending_approvals(
    conn,
    status=status_filter,
    exclude_employee_id=user.employee_id,
)

@router.post("/{approval_id}/decide", response_model=ApprovalOut)
def decide(
    approval_id: str,
    body: ApprovalDecision,
    user: CurrentUser = Depends(require_role(*roles_at_least("hr_manager"))),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    approval = approvals_db.get_approval(
        conn,
        approval_id,
    )

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    # Prevent an HR employee from approving/rejecting their own request.
    if approval.get("employee_id") == user.employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot approve or reject your own request.",
        )

    updated = approvals_db.decide_approval(
        conn,
        approval_id,
        decision=body.decision,
        decided_by=user.employee_id,
        decision_note=body.decision_note,
        cover_employee_id=body.cover_employee_id,
    )

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    write_audit(
        conn,
        actor=user.username,
        event_type=f"approval_{body.decision}",
        employee_id=updated.get("employee_id"),
        details=approval_id,
    )

    return updated

@router.get("/{approval_id}/brief")
def get_approval_brief(
    approval_id: str,
    user: CurrentUser = Depends(
        require_role(*roles_at_least("hr_manager"))
    ),
    conn: sqlite3.Connection = Depends(get_db),
    lang: str = Depends(get_request_lang),
) -> dict:
    approval = approvals_db.get_approval(
        conn,
        approval_id,
    )

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    if approval.get("employee_id") == user.employee_id:
        raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You cannot view the decision brief for your own request.",
    )
    proposal_id = approval.get("proposal_id")

    if not proposal_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval has no proposal_id",
        )

    orchestrator = OrchestratorAgent()

    brief = orchestrator.explain_pending_approval(
        proposal_id
    )

    if brief.get("status") != "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=brief.get(
                "error",
                "Could not generate decision brief",
            ),
        )

    if lang == "ar":
        # Only the LLM-written / free-text parts need translating; the UI
        # renders everything else (action, risk, precedent counts, the
        # Manager's recommendation keyword) from structured fields via
        # i18n. English originals are kept alongside the *_ar fields.
        detail = brief.get("brief") or {}
        policy = detail.get("policy") or {}
        manager = detail.get("manager") or {}
        # The UI reads the APPROVE / REJECT / MANAGER REVIEW keyword from
        # the English response and localizes it itself, so only the
        # evidence lines are translated (without the keyword line).
        explanation = "\n".join(
            line
            for line in str(manager.get("response") or "").splitlines()
            if not line.strip().lower().startswith("ai recommendation:")
        )
        reasons = [str(r) for r in manager.get("reasons") or []]
        # The brief is generated on demand, so it can't be pre-translated;
        # translating all its parts in parallel keeps the wait to about one
        # LLM round-trip.
        policy_ar, explanation_ar, *reasons_ar = localize_many(
            [policy.get("recommendation"), explanation, *reasons], lang, database_path(conn)
        )
        if policy.get("recommendation"):
            policy["recommendation_ar"] = policy_ar
        if explanation:
            manager["explanation_ar"] = explanation_ar
        if reasons:
            manager["reasons_ar"] = reasons_ar

    return brief