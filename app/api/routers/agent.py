from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.orchestrator import OrchestratorAgent
from app.api.deps import CurrentUser, get_current_user
from app.api.schemas import AgentQueryRequest
from app.security.governance import sanitize_input

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/query")
def agent_query(
    body: AgentQueryRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Any authenticated role. Orchestrator is a stub until implemented by hand."""
    return OrchestratorAgent().run(
        {
            "query": sanitize_input(body.query),
            "user": {
                "user_id": user.user_id,
                "employee_id": user.employee_id,
                "username": user.username,
                "role": user.role,
            },
        }
    )
