from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import streamlit as st

from app.ui import api_client as api
from app.ui import i18n
from app.ui import views
from app.ui import styles


st.set_page_config(
    page_title="Yusor",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

styles.inject()


# Restored against real git history — `main` (== commit 354eb95, "Payroll
# feature", the last commit before UI-owner work started) for Payroll and
# Growth Opportunities, which were live nav items silently dropped
# somewhere in this redesign. "Employees" was removed by team decision
# (it was already absent from every role in `main` itself — dropped one
# commit earlier still, by "Remove employees page" (20ffe15) / "Comment
# out 'Employees' in HR roles" (69fccd5)) — not restored here.
# Every role ends with My Requests, then Grievances and Records (where the
# role has them), just above Sign out; Regulations sits right before My
# Requests for hr_manager and admin.
PAGES_BY_ROLE = {
    "employee": ["Dashboard", "Ask Yusor", "Growth Opportunities", "My Requests"],
    # hr_specialist gets "Approvals" (the "Waiting on you" inbox) too, not
    # just the old placeholder — confirmed against the real grievances
    # router: hr_specialist CAN decide grievances (require_role includes
    # it), it just has zero /approvals access (that role check is
    # hr_manager/admin only). page_inbox() itself hides the Leave
    # Requests/Approvals tabs and never calls /approvals for this role —
    # this nav entry is what makes the Grievances-only inbox reachable at
    # all, since deciding a grievance only happens from that page now.
    "hr_specialist": [
        "Dashboard",
        "Ask Yusor",
        "Approvals",
        "Onboarding & Offboarding",
        "My Requests",
        "Grievances",
        "Records",
    ],
    "hr_manager": [
        "Dashboard",
        "Ask Yusor",
        "Approvals",
        "Onboarding & Offboarding",
        "Team Insights",
        "Payroll",
        "Regulations",
        "My Requests",
        "Grievances",
        "Records",
    ],
    "admin": [
        "Dashboard",
        "Ask Yusor",
        "Approvals",
        "Onboarding & Offboarding",
        "Team Insights",
        "Payroll",
        "Users",
        "Audit log",
        "Regulations",
        "My Requests",
        "Grievances",
        "Records",
    ],
}

# Material Symbols shorthand (native Streamlit — ":material/<name>:" is
# rendered by Streamlit's own widget-label parser since 1.31; no library).
NAV_ICONS = {
    "Dashboard": "dashboard",
    "Ask Yusor": "chat",
    "Approvals": "pending_actions",
    "Records": "folder_open",
    "Regulations": "gavel",
    "Onboarding & Offboarding": "badge",
    "Team Insights": "insights",
    "Payroll": "payments",
    "Grievances": "feedback",
    "Growth Opportunities": "trending_up",
    "My Requests": "assignment",
    "Users": "group",
    "Audit log": "history",
}


def _nav_labels() -> dict[str, str]:
    return {
        "Dashboard": i18n.t("nav.dashboard"),
        "Ask Yusor": i18n.t("nav.ask_yusor"),
        "Approvals": i18n.t("nav.approvals"),
        "Records": i18n.t("nav.records"),
        "Regulations": i18n.t("nav.regulations"),
        "Onboarding & Offboarding": i18n.t("nav.staffing"),
        "Team Insights": i18n.t("nav.team_insights"),
        "Grievances": i18n.t("nav.grievances"),
        "My Requests": i18n.t("nav.my_requests"),
        "Payroll": i18n.t("nav.payroll"),
        "Growth Opportunities": i18n.t("nav.growth"),
        "Users": i18n.t("nav.users"),
        "Audit log": i18n.t("nav.audit_log"),
    }


def _role_labels() -> dict[str, str]:
    return {
        "employee": i18n.t("role.employee"),
        "hr_specialist": i18n.t("role.hr_specialist"),
        "hr_manager": i18n.t("role.hr_manager"),
        "admin": i18n.t("role.admin"),
    }


def _is_rtl(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in (text or ""))


def _short_day_month(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%d %b")
    except ValueError:
        return ""


@st.cache_data(ttl=20, show_spinner=False)
def _pending_decisions_count(token: str) -> int:
    """Count of items waiting on a manager/HR decision — same data the
    Approvals ("Waiting on you") page shows, just counted. Short TTL so the
    sidebar badge and welcome-bar line stay close to live without adding a
    request to every single page render; keyed on the access token so one
    signed-in user's count is never served to another."""
    try:
        approvals = api.raise_for_api(api.request("GET", "/approvals")) or []
    except RuntimeError:
        approvals = []
    if not isinstance(approvals, list):
        approvals = []
    count = sum(1 for r in approvals if str(r.get("status") or "").lower() == "pending")

    try:
        grievances = api.raise_for_api(api.request("GET", "/grievances")) or []
    except RuntimeError:
        grievances = []
    if not isinstance(grievances, list):
        grievances = []
    count += sum(
        1 for g in grievances if str(g.get("status") or "").upper() == "PENDING_HR_REVIEW"
    )
    count += _pending_regulation_count()
    return count


@st.cache_data(ttl=20, show_spinner=False)
def _latest_request_update(token: str, employee_id: str) -> dict | None:
    """Most recently decided (non-pending) leave request for this employee,
    used for the employee-facing welcome-bar line."""
    try:
        reqs = api.raise_for_api(api.request("GET", "/leave/requests")) or []
    except RuntimeError:
        return None
    if not isinstance(reqs, list):
        return None
    decided = [
        r for r in reqs
        if r.get("decided_at") and str(r.get("status") or "").lower() != "pending"
    ]
    if not decided:
        return None
    decided.sort(key=lambda r: str(r.get("decided_at") or ""), reverse=True)
    return decided[0]


def _request_update_line(latest: dict) -> str:
    leave_type_key = {
        "annual": "leave.type_annual",
        "sick": "leave.type_sick",
        "emergency": "leave.type_emergency",
    }.get(str(latest.get("leave_type") or "").lower())
    leave_type = i18n.t(leave_type_key) if leave_type_key else str(latest.get("leave_type") or "").title()
    start = _short_day_month(latest.get("start_date"))
    end = _short_day_month(latest.get("end_date"))
    dates = f"{start}–{end}" if start and end else (start or end or "")
    status_key = {
        "approved": "status.approved",
        "rejected": "status.sent_back",
        "sent_back": "status.sent_back",
    }.get(str(latest.get("status") or "").lower())
    status_label = i18n.t(status_key) if status_key else str(latest.get("status") or "").title()
    return i18n.t("welcome.request_update", type=leave_type, dates=dates, status=status_label)


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "Y"
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _login() -> None:
    # Login-only CSS on top of styles.inject() (background photo, hidden
    # header, glass-styled form, fixed brand mark).
    styles.login_page_style()

    # The form itself IS the card — styled via [data-testid="stForm"] in
    # login_page_style() — so there's no extra wrapper div/container.
    with st.form("login_v2"):
        st.markdown(
            """
            <div class="yz-login-kicker">YUSOR</div>
            <div class="yz-login-title">Welcome Back</div>
            <div class="yz-login-sub">Sign in to your Yusor workspace</div>
            """,
            unsafe_allow_html=True,
        )
        username = st.text_input("Username", placeholder="e.g. mona.saleh")
        password = st.text_input(
            "Password", type="password", placeholder="Enter your password"
        )
        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
            use_container_width=True,
        )

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
        st.session_state.access_token = tokens["access_token"]
        st.session_state.refresh_token = tokens["refresh_token"]
        st.session_state.me = api.raise_for_api(api.request("GET", "/auth/me"))
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))
    except httpx.HTTPError:
        st.error("Could not reach the Yusor API. Please try again in a moment.")


def _logout() -> None:
    try:
        api.request(
            "POST",
            "/auth/logout",
            json={"refresh_token": st.session_state.get("refresh_token")},
        )
    except Exception:
        pass

    for key in ("access_token", "refresh_token", "me", "nav_page"):
        st.session_state.pop(key, None)
    st.rerun()


def _sidebar(me: dict, pending_count: int = 0) -> str:
    role = me.get("role", "employee")
    pages = PAGES_BY_ROLE.get(role, PAGES_BY_ROLE["employee"])
    display_name = me.get("full_name") or me.get("username") or "User"

    with st.sidebar:
        # 1. Brand — app/ui/assets/yusor_mark_light.png, 150px, with a
        # separately-coded "Y U S O R" wordmark line beneath it.
        st.markdown(styles.brand_logo("light", size=150), unsafe_allow_html=True)
        st.markdown(
            f'<div class="yz-sidebar-tagline">{i18n.t("app.tagline")}</div>',
            unsafe_allow_html=True,
        )

        # 2. Signed-in user, at the top
        st.markdown(
            f"""
            <div class="yz-profile">
              <div class="yz-avatar">{html.escape(_initials(str(display_name)))}</div>
              <div>
                <div class="yz-profile-name">{html.escape(str(display_name))}</div>
                <div class="yz-profile-role">{html.escape(_role_labels().get(role, role))}</div>
              </div>
            </div>
            <div class="yz-sidebar-divider"></div>
            """,
            unsafe_allow_html=True,
        )

        # 3. Navigation, scoped to the signed-in role.
        #
        # "nav_page" is OUR OWN tracked value (read before, written after),
        # deliberately not the radio's own widget key. Binding key="nav_page"
        # directly broke on a language toggle: st.radio's frontend identity
        # depends in part on the *rendered option labels*, which change
        # between English/Arabic (format_func) — Streamlit then treats it as
        # a fresh-mounted widget and snaps to index 0, discarding whatever
        # page was actually selected, even though session_state["nav_page"]
        # still held the correct value right up until the call (confirmed
        # by instrumenting both sides of the st.radio() call). Passing an
        # explicit `index=` computed from our own tracked value fixes this:
        # a "remount" then honors that index instead of defaulting to 0.
        current = st.session_state.get("nav_page")
        if current not in pages:
            current = "Ask Yusor" if "Ask Yusor" in pages else pages[0]
        default_index = pages.index(current) if current in pages else 0

        nav_labels = _nav_labels()

        def _nav_label(p: str) -> str:
            icon = NAV_ICONS.get(p, "circle")
            label = f":material/{icon}: {nav_labels.get(p, p)}"
            if p == "Approvals" and pending_count:
                return f"{label}  {styles.circled_number(pending_count)}"
            return label

        page = st.radio(
            "Navigate",
            pages,
            index=default_index,
            format_func=_nav_label,
            key="nav_radio",
            label_visibility="collapsed",
        )
        st.session_state["nav_page"] = page

        # 4. Footer — sign out, pinned to the bottom. Refresh moved to the
        # per-page welcome bar (styles.page_header()).
        with st.container(key="yz_sidebar_footer"):
            if st.button(i18n.t("sidebar.sign_out"), use_container_width=True):
                _logout()

    return page


def _team_insights_v2() -> None:
    role = (st.session_state.get("me") or {}).get("role")
    if role not in {"hr_manager", "admin"}:
        st.error("Team Insights is available to HR managers and admins only.")
        return

    st.markdown(
        f"""
        <div class="v2-topline">
          <div>
            <div class="v2-page-title">{html.escape(i18n.t("insights.title"))}</div>
            <div class="v2-page-subtitle">{html.escape(i18n.t("insights.subtitle"))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        dept_data = api.raise_for_api(api.request("GET", "/experience-gap/departments"))
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
        if d.get("department_name") and d.get("department_id")
    }
    names = list(name_to_id)
    default_index = next((i for i, n in enumerate(names) if name_to_id[n] == "DEP-06"), 0)

    department_name = st.selectbox(i18n.t("insights.department"), names, index=default_index)
    department_id = name_to_id[department_name]

    st.caption(i18n.t("insights.coverage_note"))

    try:
        data = api.raise_for_api(api.request("GET", f"/experience-gap/{department_id}"))
    except RuntimeError as exc:
        st.error(str(exc))
        return

    # Real fields only, per the live GET /experience-gap/{id} response
    # (confirmed against app/db/skills.py::get_department_experience_gap):
    # skill_name, category, is_critical, current_headcount, status,
    # recommendation, hire_recommended. There is NO required_headcount
    # field anywhere in the backend — the previous version of this page
    # read item.get("required_headcount") and silently got 0 every time,
    # producing exactly the "Required 0 / Current 0 / Gap 0" contradiction
    # for genuine MISSING skills. The backend's own docstring is explicit:
    # "Status is derived purely from real employee data (no assumed
    # per-skill headcount target)". Only two statuses exist — MISSING and
    # OK — there is no "LOW" either. Buckets below use is_critical (a real
    # field) to split MISSING into critical vs. other, instead of a
    # fabricated required/gap number.
    titles = (data or {}).get("skills") or []
    missing_critical = [
        x for x in titles
        if str(x.get("status") or "").upper() == "MISSING" and x.get("is_critical")
    ]
    missing_other = [
        x for x in titles
        if str(x.get("status") or "").upper() == "MISSING" and not x.get("is_critical")
    ]
    covered = [x for x in titles if str(x.get("status") or "").upper() == "OK"]

    st.markdown(
        f"""
        <div class="v2-summary-grid">
          <div class="v2-summary-card v2-summary-card--critical">
            <div class="v2-summary-number">{len(missing_critical)}</div>
            <div class="v2-summary-label">{html.escape(i18n.t("insights.critical_gaps"))}</div>
            <div class="v2-summary-help">{html.escape(i18n.t("insights.critical_help"))}</div>
          </div>
          <div class="v2-summary-card v2-summary-card--attention">
            <div class="v2-summary-number">{len(missing_other)}</div>
            <div class="v2-summary-label">{html.escape(i18n.t("insights.other_gaps"))}</div>
            <div class="v2-summary-help">{html.escape(i18n.t("insights.other_gaps_help"))}</div>
          </div>
          <div class="v2-summary-card v2-summary-card--covered">
            <div class="v2-summary-number">{len(covered)}</div>
            <div class="v2-summary-label">{html.escape(i18n.t("insights.covered"))}</div>
            <div class="v2-summary-help">{html.escape(i18n.t("insights.covered_help"))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if missing_critical or missing_other:
        st.markdown(
            f'<div class="v2-section-title">{html.escape(i18n.t("insights.priority_gaps"))}</div>',
            unsafe_allow_html=True,
        )

    for item in [*missing_critical, *missing_other]:
        skill = str(item.get("skill_name") or "Skill")
        category = str(item.get("category") or "—")
        current = int(item.get("current_headcount") or 0)
        critical = bool(item.get("is_critical"))
        pill_class = "critical" if critical else "attention"
        pill_text = i18n.t("insights.critical_gap_pill") if critical else i18n.t("insights.gap_pill")
        recommendation = str(item.get("recommendation") or "").strip()
        note = recommendation or i18n.t("insights.no_recommendation")

        st.markdown(
            f"""
            <div class="v2-gap-card">
              <div class="v2-gap-top">
                <div class="v2-gap-name">{html.escape(skill)}</div>
                <span class="v2-pill v2-pill--{pill_class}">{pill_text}</span>
              </div>
              <div class="v2-gap-note">{html.escape(note)}</div>
              <div class="v2-gap-stats">
                <div><div class="v2-mini-label">{html.escape(i18n.t("insights.col_category"))}</div><div class="v2-mini-value">{html.escape(category)}</div></div>
                <div><div class="v2-mini-label">{html.escape(i18n.t("insights.col_headcount"))}</div><div class="v2-mini-value">{current}</div></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        left, right, spacer = st.columns([1.15, 1.65, 5])
        with left:
            with st.popover(i18n.t("insights.view_details")):
                st.write(i18n.t("insights.popover_skill", value=skill))
                st.write(f"**{i18n.t('insights.col_category')}:** {category}")
                st.write(i18n.t("insights.popover_current", value=current))
                if recommendation:
                    st.write(recommendation)
        with right:
            with st.container(key=f"yz_btn_secondary_gap_{department_id}_{skill}"):
                if st.button(
                    i18n.t("insights.ask_about_gap_secondary"),
                    key=f"ask_gap_{department_id}_{skill}",
                    use_container_width=True,
                ):
                    st.session_state["chat_query"] = (
                        f"Explain the {skill} skill gap in the {department_name} department. "
                        f"Current mapped coverage is {current}. "
                        f"Explain what this means using available HR data and policy."
                    )
                    st.session_state["_yz_pending_nav"] = "Ask Yusor"
                    st.rerun()

    if covered:
        with st.expander(i18n.t("insights.covered_skills", n=len(covered))):
            styles.data_table(
                [
                    {
                        "skill": x.get("skill_name") or "—",
                        "category": x.get("category") or "—",
                        "current": x.get("current_headcount"),
                    }
                    for x in covered
                ],
                [
                    ("skill", i18n.t("insights.col_skill")),
                    ("category", i18n.t("insights.col_category")),
                    ("current", i18n.t("insights.col_headcount")),
                ],
                key="covered_skills",
            )


@st.cache_data(ttl=60, show_spinner=False)
def _team_readiness_data(token: str) -> dict | None:
    """Aggregate real experience-gap coverage across EVERY department for
    the manager Dashboard's readiness ring — unlike Team Insights, which
    only ever shows one selected department at a time. Returns None (the
    caller then shows nothing, per the redesign spec) if the department
    list is empty or every department's skills fetch failed, rather than
    rendering a ring off zeroed/fabricated numbers. 60s cache since this
    is N+1 real API calls (1 department list + 1 per department)."""
    try:
        dept_data = api.raise_for_api(api.request("GET", "/experience-gap/departments"))
    except RuntimeError:
        return None
    departments = (dept_data or {}).get("departments") or []
    if not departments:
        return None

    critical = other = covered = 0
    any_success = False
    for d in departments:
        dep_id = d.get("department_id")
        if not dep_id:
            continue
        try:
            data = api.raise_for_api(api.request("GET", f"/experience-gap/{dep_id}"))
        except RuntimeError:
            continue
        skills = (data or {}).get("skills") or []
        if not skills:
            continue
        any_success = True
        for s in skills:
            status = str(s.get("status") or "").upper()
            if status == "OK":
                covered += 1
            elif status == "MISSING" and s.get("is_critical"):
                critical += 1
            elif status == "MISSING":
                other += 1

    if not any_success or (critical + other + covered) == 0:
        return None
    return {"critical": critical, "other": other, "covered": covered}


@st.cache_data(ttl=20, show_spinner=False)
def _latest_own_request(token: str, employee_id: str) -> dict | None:
    """Most recent leave request of any status (pending/approved/rejected)
    for the signed-in employee — the Dashboard's "your latest request"
    card. Distinct from _latest_request_update(), which only looks at
    *decided* requests for the welcome-bar nudge."""
    try:
        reqs = api.raise_for_api(api.request("GET", "/leave/requests")) or []
    except RuntimeError:
        return None
    if not isinstance(reqs, list) or not reqs:
        return None
    reqs = sorted(reqs, key=lambda r: str(r.get("submitted_at") or ""), reverse=True)
    return reqs[0]


@st.cache_data(ttl=20, show_spinner=False)
def _open_grievances_count(token: str) -> int:
    try:
        grievances = api.raise_for_api(api.request("GET", "/grievances")) or []
    except RuntimeError:
        return 0
    if not isinstance(grievances, list):
        return 0
    return sum(1 for g in grievances if str(g.get("status") or "").upper() == "PENDING_HR_REVIEW")


@st.cache_data(ttl=20, show_spinner=False)
def _pending_approvals_only_count(token: str) -> int:
    """Pending /approvals only — separate from _pending_decisions_count()
    (which combines approvals + grievances for the sidebar badge/welcome
    line), since the Dashboard shows "Pending items" and "Open grievances"
    as two distinct real numbers."""
    try:
        approvals = api.raise_for_api(api.request("GET", "/approvals")) or []
    except RuntimeError:
        return 0
    if not isinstance(approvals, list):
        return 0
    return sum(1 for r in approvals if str(r.get("status") or "").lower() == "pending") + _pending_regulation_count()


def _pending_regulation_count() -> int:
    """System-requested regulation updates, which /approvals doesn't list."""
    try:
        rows = api.raise_for_api(api.request("GET", "/regulations/pending")) or []
    except RuntimeError:
        return 0
    return len(rows) if isinstance(rows, list) else 0


@st.cache_data(ttl=30, show_spinner=False)
def _active_users_count(token: str) -> int:
    try:
        users = api.raise_for_api(api.request("GET", "/users")) or []
    except RuntimeError:
        return 0
    if not isinstance(users, list):
        return 0
    return sum(1 for u in users if u.get("is_active"))


@st.cache_data(ttl=20, show_spinner=False)
def _recent_audit_events(token: str, limit: int = 5) -> list[dict]:
    try:
        entries = api.raise_for_api(api.request("GET", "/audit")) or []
    except RuntimeError:
        return []
    if not isinstance(entries, list):
        return []
    entries = sorted(entries, key=lambda e: str(e.get("timestamp") or ""), reverse=True)
    return entries[:limit]


def _page_dashboard(me: dict) -> None:
    role = me.get("role", "employee")
    token = st.session_state.get("access_token", "")

    # Approved termination, still employed: Active + a future last working day.
    until = views.notice_period_until(me.get("employee_id"))
    if until:
        st.info(i18n.t("notice.until", date=views._friendly_when(until)))

    # ---- Role-scoped real content, per the redesign spec ----
    if role == "employee":
        latest = _latest_own_request(token, str(me.get("employee_id") or ""))
        if latest:
            leave_type_key = {
                "annual": "leave.type_annual",
                "sick": "leave.type_sick",
                "emergency": "leave.type_emergency",
            }.get(str(latest.get("leave_type") or "").lower())
            leave_type = (
                i18n.t(leave_type_key) if leave_type_key else str(latest.get("leave_type") or "").title()
            )
            start = _short_day_month(latest.get("start_date"))
            end = _short_day_month(latest.get("end_date"))
            dates = f"{start}–{end}" if start and end else (start or end or "")
            request_line = f"{leave_type} · {dates}" if dates else leave_type

            st.markdown(
                f'<div class="v2-section-title">{html.escape(i18n.t("dashboard.latest_request_title"))}</div>',
                unsafe_allow_html=True,
            )
            styles.detail_card(
                [
                    (i18n.t("dashboard.latest_request_label"), request_line),
                    (
                        i18n.t("detail.status"),
                        styles.raw(
                            styles.status_pill_html(
                                str(latest.get("status") or "pending").lower(), styles.status_pills()
                            )
                        ),
                    ),
                ]
            )

    elif role == "hr_specialist":
        st.markdown(
            styles.stat_tiles_html([(i18n.t("dashboard.open_grievances"), _open_grievances_count(token))]),
            unsafe_allow_html=True,
        )

    elif role in {"hr_manager", "admin"}:
        tiles = [
            (i18n.t("dashboard.pending_items"), _pending_approvals_only_count(token)),
            (i18n.t("dashboard.open_grievances"), _open_grievances_count(token)),
        ]
        if role == "admin":
            tiles.append((i18n.t("dashboard.active_users"), _active_users_count(token)))
        st.markdown(styles.stat_tiles_html(tiles), unsafe_allow_html=True)

        readiness = _team_readiness_data(token)
        if readiness:
            st.markdown(
                f'<div class="v2-section-title">{html.escape(i18n.t("dashboard.readiness_title"))}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                styles.readiness_ring_html(
                    readiness["critical"], readiness["other"], readiness["covered"]
                ),
                unsafe_allow_html=True,
            )
            with st.container(key="yz_btn_secondary_view_insights"):
                if st.button(i18n.t("dashboard.view_team_insights")):
                    st.session_state["_yz_pending_nav"] = "Team Insights"
                    st.rerun()

        if role == "admin":
            recent = _recent_audit_events(token, limit=5)
            st.markdown(
                f'<div class="v2-section-title">{html.escape(i18n.t("dashboard.recent_audit_title"))}</div>',
                unsafe_allow_html=True,
            )
            styles.data_table(
                [
                    {
                        "timestamp": views._friendly_when(e.get("timestamp")) or e.get("timestamp") or "—",
                        "actor": e.get("actor") or "—",
                        "event_type": str(e.get("event_type") or "").replace("_", " ").title() or "—",
                        "details": e.get("details") or "—",
                    }
                    for e in recent
                ],
                [
                    ("timestamp", i18n.t("audit.col_time")),
                    ("actor", i18n.t("audit.col_user")),
                    ("event_type", i18n.t("audit.col_event")),
                    ("details", i18n.t("audit.col_details")),
                ],
                key="dashboard_audit",
                empty_message=i18n.t("audit.empty"),
            )

    # ---- Ask Yusor card — last, for every role ----
    with st.container(key="yz_dashboard_ask_card"):
        st.markdown(
            '<div class="yz-ask-card">'
            f'{styles.leaf_icon_html("yz-ask-card-leaf")}'
            "<div>"
            f'<div class="yz-ask-card-title">{html.escape(i18n.t("chat.title"))}</div>'
            f'<div class="yz-ask-card-sub">{html.escape(i18n.t("chat.subtitle"))}</div>'
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.container(key="yz_dashboard_open_chat"):
            if st.button(i18n.t("dashboard.open_chat")):
                st.session_state["_yz_pending_nav"] = "Ask Yusor"
                st.rerun()


def _render(page: str) -> None:
    if page == "Dashboard":
        _page_dashboard(st.session_state.me)
    elif page == "Ask Yusor":
        views.page_chat()
    elif page == "My Requests":
        # Current backend has leave requests; keep it functional for this UI preview.
        views.page_leave()
    elif page == "Approvals":
        views.page_inbox()
    elif page == "Records":
        views.page_records()
    elif page == "Regulations":
        views.page_regulations()
    elif page == "Onboarding & Offboarding":
        views.page_staffing()
    elif page == "Team Insights":
        _team_insights_v2()
    elif page == "Payroll":
        views.page_payroll()
    elif page == "Growth Opportunities":
        views.page_growth_opportunities()
    elif page == "Grievances":
        views.page_grievances()
    elif page == "Users":
        views.page_users()
    elif page == "Audit log":
        views.page_audit()


def _shell() -> None:
    # Resolve any pending "jump to page" request (from the welcome bar's
    # action line, or Team Insights' "Ask Yusor about this gap") *before*
    # the sidebar's st.radio(key="nav_page") is instantiated below —
    # Streamlit forbids writing st.session_state["nav_page"] once that
    # widget has already been created in the same run.
    pending_nav = st.session_state.pop("_yz_pending_nav", None)
    if pending_nav:
        st.session_state["nav_page"] = pending_nav

    # RTL + IBM Plex Sans Arabic switch — must run before the sidebar and
    # page content render below, since it flips the whole layout direction.
    styles.apply_language(i18n.get_lang())

    styles.shell_background()

    me = st.session_state.me
    role = me.get("role", "employee")
    token = st.session_state.get("access_token", "")

    pending_count = _pending_decisions_count(token) if role in {"hr_manager", "admin"} else 0

    action_text: str | None = None
    action_page: str | None = None
    if role in {"hr_manager", "admin"}:
        if pending_count:
            noun_key = "welcome.pending_noun_one" if pending_count == 1 else "welcome.pending_noun_many"
            action_text = i18n.t("welcome.pending_line", n=pending_count, noun=i18n.t(noun_key))
            action_page = "Approvals"
    else:
        latest = _latest_request_update(token, str(me.get("employee_id") or ""))
        if latest:
            action_text = _request_update_line(latest)
            action_page = "My Requests"

    page = _sidebar(me, pending_count)

    display_name = str(me.get("full_name") or me.get("username") or "User")
    styles.page_header(
        display_name,
        _role_labels().get(role, role),
        name_rtl=_is_rtl(display_name),
        action_text=action_text,
        action_page=action_page,
    )

    _render(page)


if "access_token" not in st.session_state or "me" not in st.session_state:
    _login()
else:
    _shell()
