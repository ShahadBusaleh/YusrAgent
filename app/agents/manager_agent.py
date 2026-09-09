from app.agents.base import BaseAgent
from app.security.governance import (
    check_authorization,
    classify_risk,
    detect_prompt_injection,
    mask_pii,
    requires_human_approval,
    validate_output,
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
        if proposed_action:
            payload = proposed_action.get("payload") or {}
            target_employee_id = payload.get("employee_id") or user.get("employee_id")
            resource = {
                "type": "proposed_action",
                "employee_id": target_employee_id,
            }
            if not check_authorization(user, resource):
                reasons.append("User is not authorized for the proposed action.")

            risk = classify_risk(proposed_action.get("action_type", ""), payload)
            if requires_human_approval(risk):
                reasons.append(f"{risk.title()}-risk action requires human approval.")

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

        return {
            "decision": "PASS",
            "reasons": [],
            "response": response,
        }
