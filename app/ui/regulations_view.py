"""Regulation update agent UI (Phase 4): the review panel in "Waiting on
you", the read-only View in Records, and the Regulations page.

Citations are structured data on the request (payload.citations and each
policy's cited_law_ids) and are shown here — never as tags in text.
"""

from __future__ import annotations

import difflib
import html
import re
from datetime import datetime

import httpx
import streamlit as st

from app.ui import api_client as api
from app.ui import i18n
from app.ui import styles
from app.ui import views

_PSTATUS_STYLE = {
    "compliant": ("✓", "#E3EEDC", "#35592A"),
    "conflict": ("!", "#F6DCD6", "#8A2E1F"),
    "needs_review": ("?", "#F6E7CF", "#7A4E12"),
}
_VSTATUS_STYLE = {
    "current": ("✓", "#E3EEDC", "#35592A"),
    "pending": ("◔", "#EDE6DA", "#5E5241"),
    "superseded": ("↺", "#ECE7E1", "#5E5241"),
    "repealed": ("✕", "#F6DCD6", "#8A2E1F"),
    "rejected": ("✕", "#F6DCD6", "#8A2E1F"),
    "sent_back": ("↩", "#F6E7CF", "#7A4E12"),
}


def _pill(label: str, style: tuple[str, str, str]) -> str:
    icon, bg, color = style
    return (
        f'<span class="dt-pill" style="background:{bg};color:{color};">'
        f"{html.escape(icon)} {html.escape(label)}</span>"
    )


def policy_pill(status: str) -> str:
    return _pill(i18n.t(f"reg.pstatus.{status}"), _PSTATUS_STYLE.get(status, _PSTATUS_STYLE["needs_review"]))


def version_pill(status: str) -> str:
    return _pill(i18n.t(f"reg.status.{status}"), _VSTATUS_STYLE.get(status, _VSTATUS_STYLE["pending"]))


def simulated_badge() -> str:
    return '<span class="rg-sim-badge">' + html.escape(i18n.t("reg.mode_simulated")) + "</span>"


def kind_label(kind: str | None) -> str:
    return i18n.t(f"reg.change.{kind or 'changed'}")


def policy_reason(policy: dict) -> str:
    """The analyst's one-line reason in the UI language. reason_ar exists
    only on requests analysed after it was added; older ones keep English."""
    return str((i18n.is_rtl() and policy.get("reason_ar")) or policy.get("reason") or "")


def summary_line(payload: dict) -> str:
    policies = payload.get("policies") or []
    return i18n.t(
        "records.summary_regulation",
        n=payload.get("article_no"),
        kind=kind_label(payload.get("change_kind")),
        conflicts=sum(1 for p in policies if p.get("status") == "conflict"),
        review=sum(1 for p in policies if p.get("status") == "needs_review"),
    ) + (f" · {i18n.t('reg.mode_simulated')}" if payload.get("simulated") else "")


def request_label(payload: dict) -> str:
    prefix = f"[{i18n.t('reg.mode_simulated')}] " if payload.get("simulated") else ""
    return prefix + i18n.t("reg.review_title", n=payload.get("article_no"), kind=kind_label(payload.get("change_kind")))


# ---------------------------------------------------------------------------
# Diff of the official (Arabic) text
# ---------------------------------------------------------------------------

def _side_by_side(old: str, new: str) -> tuple[str, str]:
    """(old with deletions marked, new with insertions marked), word level."""
    a, b = re.findall(r"\S+|\s+", old or ""), re.findall(r"\S+|\s+", new or "")
    left, right = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        old_part, new_part = html.escape("".join(a[i1:i2])), html.escape("".join(b[j1:j2]))
        if op == "equal":
            left.append(old_part)
            right.append(new_part)
            continue
        if old_part:
            left.append(f'<span class="rg-del">{old_part}</span>')
        if new_part:
            right.append(f'<span class="rg-ins">{new_part}</span>')
    return "".join(left) or "—", "".join(right) or "—"


def render_official_diff(payload: dict) -> None:
    old_html, new_html = _side_by_side(payload.get("old_text") or "", payload.get("new_text") or "")
    st.markdown(f"**{html.escape(i18n.t('reg.diff'))}**")
    left, right = st.columns(2)
    left.markdown(
        f'<div class="rg-label">{html.escape(i18n.t("reg.official_old"))}</div>'
        f'<div class="rg-ar" dir="rtl" lang="ar">{old_html}</div>',
        unsafe_allow_html=True,
    )
    right.markdown(
        f'<div class="rg-label">{html.escape(i18n.t("reg.official_new"))}</div>'
        f'<div class="rg-ar" dir="rtl" lang="ar">{new_html}</div>',
        unsafe_allow_html=True,
    )


def _header(payload: dict) -> None:
    if payload.get("simulated"):
        st.warning(i18n.t("reg.simulated_banner"))
        if payload.get("source_label"):
            # The fixture label is English: keep it left-to-right in the Arabic UI.
            st.markdown(
                f'<div class="rg-label" dir="ltr" style="text-align:left;font-weight:400">{html.escape(payload["source_label"])}</div>',
                unsafe_allow_html=True,
            )
    summary = (payload.get("summary_ar") if i18n.is_rtl() else payload.get("summary_en")) or payload.get("summary_en")
    impact = payload.get("impact_level") or "medium"
    source = payload.get("source_url") or ""
    link = (
        f'<a href="{html.escape(source)}" target="_blank" rel="noopener">{html.escape(i18n.t("reg.open_source"))}</a>'
        if source.startswith("https://") else html.escape(source)
    )
    styles.detail_card(
        [
            (i18n.t("reg.summary"), summary),
            (i18n.t("reg.source"), styles.raw(link)),
            (i18n.t("reg.col_fetched"), views._friendly_when(payload.get("fetched_at"))),
            (i18n.t("reg.impact"), i18n.t(f"reg.impact.{impact}")),
        ]
    )
    if (payload.get("analysis") or {}).get("fallback"):
        st.info(i18n.t("reg.analysis_fallback"))
    if payload.get("previous_proposal_id"):
        st.caption(i18n.t("reg.previous_request", id=payload["previous_proposal_id"]))


def _citations(payload: dict) -> None:
    items = payload.get("citations") or []
    if not items:
        return
    st.markdown(f"**{html.escape(i18n.t('reg.citations'))}**")
    lines = []
    for c in items:
        label = " · ".join(str(x) for x in (c.get("law_id") or i18n.t("reg.new_row"), f"Art. {c.get('article')}", c.get("title")) if x)
        url = c.get("source_url") or ""
        link = f' — <a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(i18n.t("reg.open_source"))}</a>' if url.startswith("https://") else ""
        lines.append(f"<li>{html.escape(label)}{link}</li>")
    st.markdown(f'<ul class="rg-cites">{"".join(lines)}</ul>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Waiting on you: review panel
# ---------------------------------------------------------------------------

def _edit_key(proposal_id: str, target: str, item_id: str) -> str:
    return f"rg_edit_{proposal_id}_{target}_{item_id}"


def render_review(row: dict, proposal: dict) -> None:
    payload = proposal.get("payload_json") if isinstance(proposal.get("payload_json"), dict) else {}
    proposal_id = str(proposal.get("proposal_id") or "")
    approval_id = str(row.get("approval_id") or "")
    me = st.session_state.get("me") or {}
    i_edited = me.get("employee_id") in {e.get("by") for e in payload.get("edits") or []}

    with st.container(key=f"inbox_review_{approval_id}"):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            badge = f" {simulated_badge()}" if payload.get("simulated") else ""
            st.markdown(
                f"#### {html.escape(i18n.t('reg.review_title', n=payload.get('article_no'), kind=kind_label(payload.get('change_kind'))))}{badge}",
                unsafe_allow_html=True,
            )
            st.caption(f"{payload.get('article_label') or ''} · {i18n.t('reg.system')} · {proposal_id}")
        with top_r:
            if st.button(i18n.t("inbox.close"), key=f"close_{approval_id}", use_container_width=True):
                st.session_state.pop("inbox_open", None)
                st.rerun()

        _header(payload)
        render_official_diff(payload)

        # Law rows: official Arabic next to the English proposed text.
        law_rows = payload.get("law_rows") or []
        changed = [r for r in law_rows if r.get("changed")]
        st.markdown(f"**{html.escape(i18n.t('reg.law_rows'))}**")
        for item in changed:
            item_id = item.get("law_id") or "NEW"
            proposed = item.get("proposed") or {}
            title = f"{item_id} · {proposed.get('title') or ''}" if item.get("law_id") else f"{i18n.t('reg.new_row')} · {proposed.get('title') or ''}"
            with st.container(key=f"rg_lawrow_{proposal_id}_{item_id}"):
                st.markdown(f"**{html.escape(title)}**")
                ar_col, en_col = st.columns(2)
                with ar_col:
                    st.markdown(
                        f'<div class="rg-label">{html.escape(i18n.t("reg.official_old"))}</div>'
                        f'<div class="rg-ar rg-small" dir="rtl" lang="ar">{html.escape(payload.get("old_text") or "—")}</div>'
                        f'<div class="rg-label">{html.escape(i18n.t("reg.official_new"))}</div>'
                        f'<div class="rg-ar rg-small" dir="rtl" lang="ar">{html.escape(payload.get("new_text") or "—")}</div>',
                        unsafe_allow_html=True,
                    )
                with en_col:
                    if item.get("old"):
                        st.markdown(
                            f'<div class="rg-label">{html.escape(i18n.t("reg.law_row_old"))}</div>'
                            f'<div class="rg-en" dir="ltr" lang="en">{html.escape((item.get("old") or {}).get("rule") or "—")}</div>',
                            unsafe_allow_html=True,
                        )
                    st.text_area(
                        i18n.t("reg.law_row_new"), value=proposed.get("rule") or "",
                        key=_edit_key(proposal_id, "law_row", item_id), height=110,
                    )
        unchanged = len(law_rows) - len(changed)
        if unchanged:
            st.caption(i18n.t("reg.unchanged_rows", n=unchanged))

        policies = payload.get("policies") or []
        st.markdown(f"**{html.escape(i18n.t('reg.policies'))}**")
        if not policies:
            st.caption(i18n.t("reg.no_policies"))
        for policy in policies:
            pid = policy.get("policy_id")
            with st.container(key=f"rg_policy_{proposal_id}_{pid}"):
                st.markdown(
                    f"**{html.escape(str(policy.get('policy_name') or ''))}** ({html.escape(str(pid))}) {policy_pill(policy.get('status'))}",
                    unsafe_allow_html=True,
                )
                reason = policy_reason(policy)
                if reason:
                    st.caption(f"{i18n.t('reg.reason')}: {reason}")
                cur, new = st.columns(2)
                cur.markdown(
                    f'<div class="rg-label">{html.escape(i18n.t("reg.policy_old"))}</div>'
                    f'<div class="rg-en" dir="ltr" lang="en">{html.escape(policy.get("old_rule") or "—")}</div>',
                    unsafe_allow_html=True,
                )
                new.text_area(
                    i18n.t("reg.policy_new"), value=policy.get("proposed_rule") or "",
                    key=_edit_key(proposal_id, "policy", pid), height=110,
                )
                if policy.get("cited_law_ids"):
                    st.caption(i18n.t("reg.cited", ids=", ".join(policy["cited_law_ids"])))

        _citations(payload)
        for edit in payload.get("edits") or []:
            st.caption(i18n.t("reg.edited_by", who=edit.get("by"), date=views._friendly_when(edit.get("at"))))

        edits = []
        for item in changed:
            item_id = item.get("law_id") or "NEW"
            value = (st.session_state.get(_edit_key(proposal_id, "law_row", item_id)) or "").strip()
            if value and value != ((item.get("proposed") or {}).get("rule") or "").strip():
                edits.append({"target": "law_row", "id": item_id, "field": "rule", "text": value})
        for policy in policies:
            value = (st.session_state.get(_edit_key(proposal_id, "policy", policy.get("policy_id"))) or "").strip()
            if value and value != (policy.get("proposed_rule") or "").strip():
                edits.append({"target": "policy", "id": policy.get("policy_id"), "field": "rule", "text": value})

        if i_edited or edits:
            st.warning(i18n.t("reg.you_edited") if i_edited else i18n.t("reg.unsaved_edits"))
        note = st.text_input(i18n.t("reg.note"), key=f"rg_note_{proposal_id}")
        b1, b2, b3, b4 = st.columns(4)
        with b1, st.container(key=f"yz_btn_secondary_rg_save_{proposal_id}"):
            if st.button(i18n.t("reg.save_edits"), key=f"rg_save_{proposal_id}", disabled=not edits, use_container_width=True):
                try:
                    for edit in edits:
                        api.raise_for_api(api.request("PUT", f"/regulations/requests/{proposal_id}/proposed-text", json=edit))
                    st.session_state["reg_flash"] = ("success", i18n.t("reg.edits_saved"))
                except RuntimeError as exc:
                    st.session_state["reg_flash"] = ("error", str(exc))
                st.rerun()
        decision = None
        with b2:
            if st.button(i18n.t("reg.approve"), key=f"rg_approve_{proposal_id}", type="primary",
                         disabled=i_edited or bool(edits), use_container_width=True):
                decision = "approve"
        with b3, st.container(key=f"yz_btn_secondary_rg_back_{proposal_id}"):
            if st.button(i18n.t("reg.send_back"), key=f"rg_sendback_{proposal_id}", use_container_width=True):
                decision = "send_back"
        with b4, st.container(key=f"yz_btn_secondary_rg_reject_{proposal_id}"):
            if st.button(i18n.t("reg.reject"), key=f"rg_reject_{proposal_id}", use_container_width=True):
                decision = "reject"
        if decision:
            _decide(proposal_id, decision, note)


def _decide(proposal_id: str, decision: str, note: str) -> None:
    try:
        with st.spinner(i18n.t("reg.checking") if decision == "approve" else ""):
            result = api.raise_for_api(
                api.request(
                    "POST", f"/regulations/requests/{proposal_id}/decide",
                    json={"decision": decision, "decision_note": note or None}, timeout=300.0,
                )
            ) or {}
    except (RuntimeError, httpx.HTTPError) as exc:
        st.session_state["reg_flash"] = ("error", str(exc))
        st.rerun()
        return
    if decision == "approve":
        reindex = result.get("reindex") or {}
        record = api.raise_for_api(api.request("GET", f"/regulations/requests/{proposal_id}")) or {}
        final = (record.get("payload") or {}).get("final") or {}
        n = len(final.get("applied_law_rows") or []) + len(final.get("applied_policies") or [])
        status = i18n.t(f"reg.reindex.{reindex.get('status') or 'not_needed'}")
        st.session_state["reg_flash"] = ("success" if reindex.get("status") != "failed" else "warning",
                                         i18n.t("reg.approved", n=n, status=status))
    elif decision == "send_back":
        st.session_state["reg_flash"] = ("success", i18n.t("reg.sent_back_done"))
    else:
        st.session_state["reg_flash"] = ("success", i18n.t("reg.rejected_done"))
    st.session_state.pop("inbox_open", None)
    st.rerun()


def pending_requests() -> list[dict]:
    """Pending regulation_update approvals (GET /regulations/pending); empty
    for roles that can't decide them."""
    try:
        rows = api.raise_for_api(api.request("GET", "/regulations/pending")) or []
    except RuntimeError:
        return []
    return rows if isinstance(rows, list) else []


def show_flash() -> None:
    flash = st.session_state.pop("reg_flash", None)
    if flash:
        {"success": st.success, "warning": st.warning}.get(flash[0], st.error)(flash[1])


# ---------------------------------------------------------------------------
# Records: read-only View
# ---------------------------------------------------------------------------

def render_record_details(record: dict) -> None:
    payload = record.get("payload") or {}
    if payload.get("simulated"):
        st.markdown(simulated_badge(), unsafe_allow_html=True)
    _header(payload)
    render_official_diff(payload)

    final = payload.get("final") or {}
    if final.get("outcome") == "approved":
        st.markdown(f"**{html.escape(i18n.t('reg.applied'))}**")
        applied = [
            (f"{a['law_id']} · {(a.get('new') or {}).get('title') or ''}", (a.get("old") or {}).get("rule"), (a.get("new") or {}).get("rule"))
            for a in final.get("applied_law_rows") or []
        ] + [
            (a["policy_id"], a.get("old_rule"), a.get("new_rule")) for a in final.get("applied_policies") or []
        ]
        if not applied:
            st.caption(i18n.t("reg.applied_none"))
        for title, old, new in applied:
            st.markdown(f"**{html.escape(title)}**")
            left, right = st.columns(2)
            left.markdown(f'<div class="rg-label">{html.escape(i18n.t("reg.law_row_old"))}</div><div class="rg-en" dir="ltr">{html.escape(old or "—")}</div>', unsafe_allow_html=True)
            right.markdown(f'<div class="rg-label">{html.escape(i18n.t("reg.law_row_new"))}</div><div class="rg-en" dir="ltr">{html.escape(new or "—")}</div>', unsafe_allow_html=True)
        reindex = final.get("reindex") or {}
        state = reindex.get("status") or "not_needed"
        st.caption(f"{i18n.t('reg.reindex')}: {i18n.t(f'reg.reindex.{state}')}"
                   + (f" · {views._friendly_when(reindex.get('at'))}" if reindex.get("at") else ""))
        if state == "failed" and (st.session_state.get("me") or {}).get("role") in {"hr_manager", "admin"}:
            if st.button(i18n.t("reg.retry_reindex"), key=f"rg_retry_{record.get('proposal_id')}"):
                try:
                    api.raise_for_api(api.request("POST", "/regulations/reindex", timeout=300.0))
                except RuntimeError as exc:
                    st.error(str(exc))
                st.rerun()

    policies = payload.get("policies") or []
    if policies:
        st.markdown(f"**{html.escape(i18n.t('reg.policies'))}**")
        styles.data_table(
            [
                {"policy": f"{p.get('policy_name')} ({p.get('policy_id')})", "status": styles.raw(policy_pill(p.get("status"))),
                 "reason": policy_reason(p) or "—", "cited": ", ".join(p.get("cited_law_ids") or []) or "—"}
                for p in policies
            ],
            [("policy", i18n.t("reg.policies")), ("status", i18n.t("reg.col_status")),
             ("reason", i18n.t("reg.reason")), ("cited", i18n.t("reg.citations"))],
            raw_html_keys={"status"},
            key=f"rg_rec_policies_{record.get('proposal_id')}",
        )
    _citations(payload)


# ---------------------------------------------------------------------------
# Regulations page
# ---------------------------------------------------------------------------

def _result_text(result: dict) -> str:
    if result.get("status") == "refused":
        return i18n.t("reg.result_refused", errors=" ".join(result.get("errors") or []))
    parts = [i18n.t(
        "reg.result",
        sources=sum(1 for s in result.get("sources") or [] if s.get("status") == "ok"),
        changes=result.get("changes_found", 0),
        created=len(result.get("requests_created") or []),
    )]
    if result.get("baseline_stored"):
        parts.append(i18n.t("reg.result_baseline", n=result["baseline_stored"]))
    if result.get("alerts_new"):
        parts.append(i18n.t("reg.alerts_new", n=result["alerts_new"]))
    return " · ".join(parts)


def _when(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        local = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return views._friendly_when(raw)
    return f"{views._friendly_when(local.isoformat())} {local:%H:%M}"


def page() -> None:
    st.markdown(
        f"""
        <div class="yz-chat-header">
          <div class="yz-chat-title">{html.escape(i18n.t("reg.title"))}</div>
          <div class="yz-chat-subtitle">{html.escape(i18n.t("reg.subtitle"))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if (st.session_state.get("me") or {}).get("role") not in {"hr_manager", "admin"}:
        st.error(i18n.t("reg.not_allowed"))
        return
    try:
        status = api.raise_for_api(api.request("GET", "/regulations/status")) or {}
    except RuntimeError as exc:
        st.error(str(exc))
        return

    simulated = bool(status.get("simulated"))
    with st.container(key="records_filters"):
        top_l, top_r = st.columns([3, 1])
        with top_l:
            mode = simulated_badge() if simulated else html.escape(i18n.t("reg.mode_live"))
            st.markdown(mode, unsafe_allow_html=True)
            last = status.get("last_check")
            st.caption(i18n.t("reg.last_check", when=_when(last.get("at")), result=_result_text(last)) if last else i18n.t("reg.never_checked"))
        with top_r:
            clicked = st.button(i18n.t("reg.check_now"), key="rg_check_now", type="primary", use_container_width=True)
        if simulated:
            st.warning(i18n.t("reg.simulated_banner"))
            for problem in status.get("demo_problems") or []:
                st.error(i18n.t("reg.demo_refused", problems=problem))
        if clicked:
            try:
                with st.spinner(i18n.t("reg.checking")):
                    st.session_state["rg_last_result"] = api.raise_for_api(
                        api.request("POST", "/regulations/check", timeout=600.0)
                    )
            except (RuntimeError, httpx.HTTPError) as exc:
                st.session_state["rg_last_result"] = {"status": "refused", "errors": [str(exc)]}
            st.rerun()
        result = st.session_state.get("rg_last_result")
        if result:
            (st.error if result.get("status") == "refused" else st.success)(_result_text(result))
            if result.get("requests_created"):
                st.caption(", ".join(result["requests_created"]))
            if result.get("skipped"):
                st.caption(i18n.t("reg.result_skipped", items=", ".join(f"{s['ref_id']} ({s['reason']})" for s in result["skipped"])))
            if result.get("errors") and result.get("status") != "refused":
                st.caption(i18n.t("reg.result_errors", errors=" · ".join(result["errors"])))

    try:
        data = api.raise_for_api(api.request("GET", "/regulations/articles", params={"source": status.get("source")})) or {}
    except RuntimeError as exc:
        st.error(str(exc))
        return
    items = data.get("items") or []
    rows = [
        {
            "id": a["ref_id"],
            "article": a["ref_id"].split(":", 1)[1],
            "title": a.get("label") or "—",
            "version": a.get("version_no"),
            "fetched": views._friendly_when(a.get("fetched_at")),
            "status": styles.raw(
                version_pill(a.get("status"))
                + (f" {version_pill('pending')}" if a.get("pending_proposal_id") else "")
            ),
        }
        for a in items
    ]

    def _open(row: dict) -> None:
        st.session_state["rg_open"] = row["id"]
        st.session_state["rg_scroll"] = "#yz-rg-history"

    st.markdown('<div id="yz-rg-list"></div>', unsafe_allow_html=True)
    with st.container(key="records_card"):
        styles.data_table(
            rows,
            [("article", i18n.t("reg.col_article")), ("title", i18n.t("reg.col_title")),
             ("version", i18n.t("reg.col_version")), ("fetched", i18n.t("reg.col_fetched")),
             ("status", i18n.t("reg.col_status"))],
            raw_html_keys={"status"},
            on_view=_open,
            view_label=i18n.t("reg.view_history"),
            key="regulations",
            empty_message=i18n.t("reg.no_articles"),
        )
        opened = st.session_state.get("rg_open")
        selected = next((i for i, r in enumerate(rows) if r["id"] == opened), None)
        if selected is not None:
            st.markdown(styles.selected_row_css("regulations", selected), unsafe_allow_html=True)

    if opened:
        st.divider()
        with st.container(key="records_detail"):
            st.markdown('<div id="yz-rg-history"></div>', unsafe_allow_html=True)
            head, close = st.columns([5, 1])
            head.markdown(f"**{html.escape(i18n.t('reg.history_for', n=opened.split(':', 1)[1]))}**")
            with close:
                if st.button(i18n.t("inbox.close"), key="rg_close", use_container_width=True):
                    st.session_state.pop("rg_open", None)
                    st.session_state["rg_scroll"] = "#yz-rg-list"
                    st.rerun()
            try:
                history = api.raise_for_api(api.request(
                    "GET", "/regulations/history", params={"kind": "article", "ref_id": opened, "source": status.get("source")}
                )) or {}
            except RuntimeError as exc:
                st.error(str(exc))
                history = {}
            for version in history.get("items") or []:
                meta = [f"v{version['version_no']}", views._friendly_when(version.get("fetched_at"))]
                if version.get("proposal_id"):
                    meta.append(version["proposal_id"])
                if version.get("decided_by"):
                    meta.append(f"{version['decided_by']} · {views._friendly_when(version.get('decided_at'))}")
                st.markdown(f"{version_pill(version.get('status'))} {html.escape(' · '.join(m for m in meta if m))}", unsafe_allow_html=True)
                st.markdown(f'<div class="rg-ar rg-small" dir="rtl" lang="ar">{html.escape(version.get("text") or "—")}</div>', unsafe_allow_html=True)

    alerts = []
    if not simulated:
        try:
            alerts = (api.raise_for_api(api.request("GET", "/regulations/alerts")) or {}).get("items") or []
        except RuntimeError:
            alerts = []
        st.markdown(f"**{html.escape(i18n.t('reg.alerts'))}**")
        if not alerts:
            st.caption(i18n.t("reg.no_alerts"))
        for a in alerts:
            st.markdown(
                f'<div class="rg-alert">{html.escape(views._friendly_when(a.get("fetched_at")))} · '
                f'<a href="{html.escape(a.get("source_url") or "")}" target="_blank" rel="noopener">{html.escape(a.get("label") or "")}</a></div>',
                unsafe_allow_html=True,
            )

    target = st.session_state.pop("rg_scroll", None)
    if target:
        views._scroll_to(target)
