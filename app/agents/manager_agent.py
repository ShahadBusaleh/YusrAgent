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


def _fallback_hr_summary(facts: dict) -> str:
    """Deterministic, rule-based response built from HR facts alone.

    The Orchestrator now conditionally skips the Consultant for
    HR-only intents, leaving consultant_result.recommendation empty.
    Without this, Manager's groundedness check would reject every
    plain HR fact (e.g. a leave-balance lookup) since there would be
    no response text at all. No LLM call — Manager stays rule-based.
    """
    if not isinstance(facts, dict) or not facts:
        return ""

    parts: list[str] = []

    assessment = facts.get("request_assessment")
    if isinstance(assessment, dict) and assessment.get("status") == "NEEDS_INFORMATION":
        missing = assessment.get("missing_information") or []
        missing_text = ", ".join(str(m) for m in missing) if missing else "the leave type and exact dates"
        parts.append(
            f"I couldn't submit a leave request from that message — please include {missing_text} "
            "(e.g. \"take annual leave from 2027-01-10 to 2027-01-12\")."
        )

    balance = facts.get("leave_balance")
    if isinstance(balance, dict):
        bits = [
            f"{label} {balance[key]:g}"
            if isinstance(balance.get(key), (int, float))
            else f"{label} {balance.get(key)}"
            for label, key in (
                ("annual", "annual_remaining"),
                ("sick", "sick_remaining"),
                ("emergency", "emergency_remaining"),
            )
            if balance.get(key) is not None
        ]
        if bits:
            parts.append("Remaining leave balance: " + ", ".join(bits) + " days.")
    elif facts.get("remaining_balance") is not None:
        parts.append(f"Remaining leave balance: {facts['remaining_balance']} days.")

    profile = facts.get("profile")
    if isinstance(profile, dict) and profile.get("full_name"):
        bits = [b for b in (profile.get("job_title"), profile.get("department_name")) if b]
        parts.append(f"{profile['full_name']}" + (f" — {', '.join(bits)}." if bits else "."))

    requests = facts.get("leave_requests")
    if isinstance(requests, list) and requests:
        parts.append(f"You have {len(requests)} leave request(s) on record.")

    return " ".join(parts)


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

        # Consultant no longer reads hr_result (by design — it's now
        # policy-only), so balance sufficiency has to be checked here
        # directly against the facts HR already computed.
        facts = hr_result.get("facts") or {}
        requested_days = facts.get("requested_days")
        remaining_balance = facts.get("remaining_balance")
        if (
            isinstance(requested_days, (int, float))
            and isinstance(remaining_balance, (int, float))
            and requested_days > remaining_balance
        ):
            reasons.append(
                f"Requested {requested_days:g} days exceeds the "
                f"remaining balance of {remaining_balance:g} days."
            )

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
        if not recommendation:
            recommendation = _fallback_hr_summary(hr_result.get("facts") or {})
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
