from app.agents.base import BaseAgent
from app.db.approvals import create_pending_approval
from app.db.connection import get_connection
from app.db.proposed_actions import create_proposed_action
from app.security.governance import (
    check_authorization,
    classify_risk,
    detect_prompt_injection,
    mask_pii,
    requires_human_approval,
    validate_output,
)


def _action_summary(action_type: str, payload: dict) -> str:
    if action_type == "leave_request":
        cover = payload.get("suggested_cover_employee_name") or "none suggested"
        return (
            f"Leave request for {payload.get('employee_id')}: "
            f"{payload.get('leave_type')} {payload.get('start_date')}"
            f"→{payload.get('end_date')} ({payload.get('days')} days). "
            f"Cover: {cover}."
        )
    return f"{action_type} for {payload.get('employee_id')}."


def _submit_for_approval(
    employee_id: str, action_type: str, payload: dict, risk_level: str
) -> str | None:
    """Persist a proposed_action + pending_approvals row. Own connection/commit
    since neither the Orchestrator nor /agent/query open one for this path."""
    conn = get_connection()
    try:
        proposal = create_proposed_action(
            conn,
            employee_id=employee_id,
            action_type=action_type,
            payload=payload,
            risk_level=risk_level,
        )
        create_pending_approval(
            conn,
            proposal_id=proposal["proposal_id"],
            employee_id=employee_id,
            action_summary=_action_summary(action_type, payload),
            risk_level=risk_level,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        return None
    finally:
        conn.close()

    cover = payload.get("suggested_cover_employee_name")
    cover_note = f" Suggested cover: {cover}." if cover else ""
    return (
        f"Your request has been submitted for approval "
        f"(proposal {proposal['proposal_id']}).{cover_note}"
    )


class ManagerAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """Manager / Validation Agent — final PASS/FAIL supervisor.

        Expected input:
            query (str)
            user (dict)
            hr_result (dict)
            consultant_result (dict)

        Expected output:
            decision: PASS | FAIL
            reasons (list): which governance checks failed, if any
            response (str): final user-facing text when PASS
        """
        query = str(input.get("query") or "")
        user = input.get("user") or {}
        hr_result = input.get("hr_result") or {}
        consultant_result = input.get("consultant_result") or {}
        reasons: list[str] = []

        if detect_prompt_injection(query):
            reasons.append("Prompt injection detected.")

        if not check_authorization(user, {"type": "agent_query"}):
            reasons.append("User is not authorized to submit agent queries.")

        conflicts = consultant_result.get("conflicts") or []
        if conflicts:
            reasons.extend(str(conflict) for conflict in conflicts)

        proposed_action = hr_result.get("proposed_action")
        target_employee_id = user.get("employee_id")
        if proposed_action:
            payload = proposed_action.get("payload") or {}
            target_employee_id = payload.get("employee_id") or target_employee_id
            resource = {
                "type": "proposed_action",
                "employee_id": target_employee_id,
            }
            if not check_authorization(user, resource):
                reasons.append("User is not authorized for the proposed action.")

        recommendation = str(consultant_result.get("recommendation") or "")
        sources = [
            *(hr_result.get("sources") or []),
            *(consultant_result.get("sources") or []),
        ]
        response = mask_pii(recommendation)

        if not validate_output(response, sources):
            reasons.append("Response is empty, unsupported, or missing sources.")

        if reasons:
            return {
                "decision": "FAIL",
                "reasons": reasons,
                "response": "",
            }

        submission_note = ""
        if proposed_action:
            payload = proposed_action.get("payload") or {}
            action_type = proposed_action.get("action_type", "")
            risk = classify_risk(action_type, payload)
            if requires_human_approval(risk):
                submission_note = _submit_for_approval(
                    target_employee_id, action_type, payload, risk
                )
                if submission_note is None:
                    return {
                        "decision": "FAIL",
                        "reasons": [
                            "Could not submit the request for approval. Please try again."
                        ],
                        "response": "",
                    }

        final_response = (
            f"{response}\n\n{submission_note}".strip() if submission_note else response
        )

        return {
            "decision": "PASS",
            "reasons": [],
            "response": final_response,
        }
