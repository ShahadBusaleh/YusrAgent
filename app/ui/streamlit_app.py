from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st

from app.ui import api_client as api
from app.ui import styles, views

st.set_page_config(page_title="Yusor", page_icon="◆", layout="wide")
styles.inject()

PAGES_BY_ROLE = {
    "employee": ["Ask Yusor", "My leave"],
    "hr_specialist": ["Ask Yusor", "My leave", "Employees"],
    "hr_manager": ["Approvals", "Employees", "Ask Yusor", "My leave"],
    "admin": ["Ask Yusor", "My leave", "Employees", "Approvals", "Users", "Audit log"],
}

NAV_LABELS = {
    "hr_manager": {
        "Approvals": "Waiting on you",
        "Employees": "People",
        "Ask Yusor": "Ask Yusor",
        "My leave": "My leave",
    }
}

ROLE_LABELS = {
    "employee": "Employee",
    "hr_specialist": "HR specialist",
    "hr_manager": "HR manager",
    "admin": "Admin",
}


def _login() -> None:
    st.markdown(
        """
        <div class="yusor-hero" style="margin-bottom: 1.8rem;">
          <div class="login-mark">YUSOR</div>
          <div class="login-tag">HR, grounded in policy.</div>
          <p class="login-copy">
            Ask leave questions, look up records, and send high-risk actions
            for human approval — in a role-aware workspace.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _left, mid, _right = st.columns([0.7, 1.4, 0.7])
    with mid:
        st.markdown('<div class="yusor-kicker">Welcome back</div>', unsafe_allow_html=True)
        st.subheader("Sign in")
        st.caption("Seeded local accounts use password `ChangeMe123!`.")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if not submitted:
        return
    try:
        tokens = api.raise_for_api(
            api.request(
                "POST",
                "/auth/login",
                json={"username": username.strip(), "password": password},
            )
        )
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.session_state.access_token = tokens["access_token"]
    st.session_state.refresh_token = tokens["refresh_token"]
    me = api.raise_for_api(api.request("GET", "/auth/me"))
    st.session_state.me = me
    st.rerun()


def _logout() -> None:
    try:
        api.request(
            "POST",
            "/auth/logout",
            json={"refresh_token": st.session_state.get("refresh_token")},
        )
    except Exception:
        pass
    for key in ("access_token", "refresh_token", "me"):
        st.session_state.pop(key, None)
    st.rerun()


def _shell() -> None:
    me = st.session_state.me
    role = me["role"]
    pages = PAGES_BY_ROLE.get(role, PAGES_BY_ROLE["employee"])
    labels = NAV_LABELS.get(role, {})
    display_name = me.get("full_name") or me["username"]
    first_name = str(display_name).split()[0]
    with st.sidebar:
        st.markdown('<div class="yusor-brand">YUSOR</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="yusor-brand-sub">{html.escape(str(display_name))}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<span class="yusor-role">{html.escape(ROLE_LABELS.get(role, role))}</span>',
            unsafe_allow_html=True,
        )
        if role == "hr_manager":
            st.markdown(
                f'<p class="sidebar-hello">Hi {html.escape(first_name)}. '
                "Start with the people waiting on a decision.</p>",
                unsafe_allow_html=True,
            )
        st.write("")
        page = st.radio(
            "Navigate",
            pages,
            format_func=lambda key: labels.get(key, key),
            label_visibility="collapsed",
        )
        st.write("")
        if st.button("Sign out", use_container_width=True):
            _logout()
            return

    if page == "Ask Yusor":
        views.page_chat()
    elif page == "My leave":
        views.page_leave()
    elif page == "Employees":
        views.page_employees()
    elif page == "Approvals":
        views.page_approvals()
    elif page == "Users":
        views.page_users()
    elif page == "Audit log":
        views.page_audit()


if "access_token" not in st.session_state:
    _login()
else:
    _shell()
