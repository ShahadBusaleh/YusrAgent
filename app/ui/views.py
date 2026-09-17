from __future__ import annotations

import html
from datetime import date, datetime, timedelta

import streamlit as st

from app.api.routers.auth import me
from app.ui import api_client as api
from app.ui import styles


def _leave_query_examples() -> list[tuple[str, str]]:
    today = date.today()
    annual_start = today + timedelta(days=21)
    annual_end = annual_start + timedelta(days=2)
    sick_start = today + timedelta(days=3)
    sick_end = sick_start + timedelta(days=1)
    return [
        (
            "Request annual leave",
            f"I want to take annual leave from {annual_start.isoformat()} to "
            f"{annual_end.isoformat()}, can you find someone to cover for me?",
        ),
        (
            "Request sick leave",
            f"I need to take sick leave from {sick_start.isoformat()} to "
            f"{sick_end.isoformat()}.",
        ),
        (
            "Check my balance",
            "How many annual leave days do I have remaining?",
        ),
    ]


def page_chat() -> None:
    styles.hero(
        "Ask Yusor",
        "What can I help with?",
        "Ask a policy or leave question. Answers are grounded in HR facts and policy sources.",
    )
    st.caption(
        "To request leave, include the **leave type** and **exact start and end "
        "dates** (YYYY-MM-DD) — Yusor will check your balance, suggest someone "
        "to cover for you, and send it for approval automatically."
    )
    example_cols = st.columns(3)
    for col, (label, text) in zip(example_cols, _leave_query_examples()):
        if col.button(label, use_container_width=True):
            st.session_state["chat_query"] = text

    with st.form("ask_form"):
        query = st.text_area(
            "Your request",
            height=150,
            key="chat_query",
            placeholder="e.g. How many annual leave days do I have remaining?",
        )
        submitted = st.form_submit_button("Submit request", type="primary")
    if not (submitted and query.strip()):
        return

    api.refresh_session()
    response = api.request(
        "POST",
        "/agent/query",
        json={"query": query.strip()},
        timeout=120.0,
    )
    if response.status_code == 501:
        try:
            detail = response.json().get("detail", "Manual implementation pending")
        except Exception:
            detail = "Manual implementation pending"
        st.info(detail)
        return

    if response.status_code == 401:
        st.error("Your session expired. Sign out, sign in again, then resubmit.")
        return
    try:
        payload = api.raise_for_api(response)
    except RuntimeError as exc:
        st.error(str(exc))
        return

    if not isinstance(payload, dict):
        st.error("Unexpected agent response.")
        return

    status = str(payload.get("status") or "").strip() or "UNKNOWN"
    answer_text = str(payload.get("response") or "").strip()
    badge_color = {"PASS": "#7eb4e0", "FAIL": "#e08a7e", "REPLAN": "#d7c6a4"}.get(
        status, "#c5d0d8"
    )
    st.markdown(
        f'<span class="yusor-role" style="background:{badge_color}">{status}</span>',
        unsafe_allow_html=True,
    )

    if status == "FAIL" and not answer_text:
        st.caption("Blocked by governance — see status.")
    elif answer_text:
        st.markdown(answer_text)

    source_ids: list[str] = []
    source_texts: list[tuple[str, str]] = []
    for item in payload.get("sources") or []:
        if isinstance(item, str):
            if item.strip():
                source_ids.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        sid = item.get("id") or item.get("source_id")
        sid = str(sid).strip() if sid else ""
        if sid:
            source_ids.append(sid)
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            source_texts.append((sid or "source", text.strip()))

    if source_ids:
        st.markdown("**Sources:** " + " · ".join(f"`{sid}`" for sid in source_ids))
    if source_texts:
        with st.expander("Source excerpts"):
            for sid, text in source_texts:
                st.caption(sid)
                st.write(text)


def page_leave() -> None:
    styles.hero(
        "Self-service",
        "My leave",
        "Your current balance and request history. To submit a new request, "
        "ask Yusor in the chat — it will suggest someone to cover for you "
        "and send it for approval.",
    )
    balance_resp = api.request("GET", "/leave/balance")
    try:
        balance = api.raise_for_api(balance_resp)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    cols = st.columns(3, gap="medium")
    cols[0].markdown(
        styles.stat_card("Annual", balance.get("annual_remaining"), "blue"),
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        styles.stat_card("Sick", balance.get("sick_remaining"), "olive"),
        unsafe_allow_html=True,
    )
    cols[2].markdown(
        styles.stat_card("Emergency", balance.get("emergency_remaining"), "coffee"),
        unsafe_allow_html=True,
    )

    as_of = _friendly_when(balance.get("as_of_date"))
    with st.expander("Balance breakdown" + (f" · as of {as_of}" if as_of else "")):
        st.dataframe(
            [
                {
                    "Type": kind,
                    "Entitlement": balance.get(f"{key}_entitlement"),
                    "Used": balance.get(f"{key}_used"),
                    "Remaining": balance.get(f"{key}_remaining"),
                }
                for kind, key in (
                    ("Annual", "annual"),
                    ("Sick", "sick"),
                    ("Emergency", "emergency"),
                )
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("My requests")
    reqs = api.raise_for_api(api.request("GET", "/leave/requests")) or []
    if not reqs:
        st.caption("No leave requests yet.")
    else:
        rows = [
            {
                "leave_type": str(r.get("leave_type") or "").title(),
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
                "days": r.get("days"),
                "reason": r.get("reason") or "—",
                "status": _status_label(str(r.get("status") or "")),
                "submitted_at": _friendly_when(r.get("submitted_at")),
                "decided_at": _friendly_when(r.get("decided_at")),
            }
            for r in reqs
        ]
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            column_order=[
                "leave_type",
                "start_date",
                "end_date",
                "days",
                "reason",
                "status",
                "submitted_at",
                "decided_at",
            ],
            column_config={
                "leave_type": st.column_config.TextColumn("Type"),
                "start_date": st.column_config.TextColumn("Start"),
                "end_date": st.column_config.TextColumn("End"),
                "days": st.column_config.NumberColumn("Days", format="%.1f"),
                "reason": st.column_config.TextColumn("Reason"),
                "status": st.column_config.TextColumn("Status"),
                "submitted_at": st.column_config.TextColumn("Submitted"),
                "decided_at": st.column_config.TextColumn("Decided"),
            },
        )
    st.caption("Need to submit a new request? Ask Yusor in the chat.")

"""
def page_employees() -> None:
    role = (st.session_state.get("me") or {}).get("role")
    if role == "hr_manager":
        styles.hero(
            "Your team",
            "People",
            "Look someone up when a case needs context. You can still fix a phone number or email here.",
        )
    else:
        styles.hero(
            "HR specialist",
            "Employee records",
            "Search the directory, open a profile, and update low-risk contact fields.",
        )
    q = st.text_input("Search by name, id, or email", placeholder="EMP0001 or Sara")
    listing = api.request("GET", "/employees", params={"q": q} if q else {})
    try:
        rows = api.raise_for_api(listing) or []
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not rows:
        st.caption("No matching employees.")
    else:
        st.dataframe(
            [
                {
                    "employee_id": r.get("employee_id"),
                    "full_name": r.get("full_name"),
                    "job_title": r.get("job_title"),
                    "department_name": r.get("department_name"),
                    "employment_status": str(r.get("employment_status") or "").title(),
                    "email": r.get("email"),
                    "mobile": r.get("mobile"),
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
            column_order=[
                "employee_id",
                "full_name",
                "job_title",
                "department_name",
                "employment_status",
                "email",
                "mobile",
            ],
            column_config={
                "employee_id": st.column_config.TextColumn("ID"),
                "full_name": st.column_config.TextColumn("Name"),
                "job_title": st.column_config.TextColumn("Title"),
                "department_name": st.column_config.TextColumn("Department"),
                "employment_status": st.column_config.TextColumn("Status"),
                "email": st.column_config.TextColumn("Email"),
                "mobile": st.column_config.TextColumn("Mobile"),
            },
        )

    employee_id = st.text_input("Employee ID to view / update")
    if not employee_id:
        return
    detail_resp = api.request("GET", f"/employees/{employee_id}")
    try:
        detail = api.raise_for_api(detail_resp)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    _render_employee_profile(detail)
    with st.form("profile_update"):
        c1, c2 = st.columns(2)
        mobile = c1.text_input("Mobile", value=detail.get("mobile") or "")
        email = c2.text_input("Email", value=detail.get("email") or "")
        city = c1.text_input("City", value=detail.get("city") or "")
        address = c2.text_input("Address", value=detail.get("address") or "")
        save = st.form_submit_button("Save profile fields", type="primary")
    if save:
        resp = api.request(
            "PATCH",
            f"/employees/{employee_id}",
            json={"mobile": mobile, "email": email, "city": city, "address": address},
        )
        try:
            api.raise_for_api(resp)
            st.success("Updated")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))
"""

def page_employees() -> None:
    me = st.session_state.get("me") or {}
    role = me.get("role")

    hr_roles = {"hr_manager", "hr_specialist", "admin"}

    # ---------------------------------------------------------
    # HR USERS: require password before accessing employee data
    # ---------------------------------------------------------
    if role in hr_roles:

        styles.hero(
            "Employee records",
            "Verify your identity",
            "Enter your password before searching employee records.",
        )

        if not st.session_state.get("employee_records_verified", False):

            with st.form("verify_employee_records"):
                password = st.text_input(
                    "Password",
                    type="password",
                )

                verify = st.form_submit_button(
                    "Verify password",
                    type="primary",
                )

            if verify:
                response = api.request(
                    "POST",
                    "/auth/verify-password",
                    json={
                        "username": me.get("username"),
                        "password": password,
                    },
                )

                if response.status_code == 200:
                    st.session_state.employee_records_verified = True
                    st.rerun()
                else:
                    st.error("Invalid password.")

            return

        # -----------------------------------------------------
        # Password verified → now show employee search
        # -----------------------------------------------------

        q = st.text_input(
            "Search by name, id, or email",
            placeholder="EMP0001 or Sara",
        )

        listing = api.request(
            "GET",
            "/employees",
            params={"q": q} if q else {},
        )

        try:
            rows = api.raise_for_api(listing) or []
        except RuntimeError as exc:
            st.error(str(exc))
            return

        if not rows:
            st.caption("No matching employees.")
            return

        st.dataframe(
            [
                {
                    "employee_id": r.get("employee_id"),
                    "full_name": r.get("full_name"),
                    "job_title": r.get("job_title"),
                    "department_name": r.get("department_name"),
                    "employment_status": str(
                        r.get("employment_status") or ""
                    ).title(),
                    "email": r.get("email"),
                    "mobile": r.get("mobile"),
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )

        employee_id = st.text_input(
            "Employee ID to view / update"
        )

        if not employee_id:
            return

    else:
        # Employee behavior stays unchanged for now.
        return

    # ---------------------------------------------------------
    # Employee profile
    # ---------------------------------------------------------

    detail_resp = api.request(
        "GET",
        f"/employees/{employee_id}",
    )

    try:
        detail = api.raise_for_api(detail_resp)
    except RuntimeError as exc:
        st.error(str(exc))
        return

    _render_employee_profile(detail)

    with st.form("profile_update"):
        c1, c2 = st.columns(2)

        mobile = c1.text_input(
            "Mobile",
            value=detail.get("mobile") or "",
        )

        email = c2.text_input(
            "Email",
            value=detail.get("email") or "",
        )

        city = c1.text_input(
            "City",
            value=detail.get("city") or "",
        )

        address = c2.text_input(
            "Address",
            value=detail.get("address") or "",
        )

        save = st.form_submit_button(
            "Save profile fields",
            type="primary",
        )

    if save:
        resp = api.request(
            "PATCH",
            f"/employees/{employee_id}",
            json={
                "mobile": mobile,
                "email": email,
                "city": city,
                "address": address,
            },
        )

        try:
            api.raise_for_api(resp)
            st.success("Updated")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))
def _kv_table(pairs: list[tuple[str, object]]) -> None:
    rows = [
        {"Field": label, "Value": value if value not in (None, "") else "—"}
        for label, value in pairs
    ]
    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Field": st.column_config.TextColumn("Field", width="small"),
            "Value": st.column_config.TextColumn("Value"),
        },
    )


def _render_employee_profile(detail: dict) -> None:
    st.markdown(f"#### {detail.get('full_name') or detail.get('employee_id')}")
    st.caption(
        " · ".join(
            part
            for part in (
                detail.get("job_title"),
                detail.get("department_name"),
                str(detail.get("employment_status") or "").title() or None,
            )
            if part
        )
    )

    with st.expander("Profile details", expanded=True):
        st.caption("Contact")
        _kv_table(
            [
                ("Email", detail.get("email")),
                ("Mobile", detail.get("mobile")),
                ("City", detail.get("city")),
                ("Address", detail.get("address")),
            ]
        )
        st.caption("Employment")
        _kv_table(
            [
                ("Employee ID", detail.get("employee_id")),
                ("Hire date", _friendly_when(detail.get("hire_date")) or detail.get("hire_date")),
                ("Manager", detail.get("manager_id")),
                ("Nationality", detail.get("nationality")),
                (
                    "HR approver",
                    "Yes" if detail.get("is_hr_approver") else "No",
                ),
            ]
        )
        if detail.get("bank_name") or detail.get("iban") or detail.get("basic_salary"):
            st.caption("Compensation & banking")
            _kv_table(
                [
                    ("Basic salary", detail.get("basic_salary")),
                    ("Housing allowance", detail.get("housing_allowance")),
                    ("Bank", detail.get("bank_name")),
                    ("Bank code", detail.get("bank_code")),
                    ("IBAN", detail.get("iban")),
                ]
            )

def page_team_insights() -> None:
    """Manager/Admin view for synthetic Experience Gap insights."""

    role = (st.session_state.get("me") or {}).get("role")

    if role not in {"hr_manager", "admin"}:
        st.error("Team Insights is available to HR managers and admins only.")
        return

    styles.hero(
        "HR manager",
        "Team Insights",
        "See which experience titles are missing or under-covered "
        "across your teams.",
    )

    dept_response = api.request("GET", "/experience-gap/departments")

    try:
        dept_data = api.raise_for_api(dept_response)
    except RuntimeError as exc:
        st.error(str(exc))
        return

    departments = (dept_data or {}).get("departments") or []

    if not departments:
        st.info("No departments found.")
        return

    name_to_id = {
        d.get("department_name"): d.get("department_id")
        for d in departments
    }
    names = list(name_to_id.keys())
    default_index = next(
        (i for i, n in enumerate(names) if name_to_id[n] == "DEP-06"),
        0,
    )

    department_name = st.selectbox(
        "Department",
        names,
        index=default_index,
    )
    department_id = name_to_id[department_name]

    st.caption(
        "Coverage is derived from job title, not an individual "
        "employee assessment. Synthetic placeholder data for this demo."
    )

    response = api.request(
        "GET",
        f"/experience-gap/{department_id}",
    )

    try:
        data = api.raise_for_api(response)
    except RuntimeError as exc:
        st.error(str(exc))
        return

    if not isinstance(data, dict):
        st.error("Unexpected Experience Gap response.")
        return

    titles = data.get("skills") or []

    if not titles:
        st.info("No experience title requirements found for this department.")
        return

    missing = [
        item for item in titles
        if str(item.get("status") or "").upper() == "MISSING"
    ]

    low = [
        item for item in titles
        if str(item.get("status") or "").upper() == "LOW"
    ]

    ok = [
        item for item in titles
        if str(item.get("status") or "").upper() == "OK"
    ]

    c1, c2, c3 = st.columns(3)

    c1.metric("Missing", len(missing))
    c2.metric("Low coverage", len(low))
    c3.metric("Covered", len(ok))

    if missing:
        st.subheader("🔴 Missing experience titles")

        st.dataframe(
            [
                {
                    "Experience Title": item.get("skill_name"),
                    "Required Employees": item.get("required_headcount"),
                    "Current Employees": item.get("current_headcount"),
                    "Critical": (
                        "Yes"
                        if item.get("is_critical")
                        else "No"
                    ),
                }
                for item in missing
            ],
            use_container_width=True,
            hide_index=True,
        )

        for item in missing:
            if item.get("recommendation"):
                st.caption(
                    f"**{item.get('skill_name')}:** "
                    f"{item.get('recommendation')}"
                )

    if low:
        st.subheader("🟡 Low coverage")

        st.dataframe(
            [
                {
                    "Experience Title": item.get("skill_name"),
                    "Required Employees": item.get("required_headcount"),
                    "Current Employees": item.get("current_headcount"),
                    "Gap": (
                        int(item.get("required_headcount") or 0)
                        - int(item.get("current_headcount") or 0)
                    ),
                    "Critical": (
                        "Yes"
                        if item.get("is_critical")
                        else "No"
                    ),
                }
                for item in low
            ],
            use_container_width=True,
            hide_index=True,
        )

        for item in low:
            if item.get("recommendation"):
                st.caption(
                    f"**{item.get('skill_name')}:** "
                    f"{item.get('recommendation')}"
                )

    if ok:
        with st.expander(f"Covered experience titles ({len(ok)})"):
            st.caption(
                "Already covered — shown as current department "
                "structure, no action needed."
            )
            st.dataframe(
                [
                    {
                        "Experience Title": item.get("skill_name"),
                        "Current Employees": item.get("current_headcount"),
                    }
                    for item in ok
                ],
                use_container_width=True,
                hide_index=True,
            )

def page_approvals() -> None:
    styles.hero(
        "HR manager",
        "Waiting on you",
        "These are the requests that need a person — not the system — to decide. "
        "Read the person first, then the risk.",
    )
    try:
        rows = api.raise_for_api(api.request("GET", "/approvals")) or []
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not isinstance(rows, list):
        rows = []

    waiting = [r for r in rows if (r.get("status") or "").lower() == "pending"]
    approved = [r for r in rows if (r.get("status") or "").lower() == "approved"]
    sent_back = [r for r in rows if (r.get("status") or "").lower() == "rejected"]
    st.markdown(
        styles.queue_stats(len(waiting), len(approved), len(sent_back)),
        unsafe_allow_html=True,
    )

    filter_label = st.radio(
        "Show",
        ["Waiting", "Approved", "Sent back", "Everything"],
        horizontal=True,
    )
    visible = {
        "Waiting": waiting,
        "Approved": approved,
        "Sent back": sent_back,
        "Everything": rows,
    }[filter_label]

    if not visible:
        empty_copy = {
            "Waiting": (
                "You're all caught up",
                "Nothing is waiting for a decision right now. Enjoy the quiet.",
            ),
            "Approved": (
                "No approvals in this list yet",
                "When you say yes, those decisions will live here.",
            ),
            "Sent back": (
                "You haven't sent anything back",
                "If a request isn't ready, it will show up here with your note.",
            ),
            "Everything": (
                "The queue is empty",
                "When people submit something that needs a manager, it will land here.",
            ),
        }[filter_label]
        title, body = empty_copy
        st.markdown(
            f'<div class="empty-catchup"><h3>{title}</h3><p>{body}</p></div>',
            unsafe_allow_html=True,
        )
        return

    names = _employee_names([r.get("employee_id") for r in visible])
    proposals = _proposal_map()
    st.caption("Open a case to read it, then approve or send it back with a short note.")
    for index, row in enumerate(visible):
        approval_id = str(row.get("approval_id") or "")
        employee_id = str(row.get("employee_id") or "")
        person = names.get(employee_id) or employee_id or "Someone on the team"
        status = (row.get("status") or "pending").lower()
        label = f"{person} · {_action_label(row)}"
        if status != "pending":
            label = f"{_status_label(status)} · {label}"
        with st.expander(label, expanded=status == "pending" and index < 2):
            st.markdown(_approval_card_html(row, person), unsafe_allow_html=True)

            # Explain this pending approval
            if status == "pending":
                if st.button(
                    "Explain this",
                    key=f"explain_{approval_id}",
                    use_container_width=True,
                ):
                    try:
                        brief_response = api.request(
                            "GET",
                            f"/approvals/{approval_id}/brief",
                            timeout=120.0,
                        )
                        brief = api.raise_for_api(brief_response)

                        if not isinstance(brief, dict):
                            st.error("Unexpected Decision Brief response.")
                        else:
                            st.session_state[
                                f"approval_brief_{approval_id}"
                            ] = brief

                    except RuntimeError as exc:
                        st.error(str(exc))

            # Show Decision Brief if it was requested
            brief = st.session_state.get(
                f"approval_brief_{approval_id}"
            )

            if brief:
                decision_brief = brief.get("brief") or {}

                st.markdown("### Decision Brief")

                st.caption(
                    f"Action: {decision_brief.get('action_type') or '—'}"
                )

                risk = decision_brief.get("risk_level")
                if risk:
                    st.caption(f"Risk level: {risk}")

                # Historical precedent
                precedent = decision_brief.get(
                    "historical_precedent"
                )

                if precedent:
                    st.markdown("**Historical precedent**")
                    _kv_table(
                        [
                            (
                                "Approved",
                                precedent.get("approved_count", 0),
                            ),
                            (
                                "Denied",
                                precedent.get("denied_count", 0),
                            ),
                            (
                                "Total",
                                precedent.get("total_count", 0),
                            ),
                        ]
                    )

                # Policy explanation
                policy = decision_brief.get("policy") or {}

                recommendation = str(
                    policy.get("recommendation") or ""
                ).strip()

                if recommendation:
                    st.markdown("**Policy**")
                    st.write(recommendation)

                sources = policy.get("sources") or []

                if sources:
                    st.markdown("**Policy sources**")

                    for source in sources:
                        if isinstance(source, dict):
                            sid = source.get("id") or "source"
                            text = source.get("text") or ""

                            st.caption(str(sid))

                            if text:
                                st.write(text)
                                    
                    

                # Manager explanation
                manager = decision_brief.get("manager") or {}

                manager_response = str(
                    manager.get("response") or ""
                ).strip()

                if manager_response:
                    st.markdown("**Manager assessment**")
                    st.write(manager_response)

                reasons = manager.get("reasons") or []

                if reasons:
                    st.markdown("**Notes**")
                    for reason in reasons:
                        st.write(f"- {reason}")

            proposal = proposals.get(row.get("proposal_id"))
            _render_proposal_details(proposal)

            cover_options = _cover_candidate_options(proposal)
            with st.form(f"decide_{approval_id}"):
                cover_employee_id = None
                if cover_options:
                    ids, labels, default_index = cover_options
                    cover_employee_id = st.selectbox(
                        "Cover employee",
                        ids,
                        index=default_index,
                        format_func=lambda eid: labels.get(eid, eid),
                    )
                note = st.text_area(
                    "Note",
                    placeholder="A sentence of context helps — especially if you send this back.",
                    label_visibility="collapsed",
                )
                st.caption("A note is optional for approve. Please add one if you send it back.")
                col_a, col_b = st.columns(2)
                approve = col_a.form_submit_button("Approve", type="primary", use_container_width=True)
                reject = col_b.form_submit_button("Send back", use_container_width=True)
            if approve:
                _decide(approval_id, "approve", note, person, cover_employee_id)
            elif reject:
                _decide(approval_id, "reject", note, person, cover_employee_id)


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
            st.success(f"Approved for {who}. They can move forward.")
        else:
            st.success(f"Sent back to {who}. Your note is on the record.")
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
    return "Needs a decision"


def _status_label(status: str) -> str:
    return {"pending": "Waiting", "approved": "Approved", "rejected": "Sent back"}.get(
        status, status.title()
    )


def _risk_label(level: str | None) -> tuple[str, str]:
    key = (level or "").strip().lower()
    if key == "high":
        return "High impact", "high"
    if key == "medium":
        return "Needs review", "medium"
    if key == "low":
        return "Low risk", "low"
    return (level or "Risk unknown"), "low"


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
    status = _status_label(str(row.get("status") or "pending"))
    decided = _friendly_when(row.get("decided_at"))
    meta_bits = [f"Employee {employee_id}" if employee_id else ""]
    if when:
        meta_bits.append(f"Raised {when}")
    if decided:
        meta_bits.append(f"Decided {decided}")
    meta = " · ".join(bit for bit in meta_bits if bit)
    return (
        '<div class="approval-card">'
        '<div class="approval-card-top">'
        f'<span class="risk-pill risk-pill--{risk_class}">{html.escape(risk_text)}</span>'
        f'<span class="status-pill">{html.escape(status)}</span>'
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

    st.caption("Request details")
    if action_type == "leave_request":
        _render_leave_proposal_details(payload)
    else:
        _render_generic_proposal_details(payload)

    related = item.get("related_request_id")
    if related:
        st.caption(f"Linked leave request: {related}")


def _render_leave_proposal_details(payload: dict) -> None:
    rows = [
        ("Leave type", str(payload.get("leave_type") or "").title()),
        ("Start date", payload.get("start_date")),
        ("End date", payload.get("end_date")),
        ("Days", payload.get("days")),
        ("Reason", payload.get("reason")),
        ("Suggested cover", payload.get("suggested_cover_employee_name")),
    ]
    if payload.get("assigned_cover_employee_name"):
        rows.append(("Assigned cover", payload.get("assigned_cover_employee_name")))
    _kv_table(rows)
    candidates = payload.get("cover_candidates")
    if isinstance(candidates, list):
        suggested_id = payload.get("suggested_cover_employee_id")
        others = [
            c
            for c in candidates
            if isinstance(c, dict) and c.get("employee_id") != suggested_id
        ]
        if others:
            with st.popover(f"Other cover options ({len(others)})"):
                st.dataframe(
                    [
                        {"Name": c.get("full_name"), "Role": c.get("job_title")}
                        for c in others
                    ],
                    use_container_width=True,
                    hide_index=True,
                )


def _render_generic_proposal_details(payload: dict) -> None:
    pairs = [
        (str(key).replace("_", " ").title(), value)
        for key, value in payload.items()
        if value not in (None, "")
    ]
    if pairs:
        _kv_table(pairs)


def page_users() -> None:
    styles.hero(
        "Admin",
        "Users & roles",
        "Manage account roles and active status. Password hashes are never shown.",
    )
    try:
        users = api.raise_for_api(api.request("GET", "/users"))
        roles = api.raise_for_api(api.request("GET", "/users/roles"))
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.dataframe(users or [], use_container_width=True, hide_index=True)
    st.caption("Roles: " + ", ".join(r["role_name"] for r in (roles or [])))
    c1, c2, c3 = st.columns(3)
    user_id = c1.text_input("User ID")
    role = c2.selectbox("Role", ["employee", "hr_specialist", "hr_manager", "admin"])
    active = c3.checkbox("Active", value=True)
    if st.button("Update user", type="primary") and user_id:
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
        "Admin",
        "Audit log",
        "Every login, profile update, leave submit, and approval decision lands here.",
    )
    try:
        rows = api.raise_for_api(api.request("GET", "/audit"))
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.dataframe(rows or [], use_container_width=True, hide_index=True)
