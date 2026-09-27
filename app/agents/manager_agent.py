import logging
import re

from app.agents import arabic_text as ar
from app.agents.base import BaseAgent
from app.db.approvals import create_pending_approval, decide_approval
from app.db.connection import get_connection
from app.db.proposed_actions import create_proposed_action
from app.security.governance import (
    check_authorization,
    classify_risk,
    detect_prompt_injection,
    mask_pii,
    requires_human_approval,
    validate_output,
    verify_citations,
)


_LEAVE_FIELDS = {"leave_type", "start_date", "end_date"}

_READABLE_FIELDS = {
    "leave_type": "the leave type",
    "start_date": "the start date",
    "end_date": "the end date",
    "new_iban": "your new IBAN",
    "new_value": "the new value",
    "field_name": "which field to change",
}

# Payload fields that must be non-empty before an action may be submitted.
_REQUIRED_PAYLOAD_FIELDS = {
    "leave_request": ("employee_id", "leave_type", "start_date", "end_date"),
    "personal_info_update": ("employee_id", "field_name", "new_value"),
    "bank_update": ("employee_id", "new_iban"),
    "certificate_request": ("employee_id",),
}

logger = logging.getLogger(__name__)


def _assessment_message(assessment: dict) -> str:
    """User-facing text for an HR request_assessment that blocks submission."""
    status = assessment.get("status")
    notes = [str(n) for n in assessment.get("notes") or [] if n]
    missing = [str(m) for m in assessment.get("missing_information") or []]

    if status == "NOT_AUTHORIZED":
        return " ".join(notes) or "You're not authorized to view this information."

    if status != "NEEDS_INFORMATION":
        return ""

    is_leave = assessment.get("action_type") == "leave_request" or (
        missing and set(missing) <= _LEAVE_FIELDS
    )
    if is_leave:
        missing_text = (
            ", ".join(_READABLE_FIELDS.get(m, m) for m in missing)
            if missing
            else "the leave type and exact dates"
        )
        return (
            f"I couldn't submit a leave request from that message — please include {missing_text} "
            "(e.g. \"take annual leave from 2027-01-10 to 2027-01-12\")."
        )

    detail = " ".join(notes) or (
        "Please include " + ", ".join(_READABLE_FIELDS.get(m, m) for m in missing) + "."
    )
    return f"I need a bit more information to prepare this request. {detail}"


def _missing_payload_fields(proposed_action: dict) -> list[str]:
    action_type = proposed_action.get("action_type", "")
    payload = proposed_action.get("payload") or {}
    return [
        field
        for field in _REQUIRED_PAYLOAD_FIELDS.get(action_type, ("employee_id",))
        if payload.get(field) in (None, "")
    ]


# Greetings and thanks classify as OTHER; answer them as such instead of
# calling them out of scope.
_GREETING_RE = re.compile(
    r"^(hi|hello|hey|good (morning|afternoon|evening)|thanks?( you)?|"
    r"salam|as-?salamu? alaikum|مرحبا|مرحباً|أهلا|أهلاً|السلام عليكم|"
    r"صباح الخير|مساء الخير|شكرا|شكراً)[\s!.,؟?]*$",
    re.IGNORECASE,
)

def _failure_message(reasons: list[str]) -> str:
    """Short, user-safe explanation for a FAIL. Raw reasons stay in
    `reasons` for logs/UI debugging; this is what the employee reads."""
    text = " ".join(reasons).lower()
    if "prompt injection" in text:
        return "I can't process that request. Please rephrase it as a normal HR question."
    if "not authorized" in text:
        return "You're not authorized to access that information or perform that action."
    for reason in reasons:
        if "exceeds the remaining balance" in reason:
            return f"I can't submit this leave request. {reason}"
    if "could not submit" in text:
        return "Your request couldn't be submitted for approval. Please try again."
    if "policy lookup failed" in text:
        return "I couldn't reach the HR policy documents right now. Please try again in a moment."
    if text == "greeting.":
        return (
            "Hello! I'm Yusor, your HR assistant. I can help with leave, payroll, "
            "attendance, your personal details, certificates, and HR policy."
        )
    if "outside hr scope" in text:
        return (
            "That's outside what I can help with. I'm Yusor, your HR assistant: "
            "ask me about leave, payroll, attendance, your personal details, "
            "certificates, or HR policy."
        )
    return (
        "I couldn't find a reliable answer to that. I can help with leave, payroll, "
        "attendance, your personal details, certificates, and HR policy questions."
    )


def _fail(reasons: list[str]) -> dict:
    reasons = list(dict.fromkeys(reasons))
    response = _failure_message(reasons)
    return {
        "decision": "FAIL",
        "reasons": reasons,
        "response": response,
        "response_blocks": [[(response, ar.failure_message_ar(reasons))]],
    }


def _fallback_hr_summary(facts: dict, include_profile: bool = True) -> str:
    """Deterministic, rule-based response built from HR facts alone.

    The Orchestrator now conditionally skips the Consultant for
    HR-only intents, leaving consultant_result.recommendation empty.
    Without this, Manager's groundedness check would reject every
    plain HR fact (e.g. a leave-balance lookup) since there would be
    no response text at all. No LLM call — Manager stays rule-based.
    """
    return " ".join(en for en, _ in _hr_summary_pairs(facts, include_profile))


def _hr_summary_pairs(facts: dict, include_profile: bool = True) -> list[tuple[str, str | None]]:
    """_fallback_hr_summary as (English, Arabic) sentence pairs. Arabic is
    None where the text isn't a fixed template (free-text HR notes); the
    Orchestrator translates only those."""
    if not isinstance(facts, dict) or not facts:
        return []

    parts: list[tuple[str, str | None]] = []

    assessment = facts.get("request_assessment")
    if isinstance(assessment, dict):
        message = _assessment_message(assessment)
        if message:
            parts.append((message, ar.assessment_message_ar(assessment)))

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
            parts.append((
                "Remaining leave balance: " + ", ".join(bits) + " days.",
                ar.leave_balance_ar(balance),
            ))
    elif facts.get("remaining_balance") is not None:
        remaining = facts["remaining_balance"]
        remaining_text = f"{remaining:g}" if isinstance(remaining, (int, float)) else remaining
        parts.append((
            f"Remaining leave balance: {remaining_text} days.",
            ar.remaining_balance_ar(remaining),
        ))

    requests = facts.get("leave_requests")
    if isinstance(requests, list) and requests:
        parts.append((
            f"You have {len(requests)} leave request(s) on record.",
            ar.leave_requests_ar(len(requests)),
        ))

    payroll = facts.get("payroll")
    if isinstance(payroll, dict) and payroll:
        bits = [
            f"{label} {payroll[key]:g} SAR"
            if isinstance(payroll.get(key), (int, float))
            else f"{label} {payroll.get(key)}"
            for label, key in (
                ("gross pay", "gross_pay_sar"),
                ("total deductions", "total_deductions_sar"),
                ("net pay", "net_pay_sar"),
            )
            if payroll.get(key) is not None
        ]
        period = payroll.get("pay_period")
        prefix = f"Payroll ({period}): " if period else "Payroll: "
        if bits:
            parts.append((prefix + ", ".join(bits) + ".", ar.payroll_ar(payroll)))
    else:
        payroll_notice = facts.get("payroll_notice")
        if isinstance(payroll_notice, str) and payroll_notice:
            parts.append((payroll_notice, None))

    attendance = facts.get("attendance")
    if isinstance(attendance, dict) and attendance:
        bits = [
            f"{label}: {attendance[key]:g}"
            if isinstance(attendance.get(key), (int, float))
            else f"{label}: {attendance.get(key)}"
            for label, key in (
                ("present", "days_present"),
                ("absent", "days_absent"),
                ("attendance rate", "attendance_rate_pct"),
            )
            if attendance.get(key) is not None
        ]
        period = attendance.get("attendance_month")
        prefix = f"Attendance ({period}): " if period else "Attendance: "
        if bits:
            parts.append((prefix + ", ".join(bits) + ".", ar.attendance_ar(attendance)))
    else:
        attendance_notice = facts.get("attendance_notice")
        if isinstance(attendance_notice, str) and attendance_notice:
            parts.append((attendance_notice, None))

    gap_text = _experience_gap_summary(facts.get("experience_gap"))
    if gap_text:
        parts.append((gap_text, None))

    # Identity is only useful as context alongside a specific answer above,
    # or as a last-resort reply when nothing else matched the query — not
    # appended to every response regardless of what was actually asked.
    profile = facts.get("profile")
    if include_profile and not parts and isinstance(profile, dict) and profile.get("full_name"):
        bits = [b for b in (profile.get("job_title"), profile.get("department_name")) if b]
        # Job titles and departments are English DB values: translate.
        parts.append((f"{profile['full_name']}" + (f" — {', '.join(bits)}." if bits else "."), None))

    return parts


def _experience_gap_summary(gap: dict | None) -> str:
    """Skill gaps per department with the internal pool that can grow
    into each one — the same data Team Insights shows."""
    if not isinstance(gap, dict):
        return ""
    departments = gap.get("departments") or []
    if not departments:
        return ""

    lines: list[str] = []
    for dep in departments:
        gaps = dep.get("gaps") or []
        name = dep.get("department_name") or dep.get("department_id")
        if not gaps:
            if gap.get("scope") != "company":
                lines.append(f"**{name}**: no skill gaps — every required skill is covered.")
            continue
        lines.extend(["", f"**{name}**"])
        for item in gaps:
            label = f"{item['skill_name']}" + (" (critical)" if item.get("is_critical") else "")
            pool = item.get("internal_candidates") or {}
            titles = pool.get("titles") or []
            if pool.get("count"):
                roles = ", ".join(f"{t['job_title']} ({t['headcount']})" for t in titles)
                lines.append(
                    f"- {label}: no one holds this skill yet. Grow internally — "
                    f"{pool['count']} candidate(s) from {roles}."
                )
            else:
                lines.append(
                    f"- {label}: no one holds this skill and no adjacent role "
                    f"exists — hire externally."
                )

    if not lines:
        return "No skill gaps found — every department covers its required skills."
    header = (
        "Skill gaps across departments, with the internal candidates who can grow into them:"
        if gap.get("scope") == "company"
        else "Skill gaps and internal candidates:"
    )
    footer = (
        "Candidates see these gaps on their Growth Opportunities page and can "
        "upload a CV for a personal development plan. See Team Insights for names."
    )
    return "\n".join([header, *lines, "", footer])


def _shown(value) -> str:
    """A payload value for display; an empty old value reads "(not set)"
    rather than the literal "None"."""
    return "(not set)" if value in (None, "") else str(value)


def _action_summary(action_type: str, payload: dict) -> str:
    if action_type == "leave_request":
        cover = payload.get("suggested_cover_employee_name") or "none suggested"
        return (
            f"Leave request for {payload.get('employee_id')}: "
            f"{payload.get('leave_type')} {payload.get('start_date')}"
            f"→{payload.get('end_date')} ({payload.get('days')} days). "
            f"Cover: {cover}."
        )
    if action_type == "personal_info_update":
        return (
            f"Personal info update for {payload.get('employee_id')}: "
            f"{payload.get('field_name')} "
            f"\"{_shown(payload.get('old_value'))}\" → \"{_shown(payload.get('new_value'))}\"."
        )
    if action_type == "bank_update":
        return (
            f"Bank/IBAN update for {payload.get('employee_id')}: "
            f"IBAN \"{_shown(payload.get('old_iban'))}\" → \"{_shown(payload.get('new_iban'))}\"."
        )
    if action_type == "certificate_request":
        return (
            f"Certificate request for {payload.get('employee_id')} "
            f"({payload.get('full_name') or 'employee'}, "
            f"{payload.get('job_title') or 'role n/a'})."
        )
    return f"{action_type} for {payload.get('employee_id')}."

def _get_ai_recommendation(
    facts: dict,
    consultant_result: dict,
) -> str:
    """Build a recommendation from deterministic evidence."""
    if not isinstance(facts, dict):
        return "MANAGER REVIEW"

    proposed_action = facts.get("proposed_action") or {}
    action_type = str(
        proposed_action.get("action_type") or ""
    )
    payload = proposed_action.get("payload") or {}

    # Security / risk evidence
    try:
        risk_level = classify_risk(action_type, payload)
    except Exception:
        risk_level = ""

    # Real policy conflicts always require human review.
    conflicts = consultant_result.get("conflicts") or []
    if conflicts:
        return "MANAGER REVIEW"

    # Leave balance evidence.
    requested_days = facts.get("requested_days")
    remaining_balance = facts.get("remaining_balance")

    if (
        isinstance(requested_days, (int, float))
        and isinstance(remaining_balance, (int, float))
        and requested_days > remaining_balance
    ):
        return "REJECT"

    # Medium/high-risk actions remain for human review.
    if risk_level in {"MEDIUM", "HIGH"}:
        return "MANAGER REVIEW"

    # Historical precedent.
    precedent = facts.get("historical_precedent") or {}
    approved = precedent.get("approved_count") or 0
    denied = precedent.get("denied_count") or 0
    total = approved + denied

    if total == 0:
        return "MANAGER REVIEW"

    approval_ratio = approved / total

    # Evidence-based recommendation:
    # sufficient balance + positive precedent + low risk.
    if (
        isinstance(requested_days, (int, float))
        and isinstance(remaining_balance, (int, float))
        and requested_days <= remaining_balance
        and approval_ratio >= 0.5
        and risk_level == "LOW"
    ):
        return "APPROVE"

    return "MANAGER REVIEW"
def _compose_decision_brief(facts: dict, consultant_result: dict) -> str:
    """Build an evidence-based Decision Brief for human HR review."""

    if not isinstance(facts, dict):
        return ""

    parts: list[str] = []

    # Employee
    profile = facts.get("profile")
    if isinstance(profile, dict) and profile.get("full_name"):
        employee_name = profile["full_name"]

        role_parts = [
            profile.get("job_title"),
            profile.get("department_name"),
        ]
        role_parts = [str(x) for x in role_parts if x]

        employee_text = employee_name
        if role_parts:
            employee_text += f" — {', '.join(role_parts)}"

        parts.append(f"Employee: {employee_text}")

    # HR assessment
    assessment = facts.get("request_assessment")
    if isinstance(assessment, dict):
        status = assessment.get("status")
        action_type = assessment.get("action_type")

        if action_type:
            parts.append(f"Request: {action_type}")

        if status:
            parts.append(f"HR Assessment: {status}")

        missing = assessment.get("missing_information") or []
        if missing:
            parts.append(
                "Missing information: "
                + ", ".join(str(item) for item in missing)
            )

        notes = assessment.get("notes") or []
        for note in notes:
            if note:
                parts.append(f"HR Note: {note}")

    # Request evidence
    requested_days = facts.get("requested_days")
    remaining_balance = facts.get("remaining_balance")

    if requested_days is not None:
        parts.append(f"Requested days: {requested_days:g}")

    if remaining_balance is not None:
        parts.append(f"Remaining balance: {remaining_balance:g}")

    # Historical precedent
    precedent = facts.get("historical_precedent")

    if isinstance(precedent, dict):
        approved = precedent.get("approved_count") or 0
        denied = precedent.get("denied_count") or 0

        parts.append(
            f"Historical precedent: "
            f"{approved} approved, {denied} denied."
        )

    # AI recommendation
    ai_recommendation = _get_ai_recommendation(
        facts,
        consultant_result,
    )

    parts.append(
        f"AI Recommendation: {ai_recommendation}"
    )

    # Policy evidence
    consultant_sources = (
        consultant_result.get("sources") or []
        if isinstance(consultant_result, dict)
        else []
    )

    source_names = []

    for source in consultant_sources:
        if isinstance(source, dict):
            source_name = (
                source.get("source_name")
                or source.get("filename")
                or source.get("id")
            )
        else:
            source_name = source

        if source_name:
            source_names.append(str(source_name))

    source_names = list(dict.fromkeys(source_names))

    if source_names:
        parts.append(
            "Policy evidence: " + ", ".join(source_names)
        )

    # Human decision remains final
    parts.append(
        "Final decision: Pending Manager Review"
    )

    return "\n".join(parts)
def _find_duplicate_pending_action(
    conn,
    employee_id: str,
    action_type: str,
    payload: dict,
) -> dict | None:
    """Return an existing pending approval for the same action and payload."""
    import json

    rows = conn.execute(
        """
        SELECT pa.*, p.approval_id
        FROM proposed_actions pa
        JOIN pending_approvals p
            ON p.proposal_id = pa.proposal_id
        WHERE pa.employee_id = ?
          AND pa.action_type = ?
          AND pa.status = 'pending_approval'
          AND p.status = 'pending'
        ORDER BY pa.created_at DESC
        """,
        (employee_id, action_type),
    ).fetchall()

    target_payload = json.dumps(payload, sort_keys=True)

    for row in rows:
        existing_payload = json.dumps(
            json.loads(row["payload_json"]),
            sort_keys=True,
        )
        if existing_payload == target_payload:
            return dict(row)

    return None
def _submit_for_approval(
    employee_id: str,
    action_type: str,
    payload: dict,
    risk_level: str,
    auto_approve: bool = False,
) -> tuple[str, str] | None:
    """Persist a proposed_action + pending_approvals row. Own connection/commit
    since neither the Orchestrator nor /agent/query open one for this path.

    Low-risk actions (auto_approve=True) go through the same queue and are
    immediately approved by `decide_approval`, so its `_sync_*` side effects
    materialize the change exactly as a human approval would. Previously
    they were confirmed to the user but never written anywhere."""
    conn = get_connection()
    try:
        duplicate = _find_duplicate_pending_action(
            conn,
            employee_id,
            action_type,
            payload,
        )

        if duplicate:
            return (
                f"Your request is already pending approval "
                f"(proposal {duplicate['proposal_id']}).",
                ar.submission_note_ar("duplicate", duplicate["proposal_id"]),
            )
    
        proposal = create_proposed_action(
            conn,
            employee_id=employee_id,
            action_type=action_type,
            payload=payload,
            risk_level=risk_level,
        )
        approval = create_pending_approval(
            conn,
            proposal_id=proposal["proposal_id"],
            employee_id=employee_id,
            action_summary=_action_summary(action_type, payload),
            risk_level=risk_level,
        )
        if auto_approve:
            decide_approval(
                conn,
                approval["approval_id"],
                decision="approve",
                # decided_by is a FK to users; NULL + the note marks it as
                # a system decision rather than recording the employee as
                # approving their own change.
                decided_by=None,
                decision_note="Auto-approved by Yusor: low-risk change.",
            )
        conn.commit()
    except Exception:
        logger.exception(
            "Could not submit %s for %s", action_type, employee_id
        )
        conn.rollback()
        return None
    finally:
        conn.close()

    if auto_approve:
        return (
            f"Your change has been recorded and auto-approved as a low-risk "
            f"update (proposal {proposal['proposal_id']}).",
            ar.submission_note_ar("auto_approved", proposal["proposal_id"]),
        )

    cover = payload.get("suggested_cover_employee_name")
    cover_note = f" Suggested cover: {cover}." if cover else ""
    return (
        f"Your request has been submitted for approval "
        f"(proposal {proposal['proposal_id']}).{cover_note}",
        ar.submission_note_ar("submitted", proposal["proposal_id"], cover),
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
            response (str): final user-facing text (a short explanation on FAIL)
        """
        query = str(input.get("query") or "")
        user = input.get("user") or {}
        hr_result = input.get("hr_result") or {}
        consultant_result = input.get("consultant_result") or {}
        reasons: list[str] = []

        if input.get("intent") == "SECURITY_BLOCK":
            # The Orchestrator already made the block decision (assess_query_risk
            # returned HIGH) before HR/Consultant ever ran, so there's no
            # hr_result/consultant_result to validate here — just report why the
            # request was blocked. Without this, the request fell through to the
            # generic path below, which always empties `response` on FAIL, and
            # the user never saw why their request was refused.
            security = input.get("security") or {}
            reason = security.get("reason") or "A security policy violation was detected."
            return {
                "decision": "FAIL",
                "reasons": [reason],
                "response": f"Your request was blocked: {reason}",
            }

        # MEDIUM risk from the Orchestrator's assess_query_risk (e.g. "act as
        # admin") is treated like a detected injection instead of ignored.
        security = input.get("security") or {}
        if detect_prompt_injection(query) or security.get("risk") == "MEDIUM":
            reasons.append("Prompt injection detected.")

        if not check_authorization(user, {"type": "agent_query"}):
            reasons.append("User is not authorized to submit agent queries.")

        # Neither HR nor Consultant runs for OTHER, so the generic path
        # failed it as "unsupported" and told the user "I couldn't find a
        # reliable answer", as if a lookup had failed (o-07: weather).
        if (
            input.get("intent") == "OTHER"
            and not input.get("intent_unclassified")
            and not reasons
        ):
            if _GREETING_RE.match(query.strip()):
                return _fail(["Greeting."])
            return _fail(["Question is outside HR scope."])

        # Grievance workflow:
        # Manager only validates whether the grievance can proceed to Human HR.
        if input.get("intent") == "GRIEVANCE":
            conflicts = consultant_result.get("conflicts") or []

            if conflicts:
                reasons.extend(str(conflict) for conflict in conflicts)

            if reasons:
                return _fail(reasons)

            return {
                "decision": "PASS",
                "reasons": [],
                "response": "Grievance workflow approved for Human HR review.",
            }
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

            # Submission guard: never submit what HR marked incomplete or
            # whose required payload fields are empty (this is how an
            # IBAN "None" -> "None" request used to reach the queue).
            assessment = facts.get("request_assessment") or {}
            missing = _missing_payload_fields(proposed_action)
            if assessment.get("status") in {"NEEDS_INFORMATION", "NOT_AUTHORIZED"} or missing:
                if missing and assessment.get("status") != "NEEDS_INFORMATION":
                    facts = {
                        **facts,
                        "request_assessment": {
                            "status": "NEEDS_INFORMATION",
                            "action_type": proposed_action.get("action_type"),
                            "missing_information": missing,
                            "notes": [],
                        },
                    }
                proposed_action = None

        # A failed Consultant run (success=False) reports its problem in
        # `error`; its recommendation text is a status message, not policy,
        # so it must not be shown or validated as an answer.
        consultant_failed = consultant_result.get("success") is False
        policy_text = "" if consultant_failed else str(consultant_result.get("recommendation") or "")
        hr_sources = list(hr_result.get("sources") or [])
        consultant_sources = list(consultant_result.get("sources") or [])
        sources = [*hr_sources, *consultant_sources]

        # Each paragraph as (English, Arabic) pieces: the Orchestrator uses
        # the fixed Arabic where there is one and translates only the rest.
        blocks: list[list[tuple[str, str | None]]] = []
        # The requester's own submitted change, echoed back unmasked (below).
        own_change: tuple[str, str | None] | None = None
        if facts.get("historical_precedent") is not None:
            # Decision Brief: composed deterministically from HR facts.
            response_text = _compose_decision_brief(facts, consultant_result) or policy_text
            blocks.append([(response_text, None)])
            grounded = bool(response_text and sources)
        else:
            # HR facts, the pending action, and the policy answer are all
            # shown together. Previously a non-empty policy answer replaced
            # the HR facts, so BOTH queries ("how many days do I have, and
            # can I carry them forward?") lost the employee's own numbers.
            parts: list[str] = []
            fact_pairs = _hr_summary_pairs(
                facts, include_profile=not (policy_text or proposed_action)
            )
            if fact_pairs:
                parts.append(" ".join(en for en, _ in fact_pairs))
                blocks.append(fact_pairs)
            if proposed_action:
                action_type = proposed_action.get("action_type", "")
                payload = proposed_action.get("payload") or {}
                parts.append(_action_summary(action_type, payload))
                blocks.append([(parts[-1], ar.action_summary_ar(action_type, payload))])
                if action_type == "personal_info_update":
                    own_change = blocks[-1][0]
            if policy_text:
                parts.append(policy_text)
                # The Consultant writes Arabic directly for Arabic readers.
                is_arabic = consultant_result.get("language") == "ar"
                blocks.append([(policy_text, policy_text if is_arabic else None)])
            elif consultant_failed and parts:
                parts.append(
                    "(I couldn't retrieve the related HR policy right now, "
                    "so this answer covers your records only.)"
                )
                blocks.append([(parts[-1], ar.POLICY_UNAVAILABLE_AR)])
            response_text = "\n\n".join(parts)

            # Only the LLM-generated policy text needs a groundedness check
            # against the retrieved documents; HR facts are deterministic
            # values from the database and just need their table sources.
            if policy_text:
                grounded = (
                    validate_output(policy_text, consultant_sources)
                    and verify_citations(policy_text, consultant_sources)
                )
            else:
                grounded = bool(response_text and hr_sources)
        if not grounded:
            if consultant_failed and not response_text:
                error = consultant_result.get("error") or {}
                reasons.append(f"Policy lookup failed ({error.get('type', 'unknown')}).")
            else:
                reasons.append("Response is empty, unsupported, or missing sources.")

        response = mask_pii(response_text)
        blocks = [
            [
                pair if pair == own_change
                else (mask_pii(en), mask_pii(ar_text) if ar_text else None)
                for pair in block
                for en, ar_text in [pair]
            ]
            for block in blocks
        ]
        # A personal-info confirmation must show the value the user just
        # typed, or it reads "email → [EMAIL]" and they can't check it.
        # Everything else (IBANs, policy text, other records) stays masked.
        if own_change:
            response = response.replace(mask_pii(own_change[0]), own_change[0], 1)

        if reasons:
            return _fail(reasons)

        submission_note = None
        if proposed_action:
            payload = proposed_action.get("payload") or {}
            action_type = proposed_action.get("action_type", "")
            risk = classify_risk(action_type, payload)
            submission_note = _submit_for_approval(
                target_employee_id,
                action_type,
                payload,
                risk,
                auto_approve=not requires_human_approval(risk),
            )
            if submission_note is None:
                return _fail(
                    ["Could not submit the request for approval. Please try again."]
                )

        final_response = response
        if submission_note:
            final_response = f"{response}\n\n{submission_note[0]}".strip()
            blocks.append([submission_note])

        return {
            "decision": "PASS",
            "reasons": [],
            "response": final_response,
            "response_blocks": blocks,
        }
