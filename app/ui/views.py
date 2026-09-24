from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

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
            "request": _action_label(r),
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


def _render_decision_brief(decision_brief: dict, approval_id: str) -> None:
    st.markdown(f"### {i18n.t('inbox.decision_brief')}")
    st.caption(f"Action: {decision_brief.get('action_type') or '—'}")

    risk = decision_brief.get("risk_level")
    if risk:
        st.caption(f"Risk level: {risk}")

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
        st.write(recommendation)

    styles.render_sources(policy.get("sources") or [], key=f"brief_{approval_id}")

    manager = decision_brief.get("manager") or {}
    manager_response = str(manager.get("response") or "").strip()
    if manager_response:
        st.markdown(f"**{i18n.t('inbox.manager_assessment')}**")
        st.write(manager_response)

    reasons = manager.get("reasons") or []
    if reasons:
        st.markdown(f"**{i18n.t('inbox.notes')}**")
        for reason in reasons:
            st.write(f"- {reason}")


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
    else:
        _render_generic_proposal_details(payload)

    related = item.get("related_request_id")
    if related:
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


def _render_generic_proposal_details(payload: dict) -> None:
    pairs = [
        (str(key).replace("_", " ").title(), value)
        for key, value in payload.items()
        if value not in (None, "")
    ]
    if pairs:
        styles.detail_card(pairs)


def page_users() -> None:
    styles.hero(
        i18n.t("role.admin"),
        i18n.t("users.title"),
        i18n.t("users.subtitle"),
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
    st.caption("Roles: " + ", ".join(r["role_name"] for r in (roles or [])))
    c1, c2, c3 = st.columns(3)
    user_id = c1.text_input("User ID")
    role = c2.selectbox("Role", ["employee", "hr_specialist", "hr_manager", "admin"])
    active = c3.checkbox("Active", value=True)
    if st.button(i18n.t("users.update_button"), type="primary") and user_id:
        resp = api.request(
            "PATCH",
            f"/users/{user_id}",
            json={"role": role, "is_active": active},
        )
        try:
            api.raise_for_api(resp)
            st.success("Saved")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))


def page_audit() -> None:
    styles.hero(
        i18n.t("role.admin"),
        i18n.t("audit.title"),
        i18n.t("audit.subtitle"),
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
