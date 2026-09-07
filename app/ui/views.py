from __future__ import annotations

import streamlit as st

from app.ui import api_client as api
from app.ui import styles


def page_chat() -> None:
    styles.hero(
        "Ask Yusor",
        "What can I help with?",
        "Send a free-text HR request. The orchestrator is still a stub, so a 501 "
        "response is expected until agent logic is written by hand.",
    )
    query = st.text_area("Your request", height=150, placeholder="e.g. How many annual leave days do I have remaining?")
    if st.button("Submit request", type="primary") and query.strip():
        response = api.request("POST", "/agent/query", json={"query": query.strip()})
        if response.status_code == 501:
            st.info(response.json().get("detail", "Manual implementation pending"))
            return
        try:
            st.json(api.raise_for_api(response))
        except RuntimeError as exc:
            st.error(str(exc))


def page_leave() -> None:
    styles.hero(
        "Self-service",
        "My leave",
        "Your current balance and request history, plus a form to submit a new request.",
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
    with st.expander("Full balance snapshot"):
        st.json(balance)

    st.subheader("My requests")
    reqs = api.raise_for_api(api.request("GET", "/leave/requests"))
    st.dataframe(reqs or [], use_container_width=True, hide_index=True)

    st.subheader("Submit a leave request")
    with st.form("leave_form"):
        c1, c2, c3 = st.columns(3)
        leave_type = c1.selectbox("Type", ["annual", "sick", "emergency"])
        start = c2.date_input("Start")
        end = c3.date_input("End")
        days = st.number_input("Days", min_value=0.5, step=0.5, value=1.0)
        reason = st.text_input("Reason")
        submitted = st.form_submit_button("Submit request", type="primary")
    if submitted:
        payload = {
            "leave_type": leave_type,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": days,
            "reason": reason or None,
        }
        created = api.request("POST", "/leave/requests", json=payload)
        try:
            st.success(f"Submitted {api.raise_for_api(created)['request_id']}")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))


def page_employees() -> None:
    styles.hero(
        "HR specialist",
        "Employee records",
        "Search the directory, open a profile, and update low-risk contact fields.",
    )
    q = st.text_input("Search by name, id, or email", placeholder="EMP0001 or Sara")
    listing = api.request("GET", "/employees", params={"q": q} if q else {})
    try:
        rows = api.raise_for_api(listing)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.dataframe(rows or [], use_container_width=True, hide_index=True)

    employee_id = st.text_input("Employee ID to view / update")
    if not employee_id:
        return
    detail_resp = api.request("GET", f"/employees/{employee_id}")
    try:
        detail = api.raise_for_api(detail_resp)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.json(detail)
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


def page_approvals() -> None:
    styles.hero(
        "HR manager",
        "Pending approvals",
        "Review medium- and high-risk actions. Approve or reject with a note.",
    )
    status_filter = st.selectbox("Status", ["pending", "approved", "rejected", "all"])
    params = {} if status_filter == "all" else {"status": status_filter}
    resp = api.request("GET", "/approvals", params=params)
    try:
        rows = api.raise_for_api(resp)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.dataframe(rows or [], use_container_width=True, hide_index=True)
    approval_id = st.text_input("Approval ID")
    note = st.text_input("Decision note")
    col_a, col_b = st.columns(2)
    if col_a.button("Approve", type="primary", use_container_width=True) and approval_id:
        _decide(approval_id, "approve", note)
    if col_b.button("Reject", use_container_width=True) and approval_id:
        _decide(approval_id, "reject", note)


def _decide(approval_id: str, decision: str, note: str) -> None:
    resp = api.request(
        "POST",
        f"/approvals/{approval_id}/decide",
        json={"decision": decision, "decision_note": note or None},
    )
    try:
        api.raise_for_api(resp)
        st.success(f"{decision.title()}d {approval_id}")
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))


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
