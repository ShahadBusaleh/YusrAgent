from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from fractions import Fraction

import httpx
import streamlit as st

from app.ui import api_client as api
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


def _submit_staffing(query: str, prefix: str) -> None:
    """Send through the same _call_agent -> POST /agent/query path as Ask
    Yusor, then show the outcome on this page (flash survives the rerun)."""
    history = st.session_state.setdefault("chat_history", [])
    before = len(history)
    payload = _call_agent(query)
    if payload is None:
        # _call_agent reports failures into the chat history; they belong
        # on this page instead.
        failures = history[before:]
        del history[before:]
        detail = failures[-1]["content"] if failures else "Request failed."
        st.session_state["staff_flash"] = ("error", detail)
    else:
        response = _format_agent_text(payload)
        submitted = _SUBMITTED_RE.search(response)
        if payload.get("status") == "PASS" and submitted:
            st.session_state["staff_flash"] = (
                "success",
                i18n.t("staffing.submitted", inbox=i18n.t("nav.approvals"), proposal=submitted.group(1)),
            )
            _reset_staff_form(prefix)
        else:
            st.session_state["staff_flash"] = ("warning", response)
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


def _employee_picker(
    label: str,
    key: str,
    *,
    department_id: str | None = None,
    optional: bool = False,
    exclude_id: str | None = None,
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
    chosen = st.selectbox(
        label,
        options,
        key=f"{key}_pick",
        format_func=lambda eid: i18n.t("staff.none")
        if eid is None
        else f"{eid} — {by_id[eid].get('full_name') or ''} — {by_id[eid].get('job_title') or ''}",
    )
    return by_id.get(chosen)


def _english_only_error(fields: list[tuple[str, str]]) -> bool:
    """Arabic text would send the whole query through the LLM translation
    step, which may rewrite the keys; records are stored in English."""
    for label, value in fields:
        if _ARABIC_RE.search(value or ""):
            st.error(i18n.t("staff.english_only", field=label))
            return True
    return False


def _render_new_hire_form() -> None:
    with st.container(key="staff_card_hire"):
        c1, c2 = st.columns(2)
        full_name = c1.text_input(i18n.t("staff.full_name"), key="hire_full_name")
        job_title = c2.text_input(i18n.t("staff.job_title"), key="hire_job_title")
        gender = c1.selectbox(i18n.t("staff.gender"), ["Female", "Male"], key="hire_gender")
        nationality = c2.text_input(i18n.t("staff.nationality"), value="Saudi", key="hire_nationality")
        email = c1.text_input(i18n.t("staff.email"), key="hire_email")
        mobile = c2.text_input(i18n.t("staff.mobile"), key="hire_mobile")

        departments = _load_departments()
        department_id = None
        if departments:
            names = {d["department_id"]: d.get("department_name") for d in departments}
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
            )
        else:
            manager = _employee_picker(i18n.t("staff.manager"), "hire_manager")
            if manager:
                department_id = manager.get("department_id")
                st.caption(
                    i18n.t(
                        "staff.department_from_manager",
                        department=f"{manager.get('department_name')} ({department_id})",
                    )
                )

        c3, c4 = st.columns(2)
        employment_type = c3.selectbox(
            i18n.t("staff.employment_type"), _EMPLOYMENT_TYPES, key="hire_employment_type"
        )
        hire_date = c4.date_input(
            i18n.t("staff.hire_date"), value=date.today(), format="DD-MM-YYYY", key="hire_date"
        )
        s1, s2, s3 = st.columns(3)
        basic = s1.number_input(i18n.t("staff.basic_salary"), min_value=0.0, step=100.0, key="hire_basic")
        housing = s2.number_input(i18n.t("staff.housing_allowance"), min_value=0.0, step=100.0, key="hire_housing")
        transport = s3.number_input(i18n.t("staff.transport_allowance"), min_value=0.0, step=50.0, key="hire_transport")

        submit = st.button(i18n.t("staff.submit"), type="primary", key="hire_submit")

    if not submit:
        return

    missing = [
        label
        for label, ok in (
            (i18n.t("staff.full_name"), full_name.strip()),
            (i18n.t("staff.job_title"), job_title.strip()),
            (i18n.t("staff.department"), department_id),
            (i18n.t("staff.basic_salary"), basic > 0),
        )
        if not ok
    ]
    if missing:
        st.error(i18n.t("staff.required", fields=", ".join(missing)))
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
            ("manager_id", (manager or {}).get("employee_id")),
            ("employment_type", employment_type),
            ("hire_date", hire_date.strftime("%d-%m-%Y")),
            ("basic_salary", f"{basic:g}"),
            ("housing_allowance", f"{housing:g}"),
            ("transport_allowance", f"{transport:g}"),
        ],
    )
    _submit_staffing(query, "hire_")


def _render_termination_form() -> None:
    me = st.session_state.get("me") or {}
    with st.container(key="staff_card_term"):
        employee = _employee_picker(
            i18n.t("staff.employee"), "term_employee", exclude_id=me.get("employee_id")
        )
        c1, c2 = st.columns(2)
        termination_type = c1.selectbox(
            i18n.t("staff.termination_type"),
            [None, *_TERMINATION_TYPES],
            key="term_type",
            format_func=lambda v: "—" if v is None else i18n.t(f"term.{v}"),
        )
        termination_date = c2.date_input(
            i18n.t("staff.termination_date"), value=date.today(), format="DD-MM-YYYY", key="term_date"
        )
        reason = st.text_area(i18n.t("staff.reason"), key="term_reason")

        submit = st.button(i18n.t("staff.submit"), type="primary", key="term_submit")

    if not submit:
        return

    missing = [
        label
        for label, ok in (
            (i18n.t("staff.employee"), employee),
            (i18n.t("staff.termination_type"), termination_type),
            (i18n.t("staff.reason"), reason.strip()),
        )
        if not ok
    ]
    if missing:
        st.error(i18n.t("staff.required", fields=", ".join(missing)))
        return
    if _english_only_error([(i18n.t("staff.reason"), reason)]):
        return

    query = _staffing_query(
        "Terminate employee",
        [
            ("employee_id", employee.get("employee_id")),
            ("termination_type", termination_type),
            ("termination_date", termination_date.strftime("%d-%m-%Y")),
            ("reason", reason.strip()),
        ],
    )
    _submit_staffing(query, "term_")


def _render_my_staffing_requests() -> None:
    """New-hire / termination requests this user submitted, from the real
    GET /proposed-actions (staff roles get every proposal; payload.requested_by
    identifies the submitter)."""
    me_id = (st.session_state.get("me") or {}).get("employee_id")
    try:
        items = api.raise_for_api(api.request("GET", "/proposed-actions")) or []
    except RuntimeError as exc:
        st.error(str(exc))
        items = []

    mine = []
    for item in items if isinstance(items, list) else []:
        payload = item.get("payload_json")
        payload = payload if isinstance(payload, dict) else {}
        if item.get("action_type") in {"new_hire", "termination"} and payload.get("requested_by") == me_id:
            mine.append((item, payload))

    names = _employee_names(
        [payload.get("employee_id") for item, payload in mine if item.get("action_type") == "termination"]
    )
    rows = []
    for item, payload in mine:
        if item.get("action_type") == "new_hire":
            kind = i18n.t("staffing.tab_hire")
            person = (payload.get("new_employee") or {}).get("full_name")
        else:
            kind = i18n.t("staffing.tab_term")
            eid = str(payload.get("employee_id") or "")
            person = f"{names.get(eid) or eid} ({eid})" if eid else None
        rows.append(
            {
                "type": kind,
                "employee": person,
                "date": _friendly_when(item.get("created_at")),
                "status": item.get("status"),
            }
        )

    styles.data_table(
        rows,
        [
            ("type", i18n.t("staffing.col_type")),
            ("employee", i18n.t("staffing.col_employee")),
            ("date", i18n.t("staffing.col_date")),
            ("status", i18n.t("staffing.col_status")),
        ],
        status_key="status",
        # proposed_actions uses "pending_approval" until decided.
        status_styles={"pending_approval": styles.status_pills()["pending"]},
        key="staffing_requests",
        empty_message=i18n.t("staffing.empty"),
    )


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

    flash = st.session_state.pop("staff_flash", None)
    if flash:
        kind, text = flash
        {"success": st.success, "warning": st.warning}.get(kind, st.error)(text)

    tab_hire, tab_term = st.tabs([i18n.t("staffing.tab_hire"), i18n.t("staffing.tab_term")])
    with tab_hire:
        _render_new_hire_form()
    with tab_term:
        _render_termination_form()

    st.markdown(f"#### {html.escape(i18n.t('staffing.my_requests'))}")
    _render_my_staffing_requests()


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
                    if st.button(label, use_container_width=True):
                        _submit_query(query)
                        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    prompt = st.chat_input(i18n.t("chat.placeholder"))
    if prompt:
        _submit_query(prompt)
        st.rerun()


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

    pending_approvals: list[dict] = []
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
            "request": _action_label(r),
            "date": _friendly_when(r.get("created_at")) or "—",
            "status": (r.get("status") or "pending").lower(),
        }

    def _approval_row(r: dict) -> dict:
        return {
            "_kind": "approval",
            "_id": str(r.get("approval_id") or ""),
            "type": styles.type_badge_html("approval", i18n.t("inbox.type_approval")),
            "employee": names.get(str(r.get("employee_id") or "")) or r.get("employee_id") or "—",
            "request": _staffing_label(proposals.get(r.get("proposal_id"))) or _action_label(r),
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
        "Inbox filter", tab_full_labels, horizontal=True, key="inbox_tab", label_visibility="collapsed"
    )
    chosen_key = dict(zip(tab_full_labels, [k for k, _ in tabs])).get(chosen_full_label, "all")
    visible_rows = rows_by_tab.get(chosen_key, [])

    def _open_review(row: dict) -> None:
        st.session_state["inbox_open"] = (row["_kind"], row["_id"])

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
        st.divider()
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


def _render_approval_review(row: dict, names: dict, proposals: dict) -> None:
    approval_id = str(row.get("approval_id") or "")
    employee_id = str(row.get("employee_id") or "")
    person = names.get(employee_id) or employee_id or "—"
    status = (row.get("status") or "pending").lower()
    staffing_label = _staffing_label(proposals.get(row.get("proposal_id")))
    if staffing_label:
        row = {**row, "action_summary": staffing_label}

    with st.container(key=f"inbox_review_{approval_id}"):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            st.markdown(f"#### {html.escape(person)}")
        with top_r:
            if st.button(i18n.t("inbox.close"), key=f"close_{approval_id}", use_container_width=True):
                st.session_state.pop("inbox_open", None)
                st.rerun()

        st.markdown(_approval_card_html(row, person), unsafe_allow_html=True)

        if status == "pending":
            if st.button(i18n.t("inbox.explain_this"), key=f"explain_{approval_id}", use_container_width=True):
                try:
                    brief_response = api.request("GET", f"/approvals/{approval_id}/brief", timeout=120.0)
                    brief = api.raise_for_api(brief_response)
                    if isinstance(brief, dict):
                        st.session_state[f"approval_brief_{approval_id}"] = brief
                    else:
                        st.error("Unexpected Decision Brief response.")
                except RuntimeError as exc:
                    st.error(str(exc))

        brief = st.session_state.get(f"approval_brief_{approval_id}")
        if brief:
            _render_decision_brief(brief.get("brief") or {}, approval_id)

        proposal = proposals.get(row.get("proposal_id"))
        _render_proposal_details(proposal)

        if status != "pending":
            # Already decided — no decision form. Re-showing Approve/Send
            # back here previously let a stray click re-run the decision
            # (duplicate leave request, double-deducted balance).
            st.caption(
                f"{_status_label(status)} by {row.get('decided_by') or '—'} "
                f"on {row.get('decided_at') or '—'}"
            )
            if row.get("decision_note"):
                st.caption(f"Note: {row['decision_note']}")
        else:
            cover_options = _cover_candidate_options(proposal)
            with st.form(f"decide_{approval_id}"):
                cover_employee_id = None
                if cover_options:
                    ids, labels, default_index = cover_options
                    cover_employee_id = st.selectbox(
                        "Cover employee", ids, index=default_index, format_func=lambda eid: labels.get(eid, eid)
                    )
                note = st.text_area(
                    "Note", placeholder=i18n.t("inbox.note_placeholder"), label_visibility="collapsed"
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
    sentences = re.split(r"(?<=[.!?])\s+", clean_policy)
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
    return recommendation, explanation


def _render_decision_brief(decision_brief: dict, approval_id: str) -> None:
    st.markdown(f"### {i18n.t('inbox.decision_brief')}")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**{i18n.t('inbox.action')}**")
        st.write(str(decision_brief.get("action_type") or "—").replace("_", " ").title())
    with col_b:
        st.markdown(f"**{i18n.t('inbox.risk')}**")
        st.write(str(decision_brief.get("risk_level") or "—").upper())

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
    recommendation = str(policy.get("recommendation") or "").strip()
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
            st.markdown(f"#### {keyword}")
        if explanation:
            st.caption(i18n.t("inbox.reason"))
            st.write(explanation)

    reasons = manager.get("reasons") or []
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


def _render_grievance_review(g: dict, names: dict) -> None:
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
                st.rerun()

        styles.detail_card(
            [
                (i18n.t("detail.identity"), person),
                (i18n.t("detail.submitted"), _friendly_when(g.get("submitted_at")) or "—"),
            ]
        )
        st.write(g.get("complaint") or "No complaint provided.")

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
            response_note = st.text_area("Note", placeholder=i18n.t("inbox.note_placeholder"))
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
        "Grievance filter", tab_full_labels, horizontal=True, key="grievances_tab", label_visibility="collapsed"
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
        g = next((g for g in grievances if str(g.get("grievance_id") or "") == str(view_id)), None)
        if g:
            _render_grievance_readonly(g, names)


def _render_grievance_readonly(g: dict, names: dict) -> None:
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
            st.markdown("**HR note**")
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


def _action_label(row: dict) -> str:
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
    if not raw:
        return ""
    text = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.strftime("%d %b %Y")
    except ValueError:
        return str(raw)[:10]


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

    st.caption("Request details")
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
        st.caption(f"Linked leave request: {related}")


def _render_leave_proposal_details(payload: dict, key_prefix: str) -> None:
    rows = [
        (i18n.t("detail.leave_type"), str(payload.get("leave_type") or "").title()),
        (i18n.t("detail.start_date"), payload.get("start_date")),
        (i18n.t("detail.end_date"), payload.get("end_date")),
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
            f"{payload.get('termination_date') or '—'}"
        )
    return ""


def _render_new_hire_details(payload: dict) -> None:
    hire = payload.get("new_employee") or {}
    styles.detail_card(
        [
            (i18n.t("detail.new_employee"), hire.get("full_name")),
            (i18n.t("staff.job_title"), hire.get("job_title")),
            (i18n.t("staff.department"), f"{hire.get('department_name') or ''} ({hire.get('department_id')})"),
            (i18n.t("staff.manager"), hire.get("manager_id")),
            (i18n.t("staff.gender"), hire.get("gender")),
            (i18n.t("staff.nationality"), hire.get("nationality")),
            (i18n.t("staff.employment_type"), hire.get("employment_type")),
            (i18n.t("staff.hire_date"), hire.get("hire_date")),
            (i18n.t("staff.basic_salary"), hire.get("basic_salary")),
            (i18n.t("staff.housing_allowance"), hire.get("housing_allowance")),
            (i18n.t("staff.transport_allowance"), hire.get("transport_allowance")),
            (i18n.t("staff.email"), hire.get("email")),
            (i18n.t("staff.mobile"), hire.get("mobile")),
            (i18n.t("detail.requested_by"), payload.get("requested_by")),
        ]
    )


def _render_termination_details(payload: dict) -> None:
    termination_type = payload.get("termination_type")
    styles.detail_card(
        [
            (i18n.t("staff.employee"), payload.get("employee_id")),
            (
                i18n.t("staff.termination_type"),
                i18n.t(f"term.{termination_type}") if termination_type else None,
            ),
            (i18n.t("staff.termination_date"), payload.get("termination_date")),
            (i18n.t("detail.reason"), payload.get("reason")),
            (i18n.t("detail.requested_by"), payload.get("requested_by")),
        ]
    )


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
