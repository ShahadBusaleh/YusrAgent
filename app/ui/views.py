from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Callable

import uuid

import httpx
import streamlit as st
import streamlit.components.v1 as components

from app.ui import api_client as api
from app.ui import exports
from app.ui import i18n
from app.ui import styles

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _is_rtl(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in (text or ""))


def _employee_suggestions() -> list[tuple[str, str]]:
    today = date.today()
    start = today + timedelta(days=21)
    end = start + timedelta(days=2)
    return [
        (
            i18n.t("sugg.request_leave.label"),
            i18n.t("sugg.request_leave.query", start=start.isoformat(), end=end.isoformat()),
        ),
        (i18n.t("sugg.check_balance.label"), i18n.t("sugg.check_balance.query")),
        (i18n.t("sugg.explain_policy.label"), i18n.t("sugg.explain_policy.query")),
    ]


def _manager_suggestions() -> list[tuple[str, str]]:
    return [
        (i18n.t("sugg.skill_gaps.label"), i18n.t("sugg.skill_gaps.query")),
        (i18n.t("sugg.pending_approvals.label"), i18n.t("sugg.pending_approvals.query")),
        (i18n.t("sugg.bank_policy.label"), i18n.t("sugg.bank_policy.query")),
    ]


def _chat_suggestions() -> list[tuple[str, str]]:
    role = (st.session_state.get("me") or {}).get("role")
    if role in {"hr_manager", "admin"}:
        return _manager_suggestions()
    return _employee_suggestions()


def _format_agent_text(payload: dict) -> str:
    response = payload.get("response")
    if isinstance(response, dict):
        # CAREER intent returns CareerAgent's structured dict, not plain
        # text — format it into something readable for a chat bubble
        # without touching the orchestrator/agent contract.
        if payload.get("intent") == "CAREER":
            employee = response.get("employee") or {}
            summary = response.get("summary") or {}
            lines = [
                f"**{employee.get('full_name') or 'Career development'}**",
                (
                    f"Completed: {summary.get('completed', 0)} · "
                    f"In progress: {summary.get('in_progress', 0)} · "
                    f"Recommended: {summary.get('recommended', 0)}"
                ),
            ]
            for item in response.get("skill_progress") or []:
                lines.append(
                    f"- {item.get('skill_name', 'Skill')}: "
                    f"{item.get('status', 'UNKNOWN')} "
                    f"({item.get('current_level', 0)}/{item.get('target_level', 0)})"
                )
            return "\n".join(lines)
        return str(response)

    text = str(response or "").strip()
    if text:
        return text

    # HTTP 200 with an empty response is a real, silent failure mode here:
    # classify_intent() swallows any LLM-call exception and falls back to
    # "OTHER" (see OrchestratorAgent.classify_intent), which skips every
    # agent branch — happens on every query while LLM_API_KEY is unset.
    # Never leave the bubble blank; say plainly that nothing came back.
    if str(payload.get("status") or "").upper() == "FAIL":
        return (
            "Sorry, I couldn't find a grounded answer for that. This usually "
            "means the connected AI/policy services aren't configured yet "
            "(check `LLM_API_KEY` in `.env`)."
        )
    return "I didn't get a response for that. Please try rephrasing your question."


def _agent_status_label(payload: dict) -> str:
    execution_order = payload.get("execution_order") or []
    intent = payload.get("intent")
    bits = []
    if "HR" in execution_order:
        bits.append("Checking employee data")
    if "CONSULTANT" in execution_order:
        bits.append("Searching HR policy")
    if intent == "CAREER":
        bits.append("Reviewing career development data")
    if intent == "GRIEVANCE":
        bits.append("Reviewing grievance workflow")
    label = " · ".join(bits) if bits else "Reviewing your request"
    if payload.get("status") == "PENDING_HR_REVIEW" or payload.get("hr_review"):
        label += " · Approval required"
    return label


def _call_agent(query: str, identity_visible: bool | None = None) -> dict | None:
    """POST /agent/query behind a small collapsible status. Appends an error
    message to chat_history and returns None on any failure."""
    status = st.status("Thinking...", expanded=True)
    api.refresh_session()

    try:
        response = api.request(
            "POST",
            "/agent/query",
            json={"query": query, "identity_visible": identity_visible},
            timeout=120.0,
        )
    except httpx.HTTPError:
        status.update(label="Could not reach Yusor", state="error", expanded=False)
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": "Could not reach the Yusor API. Please try again in a moment.",
                "sources": [],
            }
        )
        return None

    if response.status_code == 501:
        try:
            detail = response.json().get("detail", "Manual implementation pending")
        except Exception:
            detail = "Manual implementation pending"
        status.update(label="Not available yet", state="error", expanded=False)
        st.session_state.chat_history.append(
            {"role": "assistant", "content": detail, "sources": []}
        )
        return None

    if response.status_code == 401:
        status.update(label="Session expired", state="error", expanded=False)
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": "Your session expired. Sign out, sign in again, then resubmit.",
                "sources": [],
            }
        )
        return None

    try:
        payload = api.raise_for_api(response)
    except RuntimeError as exc:
        status.update(label="Request failed", state="error", expanded=False)
        st.session_state.chat_history.append(
            {"role": "assistant", "content": str(exc), "sources": []}
        )
        return None

    if not isinstance(payload, dict):
        status.update(label="Unexpected response", state="error", expanded=False)
        st.session_state.chat_history.append(
            {"role": "assistant", "content": "Unexpected agent response.", "sources": []}
        )
        return None

    status.update(label=_agent_status_label(payload), state="complete", expanded=False)
    return payload


def _submit_query(query: str) -> None:
    query = query.strip()
    if not query:
        return
    st.session_state.chat_history.append({"role": "user", "content": query, "sources": []})
    payload = _call_agent(query)
    if payload is None:
        return
    if payload.get("intent") == "GRIEVANCE" and payload.get("needs_identity_choice"):
        st.session_state["chat_pending_identity_query"] = query
        return
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": _format_agent_text(payload),
            "sources": payload.get("sources") or [],
        }
    )


def _resolve_identity_choice(identity_visible: bool) -> None:
    query = st.session_state.pop("chat_pending_identity_query", None)
    if not query:
        return
    payload = _call_agent(query, identity_visible=identity_visible)
    if payload is None:
        return
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": _format_agent_text(payload),
            "sources": payload.get("sources") or [],
        }
    )


# ---------------------------------------------------------------------------
# Phase 4: "Onboarding & Offboarding" page (HR staff only). Each form builds
# a structured "key: value" query and sends it through the normal
# POST /agent/query path (_call_agent) — HR agent parses it, Manager files it
# as a HIGH-risk pending approval. Nothing is written until an HR manager
# approves.
# ---------------------------------------------------------------------------

_STAFFING_ROLES = {"hr_specialist", "hr_manager", "admin"}
# Same values as app.agents.hr_agent.TERMINATION_TYPES.
_TERMINATION_TYPES = (
    "termination_by_employer",
    "article_80",
    "resignation",
    "end_of_contract",
    "mutual_agreement",
    "retirement",
    "force_majeure",
)
_EMPLOYMENT_TYPES = ("Full-time", "Contract", "Part-time", "Temporary")
_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿ]")
# Manager's submission note: "... submitted for approval (proposal PA00018)."
_SUBMITTED_RE = re.compile(r"submitted for approval \(proposal (PA\d+)\)")
_FIELD_BREAK_RE = re.compile(r"\s*[;\r\n]+\s*")


def _can_manage_staff() -> bool:
    return (st.session_state.get("me") or {}).get("role") in _STAFFING_ROLES


def _reset_staff_form(prefix: str) -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith(prefix):
            st.session_state.pop(key, None)


def _submit_staffing(
    query: str,
    prefix: str,
    after_submit: Callable[[str], str | None] | None = None,
) -> None:
    """Send through the same _call_agent -> POST /agent/query path as Ask
    Yusor, then show the outcome on this page (flash survives the rerun).
    `after_submit(proposal_id)` runs on success and may return a warning."""
    history = st.session_state.setdefault("chat_history", [])
    before = len(history)
    payload = _call_agent(query)
    if payload is None:
        # _call_agent reports failures into the chat history; they belong
        # on this page instead.
        failures = history[before:]
        del history[before:]
        detail = failures[-1]["content"] if failures else "Request failed."
        st.session_state["staff_flash"] = [("error", detail)]
    else:
        response = _format_agent_text(payload)
        submitted = _SUBMITTED_RE.search(response)
        if payload.get("status") == "PASS" and submitted:
            flash = [
                (
                    "success",
                    i18n.t("staffing.submitted", inbox=i18n.t("nav.approvals"), proposal=submitted.group(1)),
                )
            ]
            warning = after_submit(submitted.group(1)) if after_submit else None
            if warning:
                flash.append(("warning", warning))
            warnings = _submitted_warnings(submitted.group(1))
            if warnings:
                flash.append(("warning", warnings))
            st.session_state["staff_flash"] = flash
            _reset_staff_form(prefix)
        else:
            st.session_state["staff_flash"] = [("warning", response)]
    st.rerun()


def _staffing_query(header: str, fields: list[tuple[str, object]]) -> str:
    """One "key: value" pair per field; ";" and new lines inside a value
    would start a new field in the HR parser, so they become commas."""
    pairs = [
        f"{key}: {_FIELD_BREAK_RE.sub(', ', str(value)).strip()}"
        for key, value in fields
        if value not in (None, "")
    ]
    return header + "\n" + "; ".join(pairs)


def _load_departments() -> list[dict] | None:
    """Real departments, or None when this role can't read the list
    (GET /experience-gap/departments is hr_manager/admin only)."""
    if "staff_departments" not in st.session_state:
        try:
            data = api.raise_for_api(api.request("GET", "/experience-gap/departments")) or {}
            st.session_state["staff_departments"] = data.get("departments") or []
        except RuntimeError:
            st.session_state["staff_departments"] = None
    return st.session_state["staff_departments"]


def _department_names_ar() -> dict[str, str]:
    """department_id -> department_name_ar (GET /records/facets, which the
    staffing roles can read). Empty when unavailable."""
    if "dept_names_ar" not in st.session_state:
        try:
            facets = api.raise_for_api(api.request("GET", "/records/facets")) or {}
        except RuntimeError:
            return {}
        st.session_state["dept_names_ar"] = {
            d["department_id"]: d.get("department_name_ar")
            for d in facets.get("departments") or []
            if d.get("department_name_ar")
        }
    return st.session_state["dept_names_ar"]


def _department_label(department_id: str | None, name_en: str | None, name_ar: str | None = None) -> str:
    """Arabic department name in the Arabic UI, English otherwise."""
    if i18n.is_rtl():
        name_ar = name_ar or _department_names_ar().get(department_id or "")
        if name_ar:
            return name_ar
    return name_en or department_id or ""


def _open_terminations() -> dict[str, dict]:
    """employee_id -> the termination still in progress (pending approval, or
    approved and not yet finalized), from GET /proposed-actions. This is the
    UI's source for "Notice period until <date>": the employee API doesn't
    return termination_date, and the approved proposal carries the same date
    _sync_termination writes to employees.termination_date while the
    employee stays Active."""
    try:
        items = api.raise_for_api(api.request("GET", "/proposed-actions")) or []
    except RuntimeError:
        return {}
    open_terms: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:  # newest first
        payload = item.get("payload_json")
        payload = payload if isinstance(payload, dict) else {}
        if (
            item.get("action_type") != "termination"
            or item.get("status") not in ("pending_approval", "approved")
            or payload.get("finalized_at")
        ):
            continue
        employee_id = payload.get("employee_id")
        if employee_id and employee_id not in open_terms:
            open_terms[employee_id] = {
                "proposal_id": item.get("proposal_id"),
                "status": item.get("status"),
                "termination_date": payload.get("termination_date"),
            }
    return open_terms


def _notice_until(open_terms: dict, employee_id: str | None) -> str | None:
    term = open_terms.get(employee_id or "")
    return term["termination_date"] if term and term["status"] == "approved" else None


def notice_period_until(employee_id: str | None) -> str | None:
    """Last working day if this employee is in an approved notice period."""
    return _notice_until(_open_terminations(), employee_id)


def _employee_picker(
    label: str,
    key: str,
    *,
    department_id: str | None = None,
    optional: bool = False,
    exclude_id: str | None = None,
    open_terms: dict | None = None,
) -> dict | None:
    """Search active employees through GET /employees (max 50 per call)."""
    search = st.text_input(i18n.t("staff.search"), key=f"{key}_q")
    params = {}
    if search.strip():
        params["q"] = search.strip()
    if department_id:
        params["department_id"] = department_id
    if not params:
        st.caption(i18n.t("staff.search_hint"))
        return None
    try:
        rows = api.raise_for_api(api.request("GET", "/employees", params=params)) or []
    except RuntimeError as exc:
        st.error(str(exc))
        return None
    rows = [
        r
        for r in rows
        if r.get("employment_status") == "Active" and r.get("employee_id") != exclude_id
    ]
    if not rows:
        st.caption(i18n.t("staff.no_matches"))
        return None
    by_id = {r["employee_id"]: r for r in rows}
    options = ([None] if optional else []) + list(by_id)

    def _label(eid: str | None) -> str:
        if eid is None:
            return i18n.t("staff.none")
        text = f"{eid} — {by_id[eid].get('full_name') or ''} — {by_id[eid].get('job_title') or ''}"
        until = _notice_until(open_terms or {}, eid)
        return f"{text} · {i18n.t('notice.until', date=_friendly_when(until))}" if until else text

    chosen = st.selectbox(label, options, key=f"{key}_pick", format_func=_label)
    return by_id.get(chosen)


def _english_only_error(fields: list[tuple[str, str]]) -> bool:
    """Arabic text would send the whole query through the LLM translation
    step, which may rewrite the keys; records are stored in English."""
    for label, value in fields:
        if _ARABIC_RE.search(value or ""):
            st.error(i18n.t("staff.english_only", field=label))
            return True
    return False


# parse-cv field -> new-hire form widget key. Salary, department, manager and
# hire date are never taken from a CV.
_CV_FORM_KEYS = {
    "full_name": "hire_full_name",
    "suggested_job_title": "hire_job_title",
    "gender": "hire_gender",
    "nationality": "hire_nationality",
    "email": "hire_email",
    "mobile": "hire_mobile",
}


def _render_cv_prefill() -> None:
    """Upload a CV PDF -> POST /onboarding/parse-cv -> pre-fill the form.
    Runs before the form's widgets, so their session_state can be set."""
    uploaded = st.file_uploader(i18n.t("cv.upload_label"), type="pdf", key="hire_cv_upload")
    if uploaded is None:
        for key in ("hire_cv_file_id", "hire_cv_bytes", "hire_cv_name", "hire_cv_notice"):
            st.session_state.pop(key, None)
        return

    if uploaded.file_id != st.session_state.get("hire_cv_file_id"):
        st.session_state["hire_cv_file_id"] = uploaded.file_id
        data = uploaded.getvalue()
        with st.spinner(i18n.t("cv.reading")):
            try:
                response = api.request(
                    "POST",
                    "/onboarding/parse-cv",
                    files={"cv": (uploaded.name, data, "application/pdf")},
                    timeout=120.0,
                )
            except httpx.HTTPError:
                response = None

        if response is not None and response.status_code == 200:
            fields = (response.json() or {}).get("fields") or {}
            for field, form_key in _CV_FORM_KEYS.items():
                value = fields.get(field)
                if value and (field != "gender" or value in ("Female", "Male")):
                    st.session_state[form_key] = value
            st.session_state["hire_cv_notice"] = ("info", i18n.t("cv.filled"))
        elif response is not None and response.status_code == 422:
            st.session_state["hire_cv_notice"] = ("warning", i18n.t("cv.unreadable"))
        else:
            try:
                detail = api.raise_for_api(response) if response is not None else None
            except RuntimeError as exc:
                detail = str(exc)
            st.session_state["hire_cv_notice"] = ("error", str(detail or i18n.t("cv.unavailable")))

        # Keep the file to attach to the request after it is submitted —
        # only when it has readable text.
        readable = response is not None and response.status_code in (200, 502, 503)
        st.session_state["hire_cv_bytes"] = data if readable else None
        st.session_state["hire_cv_name"] = uploaded.name if readable else None

    notice = st.session_state.get("hire_cv_notice")
    if notice:
        kind, text = notice
        {"info": st.info, "warning": st.warning}.get(kind, st.error)(text)


def _attach_cv(proposal_id: str) -> str | None:
    """Attach the uploaded CV's text to the just-submitted new_hire request
    (POST /onboarding/proposals/{id}/cv). Returns a warning on failure."""
    data = st.session_state.get("hire_cv_bytes")
    if not data:
        return None
    try:
        api.raise_for_api(
            api.request(
                "POST",
                f"/onboarding/proposals/{proposal_id}/cv",
                files={"cv": (st.session_state.get("hire_cv_name") or "cv.pdf", data, "application/pdf")},
                timeout=60.0,
            )
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        return i18n.t("cv.attach_failed", detail=str(exc))
    return None


def _pct(ratio) -> str:
    return f"{float(ratio or 0):.0%}"


def _load_salary_scale() -> dict | None:
    """GET /onboarding/salary-scale, kept until the form is reset."""
    if "hire_scale_cache" not in st.session_state:
        try:
            st.session_state["hire_scale_cache"] = api.raise_for_api(
                api.request("GET", "/onboarding/salary-scale")
            ) or {}
        except RuntimeError as exc:
            st.warning(i18n.t("staff.scale_unavailable", detail=str(exc)))
            return None
    return st.session_state["hire_scale_cache"]


def _basic_floor_error(scale: dict | None, basic: float) -> str | None:
    """Localized copy of the HR agent's basic-salary floor block."""
    if not scale or basic <= 0 or basic >= float(scale.get("basic_floor") or 0):
        return None
    return i18n.t(
        "staff.block_basic_floor",
        floor=_money(scale.get("basic_floor")),
        share=f"{float(scale.get('basic_floor_share') or 0):g}",
        lowest=_money(scale.get("lowest_basic")),
    )


def _hire_checks(job_grade: str, nationality: str, basic: float, housing: float, transport: float) -> dict:
    try:
        return api.raise_for_api(
            api.request(
                "POST",
                "/onboarding/hire-checks",
                json={
                    "job_grade": job_grade,
                    "nationality": nationality,
                    "basic_salary": basic,
                    "housing_allowance": housing,
                    "transport_allowance": transport,
                },
            )
        ) or {}
    except RuntimeError as exc:
        return {"problems": [str(exc)], "warnings": []}


def _warning_text(warning: dict) -> str:
    """A new-hire / end-of-service warning in the interface language."""
    code = str(warning.get("code") or "")
    if code == "nitaqat_half":
        return i18n.t(
            "warn.nitaqat_half",
            wage=_money(warning.get("wage")),
            threshold=f"{float(warning.get('threshold') or 0):,.0f}",
        )
    if code == "grade_range":
        return i18n.t(
            "warn.grade_range",
            basic=_money(warning.get("basic")),
            grade=warning.get("grade"),
            min=_money(warning.get("min")),
            max=_money(warning.get("max")),
            n=warning.get("employees"),
        )
    if code in ("housing_ratio", "transport_ratio"):
        basis = (
            i18n.t("warn.basis_grade", grade=warning.get("grade"))
            if warning.get("basis") == "grade"
            else i18n.t("warn.basis_company")
        )
        return i18n.t(
            f"warn.{code}",
            ratio=_pct(warning.get("ratio")),
            basis=basis,
            typical=_pct(warning.get("typical")),
            amount=_money(warning.get("typical_amount")),
        )
    if code == "reason_review_unavailable":
        return i18n.t("warn.reason_review_unavailable")
    if warning.get("source") == "llm" and code.startswith("reason_"):
        verdict = code.removeprefix("reason_")
        text = (warning.get("text_ar") if i18n.is_rtl() else None) or warning.get("explanation") or warning.get("text")
        out = i18n.t("warn.ai_review", verdict=i18n.t(f"warn.verdict_{verdict}"), text=text or "—")
        if warning.get("suggested_type"):
            out += " " + i18n.t("warn.suggested_type", type=i18n.t(f"term.{warning['suggested_type']}"))
        return out
    return str(warning.get("text") or "")


def _render_request_warnings(payload: dict, *, for_approver: bool = False) -> None:
    """Warnings saved on a new_hire / end-of-service request."""
    warnings = [w for w in (payload or {}).get("warnings") or [] if isinstance(w, dict)]
    if not warnings:
        return
    st.markdown(f"**{i18n.t('warn.title')}**")
    for warning in warnings:
        st.warning(_warning_text(warning))
    if for_approver:
        st.caption(i18n.t("warn.approve_hint"))


def _submitted_warnings(proposal_id: str) -> str | None:
    """Flash text listing the warnings saved on a just-submitted request."""
    try:
        record = api.raise_for_api(api.request("GET", f"/records/{proposal_id}")) or {}
    except RuntimeError:
        return None
    warnings = [w for w in (record.get("payload") or {}).get("warnings") or [] if isinstance(w, dict)]
    if not warnings:
        return None
    return i18n.t("warn.submitted", n=len(warnings)) + "\n\n" + "\n".join(
        f"- {_warning_text(w)}" for w in warnings
    )


def _render_new_hire_form(open_terms: dict) -> None:
    with st.container(key="staff_card_hire"):
        _render_cv_prefill()
        st.session_state.setdefault("hire_nationality", "Saudi")
        c1, c2 = st.columns(2)
        full_name = c1.text_input(i18n.t("staff.full_name"), key="hire_full_name")
        job_title = c2.text_input(i18n.t("staff.job_title"), key="hire_job_title")
        gender = c1.selectbox(i18n.t("staff.gender"), ["Female", "Male"], key="hire_gender")
        nationality = c2.text_input(i18n.t("staff.nationality"), key="hire_nationality")
        email = c1.text_input(i18n.t("staff.email"), key="hire_email")
        mobile = c2.text_input(i18n.t("staff.mobile"), key="hire_mobile")

        departments = _load_departments()
        department_id = None
        if departments:
            names = {d["department_id"]: _department_label(d["department_id"], d.get("department_name")) for d in departments}
            department_id = st.selectbox(
                i18n.t("staff.department"),
                list(names),
                key="hire_department",
                format_func=lambda dep_id: f"{names[dep_id]} ({dep_id})",
            )
            manager = _employee_picker(
                i18n.t("staff.manager_optional"),
                "hire_manager",
                department_id=department_id,
                optional=True,
                open_terms=open_terms,
            )
        else:
            manager = _employee_picker(i18n.t("staff.manager"), "hire_manager", open_terms=open_terms)
            if manager:
                department_id = manager.get("department_id")
                st.caption(
                    i18n.t(
                        "staff.department_from_manager",
                        department=f"{_department_label(department_id, manager.get('department_name'))} ({department_id})",
                    )
                )
        manager_until = _notice_until(open_terms, (manager or {}).get("employee_id"))
        if manager_until:
            st.warning(i18n.t("notice.manager_warning", date=_friendly_when(manager_until)))

        c3, c4, c5 = st.columns(3)
        employment_type = c3.selectbox(
            i18n.t("staff.employment_type"), _EMPLOYMENT_TYPES, key="hire_employment_type"
        )
        hire_date = c4.date_input(
            i18n.t("staff.hire_date"), value=date.today(), format="DD-MM-YYYY", key="hire_date"
        )
        scale = _load_salary_scale()
        grades = (scale or {}).get("grades") or {}
        job_grade = c5.selectbox(
            i18n.t("staff.job_grade"),
            [None, *grades],
            key="hire_job_grade",
            format_func=lambda g: "—" if g is None else g,
        )
        band = grades.get(job_grade)
        if band:
            st.caption(
                i18n.t(
                    "staff.grade_range",
                    grade=job_grade,
                    min=_money(band["min_basic"]),
                    max=_money(band["max_basic"]),
                    n=band["employees"],
                    housing=_pct(band["housing_ratio"]),
                    transport=_pct(band["transport_ratio"]),
                )
            )
        s1, s2, s3 = st.columns(3)
        basic = s1.number_input(i18n.t("staff.basic_salary"), min_value=0.0, step=100.0, key="hire_basic")
        housing = s2.number_input(i18n.t("staff.housing_allowance"), min_value=0.0, step=100.0, key="hire_housing")
        transport = s3.number_input(i18n.t("staff.transport_allowance"), min_value=0.0, step=50.0, key="hire_transport")

        floor_error = _basic_floor_error(scale, basic)
        if floor_error:
            st.error(floor_error)
        elif basic > 0 and job_grade:
            # Same HR-agent checks that run again on submit.
            checks = _hire_checks(job_grade, nationality, basic, housing, transport)
            if checks.get("problems") or checks.get("warnings"):
                st.markdown(f"**{i18n.t('staff.checks_title')}**")
            for problem in checks.get("problems") or []:
                st.error(problem)
            for warning in checks.get("warnings") or []:
                st.warning(_warning_text(warning))

        submit = st.button(i18n.t("staff.submit"), type="primary", key="hire_submit")

    if not submit:
        return

    missing = [
        label
        for label, ok in (
            (i18n.t("staff.full_name"), full_name.strip()),
            (i18n.t("staff.job_title"), job_title.strip()),
            (i18n.t("staff.department"), department_id),
            (i18n.t("staff.job_grade"), job_grade),
            (i18n.t("staff.basic_salary"), basic > 0),
        )
        if not ok
    ]
    if missing:
        st.error(i18n.t("staff.required", fields=", ".join(missing)))
        return
    if floor_error:
        st.error(floor_error)
        return
    if _english_only_error(
        [
            (i18n.t("staff.full_name"), full_name),
            (i18n.t("staff.job_title"), job_title),
            (i18n.t("staff.nationality"), nationality),
        ]
    ):
        return

    query = _staffing_query(
        "Hire new employee",
        [
            ("full_name", full_name.strip()),
            ("gender", gender),
            ("nationality", nationality.strip()),
            ("email", email.strip()),
            ("mobile", mobile.strip()),
            ("department_id", department_id),
            ("job_title", job_title.strip()),
            ("job_grade", job_grade),
            ("manager_id", (manager or {}).get("employee_id")),
            ("employment_type", employment_type),
            ("hire_date", hire_date.strftime("%d-%m-%Y")),
            ("basic_salary", f"{basic:g}"),
            ("housing_allowance", f"{housing:g}"),
            ("transport_allowance", f"{transport:g}"),
        ],
    )
    _submit_staffing(query, "hire_", after_submit=_attach_cv)


# Art. 75 notice for a monthly-paid employee — same values as
# app.agents.hr_agent._NOTICE_DAYS, which re-checks them on submit.
_NOTICE_DAYS = {"resignation": 30, "termination_by_employer": 60}


# Same value as app.agents.hr_agent.REASON_MIN_CHARS, re-checked on submit.
_REASON_MIN_CHARS = 20


def _load_article_80() -> dict | None:
    """GET /onboarding/article-80: grounds + required procedures, parsed
    from our LAW075 row. Kept until the form is reset."""
    if "term_art80_cache" not in st.session_state:
        try:
            st.session_state["term_art80_cache"] = api.raise_for_api(
                api.request("GET", "/onboarding/article-80")
            ) or {}
        except RuntimeError as exc:
            st.error(i18n.t("art80.unavailable", detail=str(exc)))
            return None
    return st.session_state["term_art80_cache"]


def _render_article_80_fields(today: date) -> tuple[list[tuple[str, object]], list[str]]:
    """Ground dropdown, details and the article's procedural confirmations.
    Returns (query fields, localized errors to show on submit)."""
    article = _load_article_80()
    if not article:
        return [], [i18n.t("art80.missing")]
    grounds = {g["number"]: g for g in article.get("grounds") or []}
    number = st.selectbox(
        i18n.t("art80.ground"),
        [None, *grounds],
        key="term_art80_ground",
        format_func=lambda n: "—" if n is None else i18n.t("art80.ground_option", n=n, text=grounds[n]["text"]),
    )
    st.caption(i18n.t("art80.source", law_id=article.get("law_id"), article=article.get("article")))
    details = st.text_area(i18n.t("art80.details", n=_REASON_MIN_CHARS), key="term_art80_details")

    errors: list[str] = []
    fields: list[tuple[str, object]] = [("article_80_details", details.strip())]
    if number is None:
        errors.append(i18n.t("art80.missing"))
    else:
        fields.insert(0, ("article_80_ground", number))
    if len(details.strip()) < _REASON_MIN_CHARS:
        errors.append(
            i18n.t("staff.reason_short", field=i18n.t("art80.details_label"), n=_REASON_MIN_CHARS, have=len(details.strip()))
        )

    unconfirmed = []
    requirements = grounds[number]["requirements"] if number is not None else []
    if requirements:
        st.markdown(f"**{i18n.t('art80.procedures')}**")
    for requirement in requirements:
        kind = requirement["kind"]
        label = i18n.t(f"art80.req_{kind}")
        left, right = st.columns([3, 1])
        confirmed = left.checkbox(label, key=f"term_art80_ok_{number}_{kind}")
        left.caption(f"“{requirement['text']}”")
        when = None
        if requirement.get("needs_date"):
            when = right.date_input(
                i18n.t("art80.date"),
                value=None,
                max_value=today,
                format="DD-MM-YYYY",
                key=f"term_art80_date_{number}_{kind}",
            )
        if not confirmed or (requirement.get("needs_date") and when is None):
            unconfirmed.append(label)
        elif when is not None:
            fields.append((requirement["field"], when.strftime("%d-%m-%Y")))
        else:
            fields.append((requirement["field"], "yes"))
    if unconfirmed:
        errors.append(i18n.t("art80.unconfirmed", items="; ".join(unconfirmed)))
    return fields, errors


def _render_termination_form(open_terms: dict) -> None:
    me = st.session_state.get("me") or {}
    today = date.today()
    waived, waiver_note = False, ""
    with st.container(key="staff_card_term"):
        employee = _employee_picker(
            i18n.t("staff.employee"),
            "term_employee",
            exclude_id=me.get("employee_id"),
            open_terms=open_terms,
        )
        existing = open_terms.get((employee or {}).get("employee_id") or "")
        if existing:
            # One termination at a time — show the one already in progress.
            key = "notice.existing_pending" if existing["status"] == "pending_approval" else "notice.existing_approved"
            st.info(
                i18n.t(
                    key,
                    employee=(employee or {}).get("employee_id"),
                    proposal=existing["proposal_id"],
                    date=_friendly_when(existing["termination_date"]) or "—",
                )
            )
        c1, c2 = st.columns(2)
        termination_type = c1.selectbox(
            i18n.t("staff.termination_type"),
            [None, *_TERMINATION_TYPES],
            key="term_type",
            format_func=lambda v: "—" if v is None else i18n.t(f"term.{v}"),
        )
        notice_days = _NOTICE_DAYS.get(termination_type, 0)
        earliest = today + timedelta(days=notice_days)
        # Re-default the last working day whenever the type changes: end of
        # the notice period for notice types, today otherwise.
        if "term_date" not in st.session_state or st.session_state.get("term_date_type") != termination_type:
            st.session_state["term_date_type"] = termination_type
            st.session_state["term_date"] = earliest
        if termination_type == "article_80":
            st.session_state["term_date"] = today
        termination_date = c2.date_input(
            i18n.t("staff.termination_date"),
            format="DD-MM-YYYY",
            key="term_date",
            disabled=termination_type == "article_80",
        )
        if termination_type == "article_80":
            st.caption(i18n.t("notice.art80_immediate"))
        elif notice_days:
            st.caption(i18n.t("notice.required", days=notice_days, date=_friendly_when(earliest.isoformat())))
            waived = st.checkbox(i18n.t("notice.waived"), key="term_notice_waived")
            if waived:
                waiver_note = st.text_area(i18n.t("notice.waiver_note"), key="term_waiver_note")
        art80_fields: list[tuple[str, object]] = []
        art80_errors: list[str] = []
        if termination_type == "article_80":
            art80_fields, art80_errors = _render_article_80_fields(today)
            reason = ""
        else:
            reason = st.text_area(i18n.t("staff.reason"), key="term_reason")

        submit = st.button(
            i18n.t("staff.submit"), type="primary", key="term_submit", disabled=bool(existing)
        )

    if not submit:
        return

    missing = [
        label
        for label, ok in (
            (i18n.t("staff.employee"), employee),
            (i18n.t("staff.termination_type"), termination_type),
            (i18n.t("staff.reason"), reason.strip() or termination_type == "article_80"),
        )
        if not ok
    ]
    if missing:
        st.error(i18n.t("staff.required", fields=", ".join(missing)))
        return
    if termination_type != "article_80" and len(reason.strip()) < _REASON_MIN_CHARS:
        st.error(
            i18n.t("staff.reason_short", field=i18n.t("staff.reason"), n=_REASON_MIN_CHARS, have=len(reason.strip()))
        )
        return
    if art80_errors:
        for error in art80_errors:
            st.error(error)
        return
    early = bool(notice_days) and termination_date < earliest
    if early and not waived:
        st.error(i18n.t("notice.too_early", days=notice_days, date=_friendly_when(earliest.isoformat())))
        return
    if early and not waiver_note.strip():
        st.error(i18n.t("notice.note_required"))
        return
    art80_details = dict(art80_fields).get("article_80_details") or ""
    if _english_only_error(
        [
            (i18n.t("staff.reason"), reason),
            (i18n.t("notice.waiver_note"), waiver_note),
            (i18n.t("art80.details_label"), art80_details),
        ]
    ):
        return

    query = _staffing_query(
        "Terminate employee",
        [
            ("employee_id", employee.get("employee_id")),
            ("termination_type", termination_type),
            ("termination_date", termination_date.strftime("%d-%m-%Y")),
            ("reason", reason.strip()),
            ("notice_waived", "yes" if early else None),
            ("notice_waiver_note", waiver_note.strip() if early else None),
            *art80_fields,
        ],
    )
    _submit_staffing(query, "term_")


def _notice_basis(notice: dict) -> str:
    required = notice.get("required_days")
    if required is None:
        return i18n.t("profile.notice_by_agreement")
    if required == 0:
        return i18n.t("profile.notice_na")
    if notice.get("waived"):
        return i18n.t(
            "settlement.notice_waived",
            given=notice.get("days_given"),
            required=required,
            note=notice.get("waiver_note") or "—",
        )
    return i18n.t("profile.notice_value", required=required, given=notice.get("days_given"))


def _render_final_settlement(settlement: dict) -> None:
    """Breakdown of the settlement frozen by _sync_termination at approval."""
    st.markdown(f"**{i18n.t('settlement.title')}**")
    if settlement.get("error"):
        st.warning(settlement["error"])
        return

    service = settlement.get("service") or {}
    wage = settlement.get("wage_basis") or {}
    eos = settlement.get("end_of_service") or {}
    leave = settlement.get("unused_leave") or {}
    notice = settlement.get("notice") or {}
    components = wage.get("components") or {}
    component_labels = {
        "basic_salary_sar": i18n.t("settlement.basic"),
        "housing_allowance_sar": i18n.t("settlement.housing"),
        "transport_allowance_sar": i18n.t("settlement.transport"),
    }

    notice_basis = _notice_basis(notice)

    rows = [
        {
            "item": i18n.t("profile.service"),
            "basis": i18n.t(
                "profile.service_value",
                y=service.get("years"),
                m=service.get("months"),
                d=service.get("days"),
                total=service.get("total_days"),
            ),
            "amount": "",
            "article": _cite(service),
        },
        {
            "item": i18n.t("settlement.wage_basis"),
            "basis": " + ".join(
                f"{component_labels[key]} {_money(value)}"
                for key, value in components.items()
                if key in (wage.get("included") or [])
            ),
            "amount": _money(wage.get("monthly_wage")),
            "article": _cite(wage),
        },
        {
            "item": i18n.t("settlement.full_award"),
            "basis": i18n.t("settlement.full_award_basis"),
            "amount": _money(eos.get("full_award")),
            "article": _cite(eos.get("full_award_law")),
        },
        {
            "item": i18n.t("settlement.fraction"),
            "basis": i18n.t(f"term.{settlement.get('termination_type')}"),
            "amount": "× " + _ratio_text(eos.get("fraction")),
            "article": _cite(eos.get("fraction_law")),
        },
        *(
            [
                {
                    "item": i18n.t("art80.ground"),
                    "basis": f"({settlement['article_80'].get('ground_number')}) {settlement['article_80'].get('ground_text')}",
                    "amount": "",
                    "article": _cite(settlement["article_80"]),
                }
            ]
            if settlement.get("article_80")
            else []
        ),
        {
            "item": i18n.t("settlement.eos_amount"),
            "basis": "",
            "amount": _money(eos.get("amount")),
            "article": _cite(eos.get("fraction_law")),
        },
        {
            "item": i18n.t("profile.unused_leave"),
            "basis": i18n.t(
                "settlement.leave_basis",
                days=f"{float(leave.get('days') or 0):g}",
                daily=_money(leave.get("daily_wage")),
            ),
            "amount": _money(leave.get("amount")),
            "article": _cite(leave),
        },
        {
            "item": i18n.t("profile.notice"),
            "basis": notice_basis,
            "amount": _money(notice.get("adjustment")),
            "article": _cite(notice),
        },
        {
            "item": i18n.t("settlement.total"),
            "basis": "",
            "amount": _money(settlement.get("total")),
            "article": "",
        },
    ]
    styles.data_table(
        rows,
        [
            ("item", i18n.t("settlement.col_item")),
            ("basis", i18n.t("settlement.col_basis")),
            ("amount", i18n.t("settlement.col_amount")),
            ("article", i18n.t("settlement.col_article")),
        ],
        key=f"settlement_{settlement.get('frozen_at')}",
    )
    deadline = settlement.get("settlement_deadline") or {}
    st.caption(
        i18n.t(
            "settlement.meta",
            last_day=_friendly_when(settlement.get("termination_date")) or "—",
            deadline=_friendly_when(deadline.get("deadline")) or "—",
            deadline_cite=_cite(deadline),
            articles=", ".join(_cite(a) for a in settlement.get("articles") or []),
        )
    )
    st.caption(i18n.t("profile.disclaimer"))


_STAFFING_VIEWS = (("new_hire", "staffing.tab_hire"), ("termination", "staffing.tab_term"))


def _staffing_switcher() -> str:
    """Horizontal New hire / Termination switcher. The choice lives in
    session_state, so it survives the rerun after a submit (st.tabs in this
    Streamlit version always reopens on the first tab). Selected = primary
    button, the other = the project's secondary outline button."""
    current = st.session_state.get("staffing_view", "new_hire")
    cols = st.columns([1, 1, 3])
    for col, (view, label_key) in zip(cols, _STAFFING_VIEWS):
        with col:
            if view == current:
                st.button(i18n.t(label_key), key=f"staffing_view_{view}", type="primary", use_container_width=True)
            else:
                with st.container(key=f"yz_btn_secondary_staffing_{view}"):
                    if st.button(i18n.t(label_key), key=f"staffing_view_{view}", use_container_width=True):
                        st.session_state["staffing_view"] = view
                        st.rerun()
    return current


def _render_my_pending_line() -> None:
    """"Pending: N — open Records": my own submitted hires/terminations still
    waiting for a decision (GET /records?mine=1)."""
    try:
        data = api.raise_for_api(
            api.request(
                "GET",
                "/records",
                params={"type": "new_hire,termination", "status": "pending", "mine": "true", "page_size": 1},
            )
        ) or {}
    except RuntimeError as exc:
        st.caption(str(exc))
        return
    left, right = st.columns([3, 1])
    left.markdown(i18n.t("staffing.pending_line", n=int(data.get("total") or 0)))
    with right, st.container(key="yz_btn_secondary_open_records"):
        if st.button(i18n.t("staffing.open_records"), key="staff_open_records", use_container_width=True):
            _preset_records_filters(types=["new_hire", "termination"], status="pending", mine=True)
            st.session_state["_yz_pending_nav"] = "Records"
            st.rerun()


def page_staffing() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("staffing.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("staffing.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not _can_manage_staff():
        st.error(i18n.t("staffing.not_allowed"))
        return

    for kind, text in st.session_state.pop("staff_flash", None) or []:
        {"success": st.success, "warning": st.warning}.get(kind, st.error)(text)

    view = _staffing_switcher()
    open_terms = _open_terminations()
    if view == "termination":
        _render_termination_form(open_terms)
    else:
        _render_new_hire_form(open_terms)

    _render_my_pending_line()


# ---------------------------------------------------------------------------
# Records (Phase 4): read-only archive of every request, for HR staff.
# GET /records (+ /facets, /export, /{proposal_id}); IBANs arrive masked.
# The Excel / PDF files are rendered here (app/ui/exports.py) so they use
# the interface language.
# ---------------------------------------------------------------------------

_RECORD_STATUSES = ("", "pending", "approved", "rejected")
_REC_FILTER_KEYS = ("rec_types", "rec_status", "rec_dept", "rec_from", "rec_to", "rec_q", "rec_mine")


def _preset_records_filters(*, types=None, status="", mine=False) -> None:
    """Open Records with filters set (called before its widgets exist)."""
    for key in _REC_FILTER_KEYS + ("rec_page", "rec_open", "rec_xlsx", "rec_pdf"):
        st.session_state.pop(key, None)
    st.session_state["rec_types"] = list(types or [])
    st.session_state["rec_status"] = status
    st.session_state["rec_mine"] = mine


def _record_type_label(action_type: str) -> str:
    key = f"records.type.{action_type}"
    label = i18n.t(key)
    return label if label != key else str(action_type or "—").replace("_", " ").capitalize()


def _record_status(record: dict) -> tuple[str, str]:
    """(pill key, label) — terminations show their separation state."""
    sep = record.get("separation") or {}
    if sep.get("state") == "notice":
        return f"notice_{sep.get('until')}", i18n.t("records.in_notice", date=_friendly_when(sep.get("until")) or "—")
    if sep.get("state") == "finalized":
        return "finalized", i18n.t("records.finalized")
    return str(record.get("status") or ""), ""


def _records_params() -> dict:
    params = {}
    if st.session_state.get("rec_types"):
        params["type"] = ",".join(st.session_state["rec_types"])
    if st.session_state.get("rec_status"):
        params["status"] = st.session_state["rec_status"]
    if st.session_state.get("rec_dept"):
        params["department_id"] = st.session_state["rec_dept"]
    if st.session_state.get("rec_from"):
        params["date_from"] = st.session_state["rec_from"].isoformat()
    if st.session_state.get("rec_to"):
        params["date_to"] = st.session_state["rec_to"].isoformat()
    if (st.session_state.get("rec_q") or "").strip():
        params["q"] = st.session_state["rec_q"].strip()
    if st.session_state.get("rec_mine"):
        params["mine"] = "true"
    return params


def _facet_departments(facets: dict) -> dict[str, str]:
    return {
        d["department_id"]: _department_label(d["department_id"], d.get("department_name"), d.get("department_name_ar"))
        for d in facets.get("departments", [])
    }


def _record_department(record: dict) -> str:
    return _department_label(record.get("department_id"), record.get("department_name"), record.get("department_name_ar"))


def _record_status_text(record: dict) -> str:
    pill_key, pill_label = _record_status(record)
    if pill_label:
        return pill_label
    status = {"pending_approval": "pending"}.get(pill_key, pill_key)
    return i18n.t(f"records.status_{status}") if status in _RECORD_STATUSES else status


def _record_summary(record: dict) -> str:
    """Localized one-line summary for the exports (dates in UI format)."""
    payload = record.get("payload") or {}
    action_type = record.get("action_type")
    if action_type == "termination":
        termination_type = payload.get("termination_type")
        return (
            f"{i18n.t(f'term.{termination_type}') if termination_type else '—'} · "
            f"{i18n.t('staff.termination_date')}: {_friendly_when(payload.get('termination_date')) or '—'}"
        )
    if action_type == "new_hire":
        hire = payload.get("new_employee") or {}
        return f"{hire.get('job_title') or '—'} · {i18n.t('staff.hire_date')}: {_friendly_when(hire.get('hire_date')) or '—'}"
    if action_type == "regulation_update":
        from app.ui import regulations_view

        return regulations_view.summary_line(payload)
    return _localized_summary(action_type, payload) or record.get("summary") or ""


def _records_filter_lines(facets: dict) -> list[tuple[str, str]]:
    """The filters in effect, for the PDF report header."""
    ss = st.session_state
    comma = "، " if i18n.is_rtl() else ", "
    lines = []
    if ss.get("rec_types"):
        lines.append((i18n.t("records.f_type"), comma.join(_record_type_label(t) for t in ss["rec_types"])))
    if ss.get("rec_status"):
        lines.append((i18n.t("records.f_status"), i18n.t(f"records.status_{ss['rec_status']}")))
    if ss.get("rec_dept"):
        lines.append((i18n.t("records.f_department"), _facet_departments(facets).get(ss["rec_dept"], ss["rec_dept"])))
    if ss.get("rec_from"):
        lines.append((i18n.t("records.f_from"), _friendly_when(ss["rec_from"].isoformat())))
    if ss.get("rec_to"):
        lines.append((i18n.t("records.f_to"), _friendly_when(ss["rec_to"].isoformat())))
    if (ss.get("rec_q") or "").strip():
        lines.append((i18n.t("records.f_search"), ss["rec_q"].strip()))
    if ss.get("rec_mine"):
        lines.append((i18n.t("records.f_mine"), i18n.t("records.yes")))
    return lines or [("", i18n.t("records.report_no_filters"))]


def _build_records_export(kind: str, params: dict, facets: dict) -> bytes:
    """GET /records/export (every filtered record, IBANs masked) rendered as
    .xlsx or the branded PDF report, in the interface language."""
    data = api.raise_for_api(api.request("GET", "/records/export", params=params, timeout=120.0)) or {}
    items = data.get("items") or []
    # (row key, header, PDF width weight — 0 = Excel only)
    columns = [
        ("proposal_id", "records.col_id", 16),
        ("submitted", "records.col_submitted", 22),
        ("type", "records.col_type", 24),
        ("employee_id", "records.col_employee_id", 0),
        ("employee", "records.col_employee", 34),
        ("department", "records.col_department", 24),
        ("status", "records.col_status", 24),
        ("requested_by", "records.col_requested_by", 0),
        ("decided_by", "records.col_decided_by", 26),
        ("decided_at", "records.decided_at", 22),
        ("note", "records.note", 0),
        ("summary", "records.col_summary", 46),
    ]
    rows = []
    for record in items:
        subject = record.get("subject_name") or "—"
        if kind == "pdf" and record.get("subject_id"):
            subject = f"{subject} ({record['subject_id']})"
        rows.append(
            {
                "proposal_id": record.get("proposal_id"),
                "submitted": _friendly_when(record.get("submitted_at")),
                "type": _record_type_label(record.get("action_type")),
                "employee_id": record.get("subject_id"),
                "employee": subject,
                "department": _record_department(record),
                "status": _record_status_text(record),
                "requested_by": i18n.t("reg.system") if record.get("requested_by") == "system" else record.get("requested_by"),
                "decided_by": record.get("decided_by_name") or record.get("decided_by"),
                "decided_at": _friendly_when(record.get("decided_at")),
                "note": record.get("decision_note"),
                "summary": _record_summary(record),
            }
        )
    rtl = i18n.is_rtl()
    if kind == "xlsx":
        return exports.records_xlsx(
            [i18n.t(label) for _, label, _ in columns],
            [[row[key] for key, _, _ in columns] for row in rows],
            rtl=rtl,
            sheet_title=i18n.t("records.sheet"),
        )
    shown = [c for c in columns if c[2]]
    me = st.session_state.get("me") or {}
    now = datetime.now()
    return exports.records_pdf(
        title=i18n.t("records.report_title"),
        subtitle=i18n.t("records.report_count", total=len(rows)),
        filters_title=i18n.t("records.report_filters"),
        filters=_records_filter_lines(facets),
        generated=i18n.t(
            "records.report_generated",
            at=f"{_friendly_when(now.isoformat())} {now:%H:%M}",
            by=f"{me.get('full_name') or me.get('username') or '—'}"
            + (f" ({me['employee_id']})" if me.get("employee_id") else ""),
        ),
        headers=[i18n.t(label) for _, label, _ in shown],
        rows=[[row[key] or "—" for key, _, _ in shown] for row in rows],
        widths=[w for _, _, w in shown],
        page_label=i18n.t("records.report_page"),
        rtl=rtl,
    )


def _settlement_document(record: dict) -> dict:
    """Localized content of the final settlement PDF, from the settlement
    frozen at approval (payload.final_settlement) — no recalculation."""
    payload = record.get("payload") or {}
    settlement = payload.get("final_settlement") or {}
    rtl = i18n.is_rtl()
    sar = i18n.t("settlement.sar")

    def money(value) -> str:
        return f"{_money(value)} {sar}"

    def pick(en_key: str, ar_key: str) -> str | None:
        return (record.get(ar_key) if rtl else None) or record.get(en_key)

    service = settlement.get("service") or {}
    wage = settlement.get("wage_basis") or {}
    eos = settlement.get("end_of_service") or {}
    leave = settlement.get("unused_leave") or {}
    notice = settlement.get("notice") or {}
    deadline = settlement.get("settlement_deadline") or {}
    termination_type = settlement.get("termination_type") or payload.get("termination_type")
    type_label = i18n.t(f"term.{termination_type}") if termination_type else "—"
    component_labels = {
        "basic_salary_sar": i18n.t("settlement.basic"),
        "housing_allowance_sar": i18n.t("settlement.housing"),
        "transport_allowance_sar": i18n.t("settlement.transport"),
    }
    included = wage.get("included") or []
    wage_pairs = [
        (component_labels[key], money(value))
        for key, value in (wage.get("components") or {}).items()
        if key in included and key in component_labels
    ]
    wage_cite = _cite(wage)
    wage_pairs.append(
        (i18n.t("settlement.wage_basis"), money(wage.get("monthly_wage")) + (f" — {wage_cite}" if wage_cite else ""))
    )
    service_cite = _cite(service)
    ratio = _ratio_text(eos.get("fraction"))
    notice_basis = _notice_basis(notice)
    if notice.get("notice_given_on"):
        notice_basis += " · " + i18n.t("settlement.doc_notice_given_on", date=_friendly_when(notice["notice_given_on"]))
    who = record.get("decided_by")
    articles = ("، " if rtl else ", ").join(_cite(a) for a in settlement.get("articles") or [])
    return {
        "company": i18n.t("settlement.doc_company"),
        "company_sub": i18n.t("settlement.doc_company_sub"),
        "title": i18n.t("settlement.doc_title"),
        "ref": i18n.t("settlement.doc_ref", ref=record.get("proposal_id"), date=_friendly_when(date.today().isoformat())),
        "page_label": i18n.t("settlement.doc_page"),
        "sections": [
            {
                "title": i18n.t("settlement.doc_employee"),
                "pairs": [
                    (i18n.t("settlement.doc_name"), pick("subject_name", "subject_name_ar") or payload.get("employee_id")),
                    (i18n.t("settlement.doc_id"), record.get("subject_id") or payload.get("employee_id")),
                    (i18n.t("settlement.doc_job"), pick("job_title", "job_title_ar")),
                    (i18n.t("settlement.doc_department"), _record_department(record)),
                    (i18n.t("settlement.doc_nationality"), pick("nationality", "nationality_ar")),
                    (i18n.t("settlement.doc_type"), type_label),
                    *_article_80_rows(settlement.get("article_80") or payload.get("article_80")),
                ],
            },
            {
                "title": i18n.t("settlement.doc_service"),
                "pairs": [
                    (i18n.t("settlement.doc_hire"), _friendly_when(settlement.get("hire_date") or record.get("hire_date"))),
                    (i18n.t("settlement.doc_last_day"), _friendly_when(settlement.get("termination_date"))),
                    (
                        i18n.t("settlement.doc_length"),
                        i18n.t(
                            "profile.service_value",
                            y=service.get("years"), m=service.get("months"), d=service.get("days"),
                            total=service.get("total_days"),
                        ) + (f" — {service_cite}" if service_cite else ""),
                    ),
                ],
            },
            {"title": i18n.t("settlement.doc_wage"), "pairs": wage_pairs},
        ],
        "calc": {
            "title": i18n.t("settlement.doc_calc"),
            "headers": [
                i18n.t("settlement.col_item"),
                i18n.t("settlement.col_basis"),
                i18n.t("settlement.col_article"),
                i18n.t("settlement.col_amount"),
            ],
            "rows": [
                [i18n.t("settlement.full_award"), i18n.t("settlement.full_award_basis"),
                 _cite(eos.get("full_award_law")), _money(eos.get("full_award"))],
                [i18n.t("settlement.fraction"), type_label, _cite(eos.get("fraction_law")), "× " + ratio],
                [i18n.t("settlement.doc_eos"),
                 i18n.t("settlement.doc_eos_formula", full=_money(eos.get("full_award")), fraction=ratio,
                        amount=_money(eos.get("amount"))),
                 _cite(eos.get("fraction_law")), _money(eos.get("amount"))],
                [i18n.t("settlement.doc_leave"),
                 i18n.t("settlement.leave_basis", days=f"{float(leave.get('days') or 0):g}",
                        daily=_money(leave.get("daily_wage"))),
                 _cite(leave), _money(leave.get("amount"))],
                [i18n.t("settlement.doc_notice"), notice_basis, _cite(notice), _money(notice.get("adjustment"))],
                [i18n.t("settlement.doc_total"), "", "", _money(settlement.get("total"))],
            ],
        },
        "notes": [
            i18n.t("settlement.doc_pay_by", date=_friendly_when(deadline.get("deadline")) or "—",
                   cite=_cite(deadline) or "—"),
            i18n.t("settlement.doc_articles", articles=articles or "—"),
            i18n.t("profile.disclaimer"),
        ],
        "approval": {
            "title": i18n.t("settlement.doc_approval"),
            "pairs": [
                (i18n.t("settlement.doc_approved_by"), f"{record.get('decided_by_name') or who} ({who})" if who else "—"),
                (i18n.t("settlement.doc_decided"), _friendly_when(record.get("decided_at")) or "—"),
            ],
        },
        "signatures": {
            "ack": i18n.t("settlement.doc_ack"),
            "name": i18n.t("settlement.doc_sign_name"),
            "signature": i18n.t("settlement.doc_sign_signature"),
            "date": i18n.t("settlement.doc_sign_date"),
            "blocks": [i18n.t("settlement.doc_sign_hr"), i18n.t("settlement.doc_sign_employee")],
        },
    }


def _render_settlement_download(record: dict) -> None:
    settlement = (record.get("payload") or {}).get("final_settlement") or {}
    if record.get("status") != "approved" or not settlement or settlement.get("error"):
        return
    try:
        data = exports.settlement_pdf(_settlement_document(record), rtl=i18n.is_rtl())
    except RuntimeError as exc:  # no Arabic-capable font on this host
        st.warning(str(exc))
        return
    st.download_button(
        i18n.t("settlement.download"),
        data=data,
        file_name=f"settlement_{record.get('proposal_id')}_{i18n.get_lang()}.pdf",
        mime="application/pdf",
        key=f"rec_settlement_{record.get('proposal_id')}",
        type="primary",
    )


def _scroll_to(selector: str) -> None:
    """Smooth-scroll the Streamlit page to the first element matching
    `selector`. Streamlit scrolls inside its own container (not always the
    window), so the script walks up to the nearest scrollable ancestor. The
    nonce makes the iframe new on every call, so the script runs again
    even when the target is the same."""
    nonce = uuid.uuid4().hex
    script = f"""
<script>
/* {nonce} */
(function () {{
  const win = window.parent, doc = win.document;
  function scroller(el) {{
    for (let node = el.parentElement; node && node !== doc.body; node = node.parentElement) {{
      const oy = win.getComputedStyle(node).overflowY;
      if ((oy === "auto" || oy === "scroll") && node.scrollHeight > node.clientHeight + 1) return node;
    }}
    return null;
  }}
  function go(el) {{
    const box = scroller(el);
    if (box) {{
      const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 16;
      box.scrollTo({{ top: top, behavior: "smooth" }});
    }} else {{
      win.scrollTo({{ top: el.getBoundingClientRect().top + win.scrollY - 16, behavior: "smooth" }});
    }}
  }}
  let tries = 0;
  function attempt() {{
    const el = doc.querySelector({json.dumps(selector)});
    if (!el) {{ if (tries++ < 60) setTimeout(attempt, 50); return; }}
    go(el);
    /* Streamlit may still be laying out the page: re-aim once if the
       target moved. */
    setTimeout(function () {{
      const box = scroller(el);
      const top = el.getBoundingClientRect().top - (box ? box.getBoundingClientRect().top : 0);
      if (Math.abs(top - 16) > 40) go(el);
    }}, 900);
  }}
  attempt();
}})();
</script>
"""
    with st.container(key="yz_scroll_js"):
        components.html(script, height=0)


def _render_records_filters(facets: dict) -> None:
    types = list(dict.fromkeys(
        [*facets.get("types", []), *st.session_state.get("rec_types", [])]
    ))
    departments = _facet_departments(facets)
    with st.container(key="records_filters"):
        c1, c2, c3 = st.columns([2, 1, 1])
        c1.multiselect(
            i18n.t("records.f_type"), types, key="rec_types", format_func=_record_type_label,
            placeholder=i18n.t("records.all_types"),
        )
        c2.selectbox(
            i18n.t("records.f_status"), _RECORD_STATUSES, key="rec_status",
            format_func=lambda v: i18n.t(f"records.status_{v or 'all'}"),
        )
        c3.selectbox(
            i18n.t("records.f_department"), ["", *departments], key="rec_dept",
            format_func=lambda v: departments.get(v, i18n.t("records.all_departments")) if v else i18n.t("records.all_departments"),
        )
        d1, d2, d3 = st.columns([1, 1, 2])
        d1.date_input(i18n.t("records.f_from"), value=None, format="DD-MM-YYYY", key="rec_from")
        d2.date_input(i18n.t("records.f_to"), value=None, format="DD-MM-YYYY", key="rec_to")
        d3.text_input(i18n.t("records.f_search"), key="rec_q", placeholder=i18n.t("records.search_hint"))
        st.checkbox(i18n.t("records.f_mine"), key="rec_mine")


def _render_record_detail(proposal_id: str) -> None:
    try:
        record = api.raise_for_api(api.request("GET", f"/records/{proposal_id}")) or {}
    except RuntimeError as exc:
        st.error(str(exc))
        return
    payload = record.get("payload") or {}
    action_type = record.get("action_type") or ""
    subject = record.get("subject_name") or record.get("subject_id") or "—"

    st.markdown('<div id="yz-rec-detail"></div>', unsafe_allow_html=True)
    top_l, top_m, top_r = st.columns([4, 1.3, 1])
    with top_m, st.container(key="yz_btn_secondary_rec_back"):
        if st.button(i18n.t("records.back_to_list"), key="rec_back", use_container_width=True):
            st.session_state["rec_scroll"] = "#yz-rec-list"
            st.rerun()
    with top_l:
        st.markdown(f"**{html.escape(_record_type_label(action_type))} — {html.escape(str(subject))}**")
        pill_key, pill_label = _record_status(record)
        pills = {**styles.status_pills(), "pending_approval": styles.status_pills()["pending"]}
        if pill_label:
            pills[pill_key] = (pill_label, "◷" if pill_key.startswith("notice_") else "■", "#E8EEF6", "#2F4A6B")
        st.markdown(styles.status_pill_html(pill_key, pills), unsafe_allow_html=True)
    with top_r:
        if st.button(i18n.t("inbox.close"), key="rec_close", use_container_width=True):
            st.session_state.pop("rec_open", None)
            st.session_state["rec_scroll"] = "#yz-rec-list"
            st.rerun()

    styles.detail_card(
        [
            (i18n.t("records.col_id"), record.get("proposal_id")),
            (i18n.t("records.col_submitted"), _friendly_when(record.get("submitted_at"))),
            (i18n.t("detail.requested_by"),
             i18n.t("reg.system") if record.get("requested_by") == "system" else record.get("requested_by")),
            (i18n.t("records.col_department"), _record_department(record)),
        ]
    )

    st.caption(i18n.t("records.details"))
    if action_type == "leave_request":
        _render_leave_proposal_details(payload, f"rec_{proposal_id}")
    elif action_type == "personal_info_update":
        styles.detail_card(
            [
                (i18n.t("records.field"), str(payload.get("field_name") or "").replace("_", " ").title()),
                (i18n.t("records.old_value"), payload.get("old_value")),
                (i18n.t("records.new_value"), payload.get("new_value")),
            ]
        )
    elif action_type == "bank_update":
        styles.detail_card(
            [
                (i18n.t("records.old_value"), payload.get("old_iban")),
                (i18n.t("records.new_value"), payload.get("new_iban")),
                (i18n.t("records.bank"), payload.get("new_bank_name") or payload.get("new_bank_code")),
            ]
        )
    elif action_type == "new_hire":
        if record.get("subject_id"):
            styles.detail_card([(i18n.t("details.employee_id_created"), record.get("subject_id"))])
        _render_new_hire_details(payload)
        _render_request_warnings(payload)
    elif action_type == "regulation_update":
        from app.ui import regulations_view

        regulations_view.render_record_details(record)
    elif action_type == "termination":
        sep = record.get("separation") or {}
        if sep.get("state") == "notice":
            st.info(i18n.t("notice.until", date=_friendly_when(sep.get("until")) or "—"))
        elif sep.get("state") == "finalized":
            st.caption(i18n.t("notice.finalized", date=_friendly_when(sep.get("at")) or "—"))
        _render_termination_details(payload)
        _render_request_warnings(payload)
        _render_settlement_download(record)
    else:
        _render_generic_proposal_details(
            {k: v for k, v in payload.items() if k not in {"decision", "cover_candidates"}}
        )

    st.caption(i18n.t("records.decision"))
    if record.get("status") == "pending_approval":
        st.write(i18n.t("details.pending"))
    else:
        who = record.get("decided_by")
        styles.detail_card(
            [
                (i18n.t("records.col_decided_by"), f"{record.get('decided_by_name') or who} ({who})" if who else i18n.t("records.system")),
                (i18n.t("records.decided_at"), _friendly_when(record.get("decided_at"))),
                (i18n.t("records.note"), record.get("decision_note") or i18n.t("details.no_note")),
            ]
        )


def page_records() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("records.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("records.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not _can_manage_staff():
        st.error(i18n.t("records.not_allowed"))
        return

    try:
        facets = api.raise_for_api(api.request("GET", "/records/facets")) or {}
    except RuntimeError as exc:
        st.error(str(exc))
        return
    _render_records_filters(facets)

    params = _records_params()
    signature = json.dumps(params, sort_keys=True)
    if st.session_state.get("rec_sig") != signature:
        # New filters: back to page 1, drop the prepared files.
        st.session_state["rec_sig"] = signature
        st.session_state["rec_page"] = 1
        st.session_state.pop("rec_xlsx", None)
        st.session_state.pop("rec_pdf", None)
    if st.session_state.get("rec_export_lang") != i18n.get_lang():
        # Prepared files are in the language they were built in.
        st.session_state["rec_export_lang"] = i18n.get_lang()
        st.session_state.pop("rec_xlsx", None)
        st.session_state.pop("rec_pdf", None)
    page = st.session_state.get("rec_page", 1)

    try:
        data = api.raise_for_api(api.request("GET", "/records", params={**params, "page": page})) or {}
    except RuntimeError as exc:
        st.error(str(exc))
        return
    total, page_size = int(data.get("total") or 0), int(data.get("page_size") or 20)
    pages = max((total + page_size - 1) // page_size, 1)

    status_styles = {"pending_approval": styles.status_pills()["pending"]}
    rows = []
    for record in data.get("items") or []:
        pill_key, pill_label = _record_status(record)
        if pill_label:
            status_styles[pill_key] = (
                pill_label, "◷" if pill_key.startswith("notice_") else "■", "#E8EEF6", "#2F4A6B"
            )
        subject = record.get("subject_name") or "—"
        if record.get("subject_id"):
            subject = f"{subject} ({record['subject_id']})"
        rows.append(
            {
                "id": record.get("proposal_id"),
                "date": _friendly_when(record.get("submitted_at")),
                "type": _record_type_label(record.get("action_type")),
                "employee": subject,
                "department": _record_department(record),
                "status": pill_key,
                "decided_by": record.get("decided_by_name") or record.get("decided_by"),
            }
        )

    def _open(row: dict) -> None:
        st.session_state["rec_open"] = row.get("id")
        st.session_state["rec_scroll"] = "#yz-rec-detail"

    st.markdown('<div id="yz-rec-list"></div>', unsafe_allow_html=True)
    records_card = st.container(key="records_card")
    top_l, top_x, top_p = records_card.columns([3, 1, 1])
    top_l.caption(i18n.t("records.count", total=total, page=page, pages=pages))
    for col, kind, label, mime in (
        (top_x, "xlsx", "excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (top_p, "pdf", "pdf", "application/pdf"),
    ):
        with col:
            prepared = st.session_state.get(f"rec_{kind}")
            if prepared is None:
                with st.container(key=f"yz_btn_secondary_rec_{kind}"):
                    if st.button(i18n.t(f"records.export_{label}"), key=f"rec_prepare_{kind}",
                                 use_container_width=True, disabled=not total):
                        try:
                            st.session_state[f"rec_{kind}"] = _build_records_export(kind, params, facets)
                        except (RuntimeError, httpx.HTTPError) as exc:
                            st.session_state["rec_export_error"] = str(exc)
                        st.rerun()
            else:
                st.download_button(
                    i18n.t(f"records.download_{label}"),
                    data=prepared,
                    file_name=f"records_{date.today().isoformat()}.{kind}",
                    mime=mime,
                    key=f"rec_download_{kind}",
                    use_container_width=True,
                )
    export_error = st.session_state.pop("rec_export_error", None)
    if export_error:
        records_card.error(export_error)

    with records_card:
        styles.data_table(
            rows,
            [
                ("date", i18n.t("records.col_submitted")),
                ("type", i18n.t("records.col_type")),
                ("employee", i18n.t("records.col_employee")),
                ("department", i18n.t("records.col_department")),
                ("status", i18n.t("records.col_status")),
                ("decided_by", i18n.t("records.col_decided_by")),
            ],
            status_key="status",
            status_styles=status_styles,
            on_view=_open,
            view_label=i18n.t("staffing.view"),
            key="records",
            empty_message=i18n.t("records.empty"),
        )
        opened = st.session_state.get("rec_open")
        selected = next((i for i, row in enumerate(rows) if opened and row["id"] == opened), None)
        if selected is not None:
            st.markdown(styles.selected_row_css("records", selected), unsafe_allow_html=True)

    if pages > 1:
        p1, p2, p3 = st.columns([1, 2, 1])
        with p1, st.container(key="yz_btn_secondary_rec_prev"):
            if st.button(i18n.t("records.prev"), key="rec_prev", use_container_width=True, disabled=page <= 1):
                st.session_state["rec_page"] = page - 1
                st.rerun()
        p2.markdown(
            f'<div style="text-align:center;padding-top:.5rem">{html.escape(i18n.t("records.page", page=page, pages=pages))}</div>',
            unsafe_allow_html=True,
        )
        with p3, st.container(key="yz_btn_secondary_rec_next"):
            if st.button(i18n.t("records.next"), key="rec_next", use_container_width=True, disabled=page >= pages):
                st.session_state["rec_page"] = page + 1
                st.rerun()

    opened = st.session_state.get("rec_open")
    if opened:
        st.divider()
        with st.container(key="records_detail"):
            _render_record_detail(opened)

    # Set by View (to the details) and by Close / Back to list (to the table).
    target = st.session_state.pop("rec_scroll", None)
    if target:
        _scroll_to(target)


def _render_message(message: dict, index: int) -> None:
    role = message.get("role")
    content = str(message.get("content") or "")
    # Escape first (safety), then re-apply just **bold** as <strong> — the
    # only markdown the agent responses actually rely on (policy citations).
    content_html = _BOLD_RE.sub(r"<strong>\1</strong>", html.escape(content))
    content_html = content_html.replace("\n", "<br>")
    direction = "rtl" if _is_rtl(content) else "ltr"
    row_class = "yz-msg-row--user" if role == "user" else "yz-msg-row--assistant"
    bubble_class = "yz-bubble--user" if role == "user" else "yz-bubble--assistant"
    st.markdown(
        f'<div class="yz-msg-row {row_class}">'
        f'<div class="yz-bubble {bubble_class}" dir="{direction}">{content_html}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if role == "assistant" and message.get("sources"):
        styles.render_sources(message["sources"], key=f"chatmsg_{index}")


def page_chat() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title yz-chat-title--leaf">
            {styles.leaf_icon_html("yz-chat-leaf")}{html.escape(i18n.t("chat.title"))}
          </div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("chat.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    st.markdown('<div class="yz-chat-scroll">', unsafe_allow_html=True)

    if not st.session_state.chat_history:
        st.markdown(
            f"""
            <div class="yz-welcome">
              <strong>{html.escape(i18n.t("chat.welcome_title"))}</strong><br>
              {html.escape(i18n.t("chat.welcome_body"))}
            </div>
            """,
            unsafe_allow_html=True,
        )

    for index, message in enumerate(st.session_state.chat_history):
        _render_message(message, index)

    pending_query = st.session_state.get("chat_pending_identity_query")
    if pending_query:
        st.markdown(
            '<div class="yz-msg-row yz-msg-row--assistant">'
            '<div class="yz-bubble yz-bubble--assistant">'
            f"{html.escape(i18n.t('chat.identity_prompt'))}"
            "</div></div>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        if c1.button(i18n.t("chat.hide_identity"), use_container_width=True):
            _resolve_identity_choice(False)
            st.rerun()
        if c2.button(i18n.t("chat.show_identity"), use_container_width=True):
            _resolve_identity_choice(True)
            st.rerun()
    else:
        st.markdown(
            f'<div class="yz-suggestions-label">{html.escape(i18n.t("chat.suggested_questions"))}</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(3)
        for col, (label, query) in zip(cols, _chat_suggestions()):
            with col:
                key = "yz_sugg_" + "".join(c if c.isalnum() else "_" for c in label.lower())
                with st.container(key=key):
                    # Only fills the prompt box, so the employee can edit
                    # dates/details before sending it themselves.
                    st.button(
                        label,
                        use_container_width=True,
                        on_click=_fill_chat_draft,
                        args=(query,),
                    )

    st.markdown("</div>", unsafe_allow_html=True)

    # A form instead of st.chat_input: chat_input can't be pre-filled from
    # a suggestion, and a form sends only when the Send button is clicked.
    with st.form("yz_chat_form", clear_on_submit=True, border=False):
        prompt = st.text_area(
            i18n.t("chat.placeholder"),
            key="chat_draft",
            placeholder=i18n.t("chat.placeholder"),
            label_visibility="collapsed",
            height=90,
        )
        sent = st.form_submit_button(i18n.t("chat.send"))
    if sent and prompt.strip():
        _submit_query(prompt.strip())
        st.rerun()


def _fill_chat_draft(query: str) -> None:
    st.session_state["chat_draft"] = query


def page_leave() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("leave.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("leave.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        reqs = api.raise_for_api(api.request("GET", "/leave/requests")) or []
    except RuntimeError as exc:
        st.error(str(exc))
        return
    rows = [
        {
            "leave_type": str(r.get("leave_type") or "").title(),
            "dates": (
                f"{_friendly_when(r.get('start_date')) or r.get('start_date') or '—'} "
                f"→ {_friendly_when(r.get('end_date')) or r.get('end_date') or '—'}"
            ),
            "days": r.get("days"),
            "reason": r.get("reason") or "—",
            "status": str(r.get("status") or "pending").lower(),
            "submitted_at": _friendly_when(r.get("submitted_at")) or "—",
        }
        for r in reqs
    ]
    styles.data_table(
        rows,
        [
            ("leave_type", i18n.t("leave.col_type")),
            ("dates", i18n.t("leave.col_dates")),
            ("days", i18n.t("leave.col_days")),
            ("reason", i18n.t("leave.col_reason")),
            ("status", i18n.t("leave.col_status")),
            ("submitted_at", i18n.t("leave.col_submitted")),
        ],
        status_key="status",
        key="myrequests",
        empty_message=i18n.t("leave.empty"),
    )
    st.caption(i18n.t("leave.footer_hint"))


def page_inbox() -> None:
    """Waiting on you — every item that needs a real decision, in one
    unified table across three real sources: pending /approvals split into
    "leave_request" vs everything else (via /proposed-actions' action_type,
    the only place that field lives — /approvals itself doesn't carry it),
    and /grievances still at PENDING_HR_REVIEW. Tabs and their underlying
    API calls are scoped to what this role can actually decide: hr_manager/
    admin get all four tabs, hr_specialist gets only the Grievances tab
    (hr_specialist has no /approvals access at all — confirmed against the
    real router's role check — so /approvals is never even called for
    them, not just hidden in the UI).
    """
    me = st.session_state.get("me") or {}
    role = me.get("role")
    can_approvals = role in {"hr_manager", "admin"}
    can_grievances = role in {"hr_specialist", "hr_manager", "admin"}

    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("inbox.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("inbox.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    from app.ui import regulations_view

    regulations_view.show_flash()

    pending_approvals: list[dict] = []
    approvals: list[dict] = []
    proposals: dict = {}
    if can_approvals:
        try:
            approvals = api.raise_for_api(api.request("GET", "/approvals")) or []
        except RuntimeError as exc:
            st.error(str(exc))
            approvals = []
        if not isinstance(approvals, list):
            approvals = []
        pending_approvals = [r for r in approvals if (r.get("status") or "").lower() == "pending"]
        proposals = _proposal_map()
        # System-requested regulation updates (not returned by /approvals).
        known = {r.get("approval_id") for r in pending_approvals}
        for item in regulations_view.pending_requests():
            proposals[item["proposal_id"]] = item["proposal"]
            if item.get("approval_id") not in known:
                pending_approvals.append(item)

    pending_grievances: list[dict] = []
    if can_grievances:
        try:
            grievances = api.raise_for_api(api.request("GET", "/grievances")) or []
        except RuntimeError as exc:
            st.error(str(exc))
            grievances = []
        if not isinstance(grievances, list):
            grievances = []
        pending_grievances = [
            g for g in grievances if str(g.get("status") or "").upper() == "PENDING_HR_REVIEW"
        ]

    def _action_type(row: dict) -> str:
        proposal = proposals.get(row.get("proposal_id")) or {}
        return str(proposal.get("action_type") or "")

    leave_items = [r for r in pending_approvals if _action_type(r) == "leave_request"]
    other_approval_items = [r for r in pending_approvals if _action_type(r) != "leave_request"]
    names = _employee_names(
        [r.get("employee_id") for r in pending_approvals]
        + [g.get("employee_id") for g in pending_grievances if g.get("identity_visible")]
    )

    def _leave_row(r: dict) -> dict:
        return {
            "_kind": "approval",
            "_id": str(r.get("approval_id") or ""),
            "type": styles.type_badge_html("leave", i18n.t("inbox.type_leave")),
            "employee": names.get(str(r.get("employee_id") or "")) or r.get("employee_id") or "—",
            "request": _action_label(r, proposals.get(r.get("proposal_id"))),
            "date": _friendly_when(r.get("created_at")) or "—",
            "status": (r.get("status") or "pending").lower(),
        }

    def _approval_row(r: dict) -> dict:
        proposal = proposals.get(r.get("proposal_id")) or {}
        if proposal.get("action_type") == "regulation_update":
            from app.ui import regulations_view

            payload = proposal.get("payload_json") if isinstance(proposal.get("payload_json"), dict) else {}
            return {
                "_kind": "approval",
                "_id": str(r.get("approval_id") or ""),
                "type": styles.type_badge_html("approval", i18n.t("records.type.regulation_update")),
                "employee": i18n.t("reg.system"),
                "request": regulations_view.request_label(payload),
                "date": _friendly_when(r.get("created_at")) or "—",
                "status": (r.get("status") or "pending").lower(),
            }
        return {
            "_kind": "approval",
            "_id": str(r.get("approval_id") or ""),
            "type": styles.type_badge_html("approval", i18n.t("inbox.type_approval")),
            "employee": names.get(str(r.get("employee_id") or "")) or r.get("employee_id") or "—",
            "request": _staffing_label(proposals.get(r.get("proposal_id"))) or _action_label(r, proposal),
            "date": _friendly_when(r.get("created_at")) or "—",
            "status": (r.get("status") or "pending").lower(),
        }

    def _grievance_row(g: dict) -> dict:
        eid = str(g.get("employee_id") or "")
        employee_label = (names.get(eid) or eid or "—") if g.get("identity_visible") else i18n.t("inbox.anonymous")
        return {
            "_kind": "grievance",
            "_id": str(g.get("grievance_id") or ""),
            "type": styles.type_badge_html("grievance", i18n.t("inbox.type_grievance")),
            "employee": employee_label,
            "request": _grievance_subject(g),
            "date": _friendly_when(g.get("submitted_at")) or "—",
            "status": "pending",
        }

    rows_by_tab: dict[str, list[dict]] = {
        "leave": [_leave_row(r) for r in leave_items],
        "approval": [_approval_row(r) for r in other_approval_items],
        "grievance": [_grievance_row(g) for g in pending_grievances],
    }
    rows_by_tab["all"] = rows_by_tab["leave"] + rows_by_tab["approval"] + rows_by_tab["grievance"]

    tabs = [("all", i18n.t("inbox.tab_all"))]
    if can_approvals:
        tabs += [("leave", i18n.t("inbox.tab_leave")), ("approval", i18n.t("inbox.tab_approval"))]
    if can_grievances:
        tabs.append(("grievance", i18n.t("inbox.tab_grievance")))

    tab_full_labels = [f"{label} ({len(rows_by_tab[key])})" for key, label in tabs]
    chosen_full_label = st.radio(
        i18n.t("inbox.filter_label"), tab_full_labels, horizontal=True, key="inbox_tab", label_visibility="collapsed"
    )
    chosen_key = dict(zip(tab_full_labels, [k for k, _ in tabs])).get(chosen_full_label, "all")
    visible_rows = rows_by_tab.get(chosen_key, [])

    def _open_review(row: dict) -> None:
        st.session_state["inbox_open"] = (row["_kind"], row["_id"])
        st.session_state["inbox_scroll"] = "#yz-inbox-detail"

    st.markdown('<div id="yz-inbox-list"></div>', unsafe_allow_html=True)
    styles.data_table(
        visible_rows,
        [
            ("type", i18n.t("inbox.col_type")),
            ("employee", i18n.t("inbox.col_employee")),
            ("request", i18n.t("inbox.col_request")),
            ("date", i18n.t("inbox.col_date")),
            ("status", i18n.t("inbox.col_status")),
        ],
        status_key="status",
        raw_html_keys={"type"},
        on_view=_open_review,
        view_label=i18n.t("inbox.review"),
        key="inbox",
        empty_message=i18n.t("inbox.empty"),
    )

    opened = st.session_state.get("inbox_open")
    if opened:
        kind, item_id = opened
        selected = next(
            (i for i, r in enumerate(visible_rows) if (r.get("_kind"), r.get("_id")) == (kind, item_id)),
            None,
        )
        if selected is not None:
            st.markdown(styles.selected_row_css("inbox", selected), unsafe_allow_html=True)
        st.divider()
        st.markdown('<div id="yz-inbox-detail"></div>', unsafe_allow_html=True)
        if kind == "approval":
            row = next(
                (r for r in pending_approvals if str(r.get("approval_id") or "") == item_id), None
            )
            if row:
                _render_approval_review(row, names, proposals)
        elif kind == "grievance":
            g = next(
                (g for g in pending_grievances if str(g.get("grievance_id") or "") == item_id), None
            )
            if g:
                _render_grievance_review(g, names)

    # Set by Review (to the details) and by Close (back to the list).
    target = st.session_state.pop("inbox_scroll", None)
    if target:
        _scroll_to(target)


def _render_approval_review(row: dict, names: dict, proposals: dict) -> None:
    proposal = proposals.get(row.get("proposal_id")) or {}
    if proposal.get("action_type") == "regulation_update":
        from app.ui import regulations_view

        regulations_view.render_review(row, proposal)
        return
    approval_id = str(row.get("approval_id") or "")
    employee_id = str(row.get("employee_id") or "")
    person = names.get(employee_id) or employee_id or "—"
    status = (row.get("status") or "pending").lower()
    staffing_label = _staffing_label(proposals.get(row.get("proposal_id")))
    if staffing_label:
        row = {**row, "action_summary": staffing_label}
    else:
        row = {**row, "action_summary": _action_label(row, proposal)}

    with st.container(key=f"inbox_review_{approval_id}"):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            st.markdown(f"#### {html.escape(person)}")
        with top_r:
            if st.button(i18n.t("inbox.close"), key=f"close_{approval_id}", use_container_width=True):
                st.session_state.pop("inbox_open", None)
                st.session_state["inbox_scroll"] = "#yz-inbox-list"
                st.rerun()

        st.markdown(_approval_card_html(row, person), unsafe_allow_html=True)

        # Briefs are kept per language: after a language switch an open
        # brief is re-requested in the new language instead of showing the
        # old language's text under the new language's labels.
        lang = i18n.get_lang()
        brief_key = f"approval_brief_{lang}_{approval_id}"
        other_key = f"approval_brief_{'en' if lang == 'ar' else 'ar'}_{approval_id}"
        if status == "pending":
            explain = st.button(i18n.t("inbox.explain_this"), key=f"explain_{approval_id}", use_container_width=True)
            if explain or (brief_key not in st.session_state and other_key in st.session_state):
                try:
                    with st.spinner(i18n.t("inbox.brief_loading")):
                        brief_response = api.request("GET", f"/approvals/{approval_id}/brief", timeout=120.0)
                    brief = api.raise_for_api(brief_response)
                    if isinstance(brief, dict):
                        st.session_state[brief_key] = brief
                    else:
                        st.error(i18n.t("brief.unexpected"))
                except RuntimeError as exc:
                    st.error(str(exc))

        brief = st.session_state.get(brief_key)
        if brief:
            _render_decision_brief(brief.get("brief") or {}, approval_id)

        proposal = proposals.get(row.get("proposal_id"))
        _render_proposal_details(proposal)
        if (proposal or {}).get("action_type") in ("new_hire", "termination"):
            proposal_payload = proposal.get("payload_json")
            _render_request_warnings(
                proposal_payload if isinstance(proposal_payload, dict) else {},
                for_approver=status == "pending",
            )

        if status != "pending":
            # Already decided — no decision form. Re-showing Approve/Send
            # back here previously let a stray click re-run the decision
            # (duplicate leave request, double-deducted balance).
            st.caption(
                i18n.t(
                    "inbox.decided_by",
                    status=_status_label(status),
                    who=row.get("decided_by") or "—",
                    when=_friendly_when(row.get("decided_at")) or "—",
                )
            )
            if row.get("decision_note"):
                st.caption(i18n.t("inbox.decision_note", note=row["decision_note"]))
        else:
            cover_options = _cover_candidate_options(proposal)
            with st.form(f"decide_{approval_id}"):
                cover_employee_id = None
                if cover_options:
                    ids, labels, default_index = cover_options
                    cover_employee_id = st.selectbox(
                        i18n.t("inbox.cover_employee"), ids, index=default_index, format_func=lambda eid: labels.get(eid, eid)
                    )
                note = st.text_area(
                    i18n.t("inbox.note_label"), placeholder=i18n.t("inbox.note_placeholder"), label_visibility="collapsed"
                )
                st.caption(i18n.t("inbox.note_optional"))
                col_a, col_b = st.columns(2)
                approve = col_a.form_submit_button(i18n.t("inbox.approve"), type="primary", use_container_width=True)
                reject = col_b.form_submit_button(i18n.t("inbox.send_back"), use_container_width=True)
            if approve:
                _decide(approval_id, "approve", note, person, cover_employee_id)
            elif reject:
                _decide(approval_id, "reject", note, person, cover_employee_id)


_POLICY_HEADING_STOP_WORDS = {"summary", "key points", "citations", "policy sources", "request details"}
_POLICY_SUBSECTION_RE = re.compile(
    r"^[-•]?\s*[^:–—]+\s*[-–—]\s*(Rule|Process|Conditions|Eligibility)",
    re.IGNORECASE,
)
_POLICY_HEADING_RE = re.compile(r"^#{1,6}\s*")
_MANAGER_RECOMMENDATION_KEYWORDS = ("MANAGER REVIEW", "APPROVE", "REJECT")
_MANAGER_RECOMMENDATION_RE = re.compile(
    r"\b(" + "|".join(_MANAGER_RECOMMENDATION_KEYWORDS) + r")\b", re.IGNORECASE
)


def _clean_policy_summary(policy_text: str, max_sentences: int = 4) -> str:
    """Trim a real LLM-generated policy recommendation to a short, clean
    summary — strips markdown bold/headings, tables, source-reference
    lines, and per-clause "Rule/Process/Conditions" subsection lines, then
    keeps only the first few complete sentences. Merged in from a
    teammate's fix on main (real LLM prose can run long and noisy); the
    raw text is still fully available via styles.render_sources()'s "Show
    full text" expander for the policy sources themselves — this only
    trims the one-line recommendation summary."""
    clean = policy_text.replace(r"\*\*", "").replace("**", "")
    lines: list[str] = []
    for line in clean.splitlines():
        line = line.strip()
        if not line:
            continue
        line = _POLICY_HEADING_RE.sub("", line)
        if line.lower() in _POLICY_HEADING_STOP_WORDS:
            break
        if line.startswith("|"):
            continue
        if line.startswith("[Source:") or line.startswith("- Reference:"):
            continue
        if _POLICY_SUBSECTION_RE.match(line):
            continue
        lines.append(line)
    clean_policy = " ".join(lines)
    sentences = re.split(r"(?<=[.!?؟])\s+", clean_policy)
    return " ".join(s.strip() for s in sentences[:max_sentences] if s.strip())


def _extract_manager_recommendation(manager_response: str) -> tuple[str | None, str]:
    """Pull an APPROVE/REJECT/MANAGER REVIEW keyword out of the manager
    agent's free-text response for a prominent heading, returning it
    alongside the remaining explanation with that keyword stripped.
    Merged in from a teammate's fix on main — reworked from a plain
    substring check to a word-boundary regex, since the original matched
    "APPROVE" inside "0 approved, 2 denied" (a real historical-precedent
    line) and misreported a MANAGER REVIEW case as APPROVE."""
    match = _MANAGER_RECOMMENDATION_RE.search(manager_response)
    if not match:
        return None, manager_response
    recommendation = match.group(1).upper()
    explanation = (manager_response[: match.start()] + manager_response[match.end() :]).strip(" :-\n")
    # Drop the now-empty "AI Recommendation:" label line the keyword came from.
    explanation = "\n".join(
        line for line in explanation.splitlines() if line.strip().lower() != "ai recommendation:"
    ).strip()
    return recommendation, explanation


def _render_decision_brief(decision_brief: dict, approval_id: str) -> None:
    st.markdown(f"### {i18n.t('inbox.decision_brief')}")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**{i18n.t('inbox.action')}**")
        st.write(_record_type_label(decision_brief.get("action_type")))
    with col_b:
        st.markdown(f"**{i18n.t('inbox.risk')}**")
        st.write(_risk_label(decision_brief.get("risk_level"))[0])

    profile = decision_brief.get("termination_profile")
    if profile:
        _render_termination_profile(profile)
    _render_staffing_checklist(decision_brief.get("action_type"))

    precedent = decision_brief.get("historical_precedent")
    if precedent:
        st.markdown(f"**{i18n.t('inbox.historical_precedent')}**")
        styles.detail_card(
            [
                (i18n.t("detail.approved"), precedent.get("approved_count", 0)),
                (i18n.t("detail.denied"), precedent.get("denied_count", 0)),
                (i18n.t("detail.total"), precedent.get("total_count", 0)),
            ]
        )

    policy = decision_brief.get("policy") or {}
    # The API adds *_ar translations of the LLM-written parts when the UI
    # language is Arabic (Accept-Language); fall back to the English text.
    recommendation = str(
        (i18n.is_rtl() and policy.get("recommendation_ar")) or policy.get("recommendation") or ""
    ).strip()
    if recommendation:
        st.markdown(f"**{i18n.t('inbox.policy')}**")
        st.write(_clean_policy_summary(recommendation))

    styles.render_sources(policy.get("sources") or [], key=f"brief_{approval_id}")

    manager = decision_brief.get("manager") or {}
    manager_response = str(manager.get("response") or "").strip()
    if manager_response:
        st.markdown(f"**{i18n.t('inbox.ai_recommendation')}**")
        keyword, explanation = _extract_manager_recommendation(manager_response)
        if keyword:
            # The keyword is a fixed rule-based value
            # (manager_agent._get_ai_recommendation), so it's rendered from
            # i18n; the evidence lines come pre-translated as explanation_ar.
            code = keyword.replace(" ", "_").lower()
            st.markdown(f"#### {i18n.t(f'brief.rec_{code}')}")
        if i18n.is_rtl() and manager.get("explanation_ar"):
            explanation = manager["explanation_ar"]
        if explanation:
            st.caption(i18n.t("inbox.reason"))
            st.write(explanation)

    reasons = (i18n.is_rtl() and manager.get("reasons_ar")) or manager.get("reasons") or []
    if reasons:
        st.markdown(f"**{i18n.t('inbox.notes')}**")
        for reason in reasons:
            st.write(f"- {reason}")


def _cite(item: dict | None) -> str:
    item = item or {}
    if not item.get("law_id"):
        return ""
    return i18n.t("profile.cite", article=item.get("article"), law_id=item.get("law_id"))


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


def _ratio_text(ratio) -> str:
    ratio = float(ratio or 0)
    if ratio >= 1:
        return i18n.t("profile.ratio_full")
    if ratio <= 0:
        return "0"
    return str(Fraction(ratio).limit_denominator(3))


def _render_termination_profile(profile: dict) -> None:
    """Read-only end-of-service file from HR agent's get_termination_profile.
    Every figure shows the article it was computed from."""
    st.markdown(f"**{i18n.t('profile.title')}**")
    if profile.get("error"):
        st.warning(profile["error"])
        st.caption(i18n.t("profile.disclaimer"))
        return

    service = profile.get("service") or {}
    wage = profile.get("monthly_wage") or {}
    award = profile.get("end_of_service_award") or {}
    notice = profile.get("notice") or {}
    leave = profile.get("unused_leave") or {}
    settlement = profile.get("settlement") or {}
    impact = profile.get("impact") or {}

    award_cites = [_cite(award.get("full_award_law"))]
    if (award.get("ratio_law") or {}).get("law_id") != (award.get("full_award_law") or {}).get("law_id"):
        award_cites.append(_cite(award.get("ratio_law")))

    required = notice.get("required_days")
    if required is None:
        notice_text = i18n.t("profile.notice_by_agreement")
    elif required == 0:
        notice_text = i18n.t("profile.notice_na")
    else:
        notice_text = i18n.t("profile.notice_value", required=required, given=notice.get("days_given"))

    styles.detail_card(
        [
            (
                i18n.t("profile.service"),
                i18n.t(
                    "profile.service_value",
                    y=service.get("years"),
                    m=service.get("months"),
                    d=service.get("days"),
                    total=service.get("total_days"),
                )
                + f" · {_cite(service)}",
            ),
            (i18n.t("profile.monthly_wage"), f"{_money(wage.get('value'))} SAR · {_cite(wage)}"),
            (
                i18n.t("profile.award"),
                i18n.t(
                    "profile.award_value",
                    full=_money(award.get("full_award")),
                    ratio=_ratio_text(award.get("ratio")),
                    value=_money(award.get("value")),
                )
                + " · "
                + " + ".join(award_cites),
            ),
            (i18n.t("profile.notice"), f"{notice_text} · {_cite(notice)}"),
            (
                i18n.t("profile.unused_leave"),
                i18n.t(
                    "profile.unused_leave_value",
                    days=f"{float(leave.get('annual_remaining_days') or 0):g}",
                    daily=_money(leave.get("daily_wage")),
                    value=_money(leave.get("value")),
                )
                + f" · {_cite(leave)}",
            ),
            (
                i18n.t("profile.settlement"),
                i18n.t(
                    "profile.settlement_value",
                    deadline=settlement.get("deadline"),
                    days=settlement.get("within_days"),
                )
                + f" · {_cite(settlement)}",
            ),
        ]
    )
    st.caption(i18n.t("profile.monthly_wage_note"))
    if profile.get("article_80"):
        styles.detail_card(_article_80_rows(profile["article_80"]))

    for warning in profile.get("warnings") or []:
        code = warning.get("code")
        if code == "notice_shortfall":
            text = i18n.t(
                "profile.warn_notice",
                given=notice.get("days_given"),
                required=notice.get("required_days"),
                payer=i18n.t(f"profile.payer_{notice.get('payer')}"),
                shortfall=notice.get("shortfall_days"),
                amount=_money(notice.get("compensation")),
            )
        elif code == "article_80_objection":
            text = i18n.t("profile.warn_article_80")
        elif code == "illegitimate_termination":
            text = i18n.t("profile.warn_illegitimate")
        else:
            text = str(warning.get("text") or "")
        st.warning(f"{text} · {_cite(warning)}")

    st.caption(i18n.t("profile.impact"))
    styles.detail_card(
        [
            (i18n.t("profile.direct_reports"), impact.get("active_direct_reports")),
            (i18n.t("profile.departments_headed"), ", ".join(impact.get("departments_headed") or []) or "—"),
            (i18n.t("profile.pending_requests"), impact.get("pending_requests")),
        ]
    )
    st.caption(i18n.t("profile.disclaimer"))


def _render_staffing_checklist(action_type: str | None) -> None:
    keys = {
        "termination": ("term_qiwa", "term_gosi", "term_mudad", "term_clearance"),
        "new_hire": ("hire_qiwa", "hire_gosi", "hire_medical", "hire_probation"),
    }.get(str(action_type or ""))
    if not keys:
        return
    st.markdown(f"**{i18n.t('checklist.title')}**")
    st.markdown("\n".join(f"- {i18n.t(f'checklist.{key}')}" for key in keys))


def _localized_grievance(g: dict) -> dict:
    """Overlay the complaint / Consultant assessment / HR note in the UI
    language from GET /grievances/{id} (the list endpoint stays
    untranslated so the inbox loads fast). Works both ways: an Arabic
    complaint is shown in English to an English-language reviewer, and
    English Consultant text in Arabic. Cached per grievance + status +
    language for the session; falls back to the stored text on any error."""
    grievance_id = str(g.get("grievance_id") or "")
    if not grievance_id:
        return g
    lang = i18n.get_lang()
    cache_key = f"grievance_{lang}_{grievance_id}_{g.get('status')}"
    detail = st.session_state.get(cache_key)
    if detail is None:
        try:
            with st.spinner(i18n.t("common.opening")):
                detail = api.raise_for_api(api.request("GET", f"/grievances/{grievance_id}", timeout=120.0)) or {}
        except RuntimeError:
            detail = {}
        st.session_state[cache_key] = detail
    localized = dict(g)
    for field in ("complaint", "consultant_recommendation", "hr_response"):
        if detail.get(f"{field}_{lang}"):
            localized[field] = detail[f"{field}_{lang}"]
    return localized


def _render_grievance_review(g: dict, names: dict) -> None:
    g = _localized_grievance(g)
    grievance_id = str(g.get("grievance_id") or "")
    identity_visible = bool(g.get("identity_visible"))
    if identity_visible:
        employee_id = str(g.get("employee_id") or "")
        person = names.get(employee_id) or employee_id or "—"
    else:
        person = i18n.t("inbox.anonymous")

    with st.container(key=f"inbox_review_g_{grievance_id}"):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            st.markdown(f"#### {html.escape(person)} · {html.escape(grievance_id)}")
        with top_r:
            if st.button(i18n.t("inbox.close"), key=f"close_g_{grievance_id}", use_container_width=True):
                st.session_state.pop("inbox_open", None)
                st.session_state["inbox_scroll"] = "#yz-inbox-list"
                st.rerun()

        styles.detail_card(
            [
                (i18n.t("detail.identity"), person),
                (i18n.t("detail.submitted"), _friendly_when(g.get("submitted_at")) or "—"),
            ]
        )
        st.write(g.get("complaint") or i18n.t("grievances.no_complaint"))

        if st.button(
            i18n.t("inbox.explain_this"), key=f"explain_grievance_{grievance_id}", use_container_width=True
        ):
            st.session_state[f"grievance_brief_{grievance_id}"] = {
                "recommendation": g.get("consultant_recommendation") or "",
                "sources": g.get("sources"),
            }

        brief = st.session_state.get(f"grievance_brief_{grievance_id}")
        if brief:
            st.markdown(f"### {i18n.t('inbox.decision_brief')}")
            recommendation = str(brief.get("recommendation") or "").strip()
            if recommendation:
                st.markdown(f"**{i18n.t('inbox.consultant_assessment')}**")
                st.write(recommendation)
            styles.render_sources(brief.get("sources") or [], key=f"grievance_brief_{grievance_id}")

        with st.form(f"grievance_decision_{grievance_id}"):
            response_note = st.text_area(i18n.t("inbox.note_label"), placeholder=i18n.t("inbox.note_placeholder"))
            col_a, col_b = st.columns(2)
            accept = col_a.form_submit_button(i18n.t("inbox.accept"), type="primary", use_container_width=True)
            reject = col_b.form_submit_button(i18n.t("inbox.send_back"), use_container_width=True)
        if accept:
            _decide_grievance(grievance_id, "accept", response_note)
        elif reject:
            _decide_grievance(grievance_id, "reject", response_note)


def _grievance_status_styles() -> dict:
    return {
        "pending_hr_review": (i18n.t("status.open"), "◔", "#EDE6DA", "#5E5241"),
        "sent_back": (i18n.t("status.in_progress"), "↩", "#F6E7CF", "#7A4E12"),
        "submitted": (i18n.t("status.closed"), "✓", "#E3EEDC", "#35592A"),
    }


def _grievance_subject(g: dict) -> str:
    """Grievances have no subject/title field in the backend (confirmed:
    the table is grievance_id/employee_id/identity_visible/complaint/
    consultant_recommendation/sources/status/submitted_at/decided_at/
    decided_by/hr_response, SELECT * — nothing else). Derived client-side
    by truncating the complaint text, since a real backend field doesn't
    exist to use instead."""
    complaint = str(g.get("complaint") or "").strip()
    if not complaint:
        return "—"
    return complaint[:60] + "…" if len(complaint) > 60 else complaint


def page_grievances() -> None:
    """The full grievance record — every case regardless of status, unlike
    page_inbox()'s Grievances tab which only shows PENDING_HR_REVIEW ones.

    Tab mapping note: the backend only has 3 real grievance statuses —
    PENDING_HR_REVIEW, SUBMITTED, SENT_BACK (confirmed against the live DB
    and the grievances router; there is no RESOLVED/CLOSED status at all).
    "Open / In progress / Closed" are mapped onto those 3 real values as
    the closest fit: Open = PENDING_HR_REVIEW (awaiting a first HR look),
    In progress = SENT_BACK (sent back to the employee, not yet resolved),
    Closed = SUBMITTED (HR accepted it — the backend has no further action
    on a grievance once it leaves PENDING_HR_REVIEW either way, so this is
    the closest real equivalent to "closed" that exists)."""
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("grievances.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("grievances.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        grievances = api.raise_for_api(api.request("GET", "/grievances")) or []
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not isinstance(grievances, list):
        grievances = []

    names = _employee_names([g.get("employee_id") for g in grievances if g.get("identity_visible")])

    def _bucket(status: str) -> str:
        s = status.upper()
        if s == "SENT_BACK":
            return "in_progress"
        if s == "SUBMITTED":
            return "closed"
        return "open"

    groups: dict[str, list[dict]] = {"open": [], "in_progress": [], "closed": []}
    for g in grievances:
        groups[_bucket(str(g.get("status") or ""))].append(g)

    tabs = [
        ("all", i18n.t("grievances.tab_all"), grievances),
        ("open", i18n.t("grievances.tab_open"), groups["open"]),
        ("in_progress", i18n.t("grievances.tab_in_progress"), groups["in_progress"]),
        ("closed", i18n.t("grievances.tab_closed"), groups["closed"]),
    ]
    tab_full_labels = [f"{label} ({len(items)})" for _, label, items in tabs]
    chosen = st.radio(
        i18n.t("grievances.filter_label"), tab_full_labels, horizontal=True, key="grievances_tab", label_visibility="collapsed"
    )
    chosen_items = next(items for (_, _, items), full in zip(tabs, tab_full_labels) if full == chosen)

    rows = [
        {
            "id": g.get("grievance_id") or "—",
            "employee": (
                (names.get(str(g.get("employee_id") or "")) or g.get("employee_id"))
                if g.get("identity_visible")
                else i18n.t("inbox.anonymous")
            )
            or "—",
            "subject": _grievance_subject(g),
            "date": _friendly_when(g.get("submitted_at")) or "—",
            "status": str(g.get("status") or "").lower(),
            "_gid": g.get("grievance_id"),
        }
        for g in chosen_items
    ]

    def _open_view(row: dict) -> None:
        st.session_state["grievance_view_id"] = row.get("_gid")
        st.session_state["gv_scroll"] = "#yz-gv-detail"

    st.markdown('<div id="yz-gv-list"></div>', unsafe_allow_html=True)
    styles.data_table(
        rows,
        [
            ("id", i18n.t("grievances.col_id")),
            ("employee", i18n.t("grievances.col_employee")),
            ("subject", i18n.t("grievances.col_subject")),
            ("date", i18n.t("grievances.col_date")),
            ("status", i18n.t("grievances.col_status")),
        ],
        status_key="status",
        status_styles=_grievance_status_styles(),
        on_view=_open_view,
        view_label=i18n.t("grievances.col_view"),
        key="grievancesfull",
        empty_message=i18n.t("grievances.empty"),
    )

    view_id = st.session_state.get("grievance_view_id")
    if view_id:
        selected = next((i for i, r in enumerate(rows) if str(r.get("_gid")) == str(view_id)), None)
        if selected is not None:
            st.markdown(styles.selected_row_css("grievancesfull", selected), unsafe_allow_html=True)
        g = next((g for g in grievances if str(g.get("grievance_id") or "") == str(view_id)), None)
        if g:
            st.markdown('<div id="yz-gv-detail"></div>', unsafe_allow_html=True)
            _render_grievance_readonly(g, names)

    # Set by View (to the details) and by Close (back to the list).
    target = st.session_state.pop("gv_scroll", None)
    if target:
        _scroll_to(target)


def _render_grievance_readonly(g: dict, names: dict) -> None:
    g = _localized_grievance(g)
    grievance_id = str(g.get("grievance_id") or "")
    identity_visible = bool(g.get("identity_visible"))
    if identity_visible:
        employee_id = str(g.get("employee_id") or "")
        person = names.get(employee_id) or employee_id or "—"
    else:
        person = i18n.t("inbox.anonymous")

    with st.container(key=f"grievance_readonly_{grievance_id}"):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            st.markdown(f"#### {html.escape(person)} · {html.escape(grievance_id)}")
        with top_r:
            if st.button(i18n.t("inbox.close"), key=f"close_gview_{grievance_id}", use_container_width=True):
                st.session_state.pop("grievance_view_id", None)
                st.session_state["gv_scroll"] = "#yz-gv-list"
                st.rerun()

        st.write(g.get("complaint") or "—")
        status_val = str(g.get("status") or "").lower()
        styles.detail_card(
            [
                (
                    i18n.t("detail.identity"),
                    f"🔒 {i18n.t('inbox.anonymous')}" if not identity_visible else person,
                ),
                (i18n.t("detail.submitted"), _friendly_when(g.get("submitted_at")) or "—"),
                (
                    i18n.t("detail.status"),
                    styles.raw(styles.status_pill_html(status_val, _grievance_status_styles())),
                ),
            ]
        )
        if g.get("hr_response"):
            st.markdown(f"**{i18n.t('grievances.hr_note')}**")
            st.write(g.get("hr_response"))


def _decide_grievance(
    grievance_id: str,
    decision: str,
    response: str,
) -> None:
    resp = api.request(
        "POST",
        f"/grievances/{grievance_id}/decide",
        json={
            "decision": decision,
            "response": response or None,
        },
    )

    try:
        api.raise_for_api(resp)
        if decision == "accept":
            st.success(i18n.t("inbox.grievance_accepted_msg"))
        else:
            st.success(i18n.t("inbox.grievance_sent_back_msg"))
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))


def _cover_candidate_options(proposal: dict | None) -> tuple[list, dict, int] | None:
    if not proposal or str(proposal.get("action_type")) != "leave_request":
        return None
    payload = proposal.get("payload_json")
    payload = payload if isinstance(payload, dict) else {}
    candidates = payload.get("cover_candidates")
    if not isinstance(candidates, list) or not candidates:
        return None

    ids = [c.get("employee_id") for c in candidates if isinstance(c, dict) and c.get("employee_id")]
    if not ids:
        return None
    labels = {
        c.get("employee_id"): (
            f"{c.get('full_name')} — {c.get('job_title')}" if c.get("job_title") else str(c.get("full_name"))
        )
        for c in candidates
        if isinstance(c, dict) and c.get("employee_id")
    }
    suggested_id = payload.get("suggested_cover_employee_id")
    default_index = ids.index(suggested_id) if suggested_id in ids else 0
    return ids, labels, default_index


def _decide(
    approval_id: str,
    decision: str,
    note: str,
    person: str = "",
    cover_employee_id: str | None = None,
) -> None:
    resp = api.request(
        "POST",
        f"/approvals/{approval_id}/decide",
        json={
            "decision": decision,
            "decision_note": note or None,
            "cover_employee_id": cover_employee_id if decision == "approve" else None,
        },
    )
    try:
        api.raise_for_api(resp)
        who = person or "this request"
        if decision == "approve":
            st.success(i18n.t("inbox.approved_msg", who=who))
        else:
            st.success(i18n.t("inbox.sent_back_msg", who=who))
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))


def _employee_names(employee_ids: list) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        listing = api.raise_for_api(api.request("GET", "/employees")) or []
    except RuntimeError:
        listing = []
    if isinstance(listing, list):
        for row in listing:
            eid = str(row.get("employee_id") or "")
            if eid:
                names[eid] = row.get("full_name") or eid
    for raw_id in employee_ids:
        eid = str(raw_id or "")
        if not eid or eid in names:
            continue
        try:
            detail = api.raise_for_api(api.request("GET", f"/employees/{eid}")) or {}
            names[eid] = detail.get("full_name") or eid
        except RuntimeError:
            names[eid] = eid
    return names


def _masked_iban(iban) -> str:
    text = str(iban or "").replace(" ", "")
    return f"•••• {text[-4:]}" if len(text) >= 4 else "—"


def _localized_summary(action_type: str | None, payload: dict | None) -> str:
    """One-line request summary in the UI language, built from the
    proposal payload. pending_approvals.action_summary is written once, in
    English, by the Manager agent; this replaces it for the self-service
    request types. Returns "" for types it doesn't know, so callers fall
    back to the stored summary."""
    payload = payload if isinstance(payload, dict) else {}
    if action_type == "leave_request":
        leave_type = str(payload.get("leave_type") or "").lower()
        type_label = i18n.t(f"leave.type_{leave_type}")
        return i18n.t(
            "records.summary_leave",
            type=type_label if type_label != f"leave.type_{leave_type}" else leave_type or "—",
            start=_friendly_when(payload.get("start_date")) or "?",
            end=_friendly_when(payload.get("end_date")) or "?",
        )
    if action_type == "personal_info_update":
        field = str(payload.get("field_name") or "")
        field_label = i18n.t(f"summary.field_{field}")
        if field_label == f"summary.field_{field}":
            field_label = field or "—"
        return i18n.t("summary.personal_info", field=field_label, value=payload.get("new_value") or "—")
    if action_type == "bank_update":
        return i18n.t("summary.bank", iban=_masked_iban(payload.get("new_iban")))
    if action_type == "certificate_request":
        return i18n.t("summary.certificate", name=payload.get("full_name") or payload.get("employee_id") or "—")
    return ""


def _action_label(row: dict, proposal: dict | None = None) -> str:
    if proposal:
        payload = proposal.get("payload_json")
        localized = _localized_summary(proposal.get("action_type"), payload)
        if localized:
            return localized
    summary = str(row.get("action_summary") or "").strip()
    if summary:
        return summary
    return i18n.t("inbox.needs_decision")


def _status_label(status: str) -> str:
    key = {"pending": "status.pending", "approved": "status.approved", "rejected": "status.sent_back"}.get(status)
    if key:
        return i18n.t(key)
    return status.title()


def _risk_label(level: str | None) -> tuple[str, str]:
    key = (level or "").strip().lower()
    if key == "high":
        return i18n.t("risk.high"), "high"
    if key == "medium":
        return i18n.t("risk.medium"), "medium"
    if key == "low":
        return i18n.t("risk.low"), "low"
    return (level or i18n.t("risk.unknown")), "low"


def _friendly_when(raw: str | None) -> str:
    """"25 Sep 2026" / "25 سبتمبر 2026" (ISO or DD-MM-YYYY input)."""
    if not raw:
        return ""
    return i18n.format_date(raw) if i18n.parse_date(raw) else str(raw)[:10]


def _approval_card_html(row: dict, person: str) -> str:
    risk_text, risk_class = _risk_label(row.get("risk_level"))
    when = _friendly_when(row.get("created_at"))
    employee_id = html.escape(str(row.get("employee_id") or ""))
    summary = html.escape(_action_label(row))
    status_pill = styles.status_pill_html(row.get("status") or "pending", styles.status_pills())
    decided = _friendly_when(row.get("decided_at"))
    meta_bits = [i18n.t("inbox.meta_employee", id=employee_id) if employee_id else ""]
    if when:
        meta_bits.append(i18n.t("inbox.meta_raised", when=when))
    if decided:
        meta_bits.append(i18n.t("inbox.meta_decided", when=decided))
    meta = " · ".join(bit for bit in meta_bits if bit)
    return (
        '<div class="approval-card">'
        '<div class="approval-card-top">'
        f'<span class="risk-pill risk-pill--{risk_class}">{html.escape(risk_text)}</span>'
        f"{status_pill}"
        "</div>"
        f'<div class="approval-who">{html.escape(person)}</div>'
        f'<p class="approval-summary">{summary}</p>'
        f'<div class="approval-meta">{html.escape(meta)}</div>'
        "</div>"
    )


def _proposal_map() -> dict:
    try:
        items = api.raise_for_api(api.request("GET", "/proposed-actions")) or []
    except RuntimeError:
        return {}
    if not isinstance(items, list):
        return {}
    return {
        item.get("proposal_id"): item
        for item in items
        if isinstance(item, dict) and item.get("proposal_id")
    }


def _render_proposal_details(item: dict | None) -> None:
    if not item:
        return
    payload = item.get("payload_json")
    payload = payload if isinstance(payload, dict) else {}
    action_type = str(item.get("action_type") or "")
    key_prefix = str(item.get("proposal_id") or "proposal")

    st.caption(i18n.t("detail.request_details"))
    if action_type == "leave_request":
        _render_leave_proposal_details(payload, key_prefix)
    elif action_type == "new_hire":
        _render_new_hire_details(payload)
    elif action_type == "termination":
        _render_termination_details(payload)
    else:
        _render_generic_proposal_details(payload)

    related = item.get("related_request_id")
    if related and action_type == "leave_request":
        st.caption(i18n.t("detail.linked_leave", id=related))


def _render_leave_proposal_details(payload: dict, key_prefix: str) -> None:
    rows = [
        (i18n.t("detail.leave_type"), str(payload.get("leave_type") or "").title()),
        (i18n.t("detail.start_date"), _friendly_when(payload.get("start_date")) or payload.get("start_date")),
        (i18n.t("detail.end_date"), _friendly_when(payload.get("end_date")) or payload.get("end_date")),
        (i18n.t("detail.days"), payload.get("days")),
        (i18n.t("detail.reason"), payload.get("reason")),
        (i18n.t("detail.suggested_cover"), payload.get("suggested_cover_employee_name")),
    ]
    if payload.get("assigned_cover_employee_name"):
        rows.append((i18n.t("detail.assigned_cover"), payload.get("assigned_cover_employee_name")))
    styles.detail_card(rows)
    candidates = payload.get("cover_candidates")
    if isinstance(candidates, list):
        suggested_id = payload.get("suggested_cover_employee_id")
        others = [
            c
            for c in candidates
            if isinstance(c, dict) and c.get("employee_id") != suggested_id
        ]
        if others:
            with st.popover(i18n.t("detail.other_cover_options", n=len(others))):
                styles.data_table(
                    [{"name": c.get("full_name") or "—", "role": c.get("job_title") or "—"} for c in others],
                    [("name", i18n.t("detail.col_name")), ("role", i18n.t("detail.col_role"))],
                    key=f"covercandidates_{key_prefix}",
                )


def _staffing_label(proposal: dict | None) -> str:
    """Readable inbox label for new_hire / termination. Manager's generic
    action_summary for these is "<action_type> for <employee_id>", and a
    new_hire's employee_id is the HR requester, not the new employee."""
    if not proposal:
        return ""
    payload = proposal.get("payload_json")
    payload = payload if isinstance(payload, dict) else {}
    action_type = proposal.get("action_type")
    if action_type == "new_hire":
        hire = payload.get("new_employee") or {}
        return (
            f"{i18n.t('staff.add_employee')}: {hire.get('full_name') or '—'} — "
            f"{hire.get('job_title') or '—'} ({hire.get('department_id') or '—'})"
        )
    if action_type == "termination":
        termination_type = payload.get("termination_type")
        return (
            f"{i18n.t('staff.terminate_employee')}: {payload.get('employee_id') or '—'} — "
            f"{i18n.t(f'term.{termination_type}') if termination_type else '—'} — "
            f"{_friendly_when(payload.get('termination_date')) or '—'}"
        )
    return ""


def _render_new_hire_details(payload: dict) -> None:
    hire = payload.get("new_employee") or {}
    styles.detail_card(
        [
            (i18n.t("detail.new_employee"), hire.get("full_name")),
            (i18n.t("staff.job_title"), hire.get("job_title")),
            (i18n.t("staff.job_grade"), hire.get("job_grade")),
            (i18n.t("staff.department"), f"{_department_label(hire.get('department_id'), hire.get('department_name'))} ({hire.get('department_id')})"),
            (i18n.t("staff.manager"), hire.get("manager_id")),
            (i18n.t("staff.gender"), hire.get("gender")),
            (i18n.t("staff.nationality"), hire.get("nationality")),
            (i18n.t("staff.employment_type"), hire.get("employment_type")),
            (i18n.t("staff.hire_date"), _friendly_when(hire.get("hire_date"))),
            (i18n.t("staff.basic_salary"), hire.get("basic_salary")),
            (i18n.t("staff.housing_allowance"), hire.get("housing_allowance")),
            (i18n.t("staff.transport_allowance"), hire.get("transport_allowance")),
            (i18n.t("staff.email"), hire.get("email")),
            (i18n.t("staff.mobile"), hire.get("mobile")),
            (i18n.t("detail.cv"), payload.get("cv_filename")),
            (i18n.t("detail.requested_by"), payload.get("requested_by")),
        ]
    )


def _article_80_rows(article_80: dict | None) -> list[tuple[str, object]]:
    """Ground (with citation), details and confirmed procedures."""
    if not article_80:
        return []
    rows = [
        (
            i18n.t("art80.ground"),
            i18n.t(
                "art80.ground_value",
                n=article_80.get("ground_number"),
                text=article_80.get("ground_text"),
                article=article_80.get("article"),
                law_id=article_80.get("law_id"),
            ),
        ),
        (i18n.t("art80.details_label"), article_80.get("details")),
    ]
    for procedure in article_80.get("procedures") or []:
        rows.append(
            (
                i18n.t("art80.procedures"),
                i18n.t(
                    "art80.procedure_value",
                    label=i18n.t(f"art80.req_{procedure.get('kind')}"),
                    date=_friendly_when(procedure.get("date")) or "✓",
                ),
            )
        )
    return rows


def _render_termination_details(payload: dict) -> None:
    termination_type = payload.get("termination_type")
    article_80 = payload.get("article_80")
    styles.detail_card(
        [
            (i18n.t("staff.employee"), payload.get("employee_id")),
            (
                i18n.t("staff.termination_type"),
                i18n.t(f"term.{termination_type}") if termination_type else None,
            ),
            (i18n.t("staff.termination_date"), _friendly_when(payload.get("termination_date"))),
            *(_article_80_rows(article_80) if article_80 else [(i18n.t("detail.reason"), payload.get("reason"))]),
            (
                i18n.t("profile.notice"),
                i18n.t("notice.waived_detail", note=payload.get("notice_waiver_note") or "—")
                if payload.get("notice_waived")
                else (
                    i18n.t("notice.days", days=payload.get("notice_days_required"))
                    if payload.get("notice_days_required")
                    else None
                ),
            ),
            (i18n.t("detail.requested_by"), payload.get("requested_by")),
        ]
    )
    if payload.get("final_settlement"):
        _render_final_settlement(payload["final_settlement"])


def _render_generic_proposal_details(payload: dict) -> None:
    pairs = [
        (str(key).replace("_", " ").title(), value)
        for key, value in payload.items()
        if value not in (None, "")
    ]
    if pairs:
        styles.detail_card(pairs)


def page_users() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("users.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("users.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        users = api.raise_for_api(api.request("GET", "/users"))
        roles = api.raise_for_api(api.request("GET", "/users/roles"))
    except RuntimeError as exc:
        st.error(str(exc))
        return

    rows = [
        {
            "user_id": u.get("user_id") or "—",
            "employee_id": u.get("employee_id") or "—",
            "username": u.get("username") or "—",
            "role": str(u.get("role") or "").replace("_", " ").title() or "—",
            "status": "active" if u.get("is_active") else "inactive",
            "last_login_at": _friendly_when(u.get("last_login_at")) or "Never",
        }
        for u in (users or [])
    ]
    styles.data_table(
        rows,
        [
            ("user_id", i18n.t("users.col_user_id")),
            ("employee_id", i18n.t("users.col_employee_id")),
            ("username", i18n.t("users.col_username")),
            ("role", i18n.t("users.col_role")),
            ("status", i18n.t("users.col_status")),
            ("last_login_at", i18n.t("users.col_last_login")),
        ],
        status_key="status",
        key="users",
        empty_message=i18n.t("users.empty"),
    )
    st.caption(i18n.t("users.roles_caption", roles=", ".join(r["role_name"] for r in (roles or []))))
    c1, c2, c3 = st.columns(3)
    user_id = c1.text_input(i18n.t("users.field_user_id"))
    role = c2.selectbox(i18n.t("users.field_role"), ["employee", "hr_specialist", "hr_manager", "admin"])
    active = c3.checkbox(i18n.t("users.field_active"), value=True)
    if st.button(i18n.t("users.update_button"), type="primary") and user_id:
        resp = api.request(
            "PATCH",
            f"/users/{user_id}",
            json={"role": role, "is_active": active},
        )
        try:
            api.raise_for_api(resp)
            st.success(i18n.t("users.saved"))
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))


def page_audit() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("audit.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("audit.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        entries = api.raise_for_api(api.request("GET", "/audit"))
    except RuntimeError as exc:
        st.error(str(exc))
        return

    rows = [
        {
            "timestamp": _friendly_when(e.get("timestamp")) or e.get("timestamp") or "—",
            "actor": e.get("actor") or "—",
            "event_type": str(e.get("event_type") or "").replace("_", " ").title() or "—",
            "employee_id": e.get("employee_id") or "—",
            "details": e.get("details") or "—",
        }
        for e in (entries or [])
    ]
    styles.data_table(
        rows,
        [
            ("timestamp", i18n.t("audit.col_time")),
            ("actor", i18n.t("audit.col_user")),
            ("event_type", i18n.t("audit.col_event")),
            ("employee_id", i18n.t("audit.col_employee")),
            ("details", i18n.t("audit.col_details")),
        ],
        key="audit",
        empty_message=i18n.t("audit.empty"),
    )


def page_payroll() -> None:
    """HR manager/admin: download the all-employee payroll PDF for a
    chosen month. Restored from commit 354eb95 ("Payroll feature") — real
    GET /payroll/periods + GET /payroll/monthly/{period}/pdf, unchanged;
    only the UI chrome is new (month shown as "June 2026", a single
    Download PDF control, a file card, a confidentiality banner)."""
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("payroll.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("payroll.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="yz-confidential-banner">'
        f'<strong>{html.escape(i18n.t("payroll.confidentiality_title"))}</strong> '
        f'{html.escape(i18n.t("payroll.confidentiality_body"))}'
        "</div>",
        unsafe_allow_html=True,
    )

    try:
        periods_data = api.raise_for_api(api.request("GET", "/payroll/periods"))
    except RuntimeError as exc:
        st.error(str(exc))
        return
    periods = (periods_data or {}).get("periods") or []
    if not periods:
        st.info(i18n.t("payroll.empty"))
        return

    def _format_period(p: str) -> str:
        try:
            return datetime.strptime(p, "%Y-%m").strftime("%B %Y")
        except ValueError:
            return p

    period = st.selectbox(i18n.t("payroll.month_label"), periods, index=0, format_func=_format_period)

    # A generated PDF is only valid for the period it was generated for —
    # drop it the moment the month selection changes, so the "one Download
    # PDF control" never silently offers stale bytes for a different month.
    generated = st.session_state.get("payroll_pdf")
    if generated and generated.get("period") != period:
        st.session_state.pop("payroll_pdf", None)
        generated = None

    if generated:
        file_name = i18n.t("payroll.file_name", period=period)
        st.markdown(
            '<div class="yz-file-card">'
            '<div class="yz-file-card-icon">📄</div>'
            "<div>"
            f'<div class="yz-file-card-name">{html.escape(file_name)}</div>'
            f'<div class="yz-file-card-status">{html.escape(i18n.t("payroll.file_ready"))}</div>'
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.download_button(
            i18n.t("payroll.download_button"),
            data=generated["bytes"],
            file_name=file_name,
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        # One button, one label, throughout: this same control fetches the
        # real PDF on first click, then (after the resulting rerun) the
        # branch above takes over and renders it as a real download button
        # under the identical "Download PDF" label — st.download_button
        # can't lazily fetch on its own click, so this is the honest
        # two-step-but-one-visible-control shape Streamlit allows.
        if st.button(i18n.t("payroll.download_button"), use_container_width=True):
            with st.spinner(i18n.t("payroll.generating")):
                pdf_response = api.request("GET", f"/payroll/monthly/{period}/pdf")
            if pdf_response.status_code >= 400:
                try:
                    detail = pdf_response.json().get("detail", pdf_response.text)
                except Exception:
                    detail = pdf_response.text
                st.error(f"{pdf_response.status_code}: {detail}")
            else:
                st.session_state["payroll_pdf"] = {"period": period, "bytes": pdf_response.content}
                st.rerun()


def page_growth_opportunities() -> None:
    """Employee: department experience gaps this employee is a close-fit
    candidate for, with a CV upload that generates a personalized
    development plan. Restored from commit 354eb95 — real
    GET /growth/opportunities + POST /growth/opportunities/{skill_id}/cv,
    unchanged; only the UI chrome is new."""
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("growth.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("growth.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        data = api.raise_for_api(api.request("GET", "/growth/opportunities"))
    except RuntimeError as exc:
        st.error(str(exc))
        return
    opportunities = (data or {}).get("opportunities") or []
    if not opportunities:
        st.info(i18n.t("growth.empty"))
        return

    for item in opportunities:
        skill_id = item.get("skill_id")
        skill_name = item.get("skill_name") or "—"
        with st.container(key=f"growth_card_{skill_id}"):
            st.markdown(
                f'<div class="yz-growth-card-title">{html.escape(skill_name)}</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                i18n.t(
                    "growth.missing_note",
                    current=item.get("current_headcount"),
                    job_title=item.get("current_job_title") or "—",
                )
            )

            existing_plan = item.get("plan")
            if existing_plan:
                st.markdown(existing_plan.get("plan_text") or "")
                st.caption(
                    i18n.t(
                        "growth.generated_from",
                        filename=existing_plan.get("cv_filename") or "your CV",
                        date=existing_plan.get("created_at") or "—",
                    )
                )
                upload_label = i18n.t("growth.upload_replace")
            else:
                upload_label = i18n.t("growth.upload_new")

            cv_file = st.file_uploader(upload_label, type="pdf", key=f"cv_upload_{skill_id}")

            if st.button(
                i18n.t("growth.generate_button"),
                key=f"generate_plan_{skill_id}",
                disabled=cv_file is None,
            ):
                upload_response = api.request(
                    "POST",
                    f"/growth/opportunities/{skill_id}/cv",
                    files={"cv": (cv_file.name, cv_file.getvalue(), "application/pdf")},
                    timeout=120.0,
                )
                try:
                    api.raise_for_api(upload_response)
                except RuntimeError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        st.write("")


def page_regulations() -> None:
    """Regulations page (Phase 4): app/ui/regulations_view.py."""
    from app.ui import regulations_view

    regulations_view.page()
