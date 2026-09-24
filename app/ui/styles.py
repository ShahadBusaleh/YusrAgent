from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

import streamlit as st

from app.ui import i18n

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_LOGIN_BG_FILE = "login_bg.jpg"
_LEAF_SVG_FILE = "leaf.svg"
_BRAND_MARK_FILES = {"light": "yusor_mark_light.png", "brown": "yusor_mark_brown.png"}


@lru_cache(maxsize=2)
def _brand_mark_data_uri(variant: str) -> str | None:
    filename = _BRAND_MARK_FILES.get(variant)
    if not filename:
        return None
    path = _ASSETS_DIR / filename
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def brand_logo(variant: str, *, size: int = 150, wordmark_class: str = "yz-brand-wordmark") -> str:
    """Renders app/ui/assets/yusor_mark_{variant}.png (the "يُسر" wordmark
    icon) at `size` px, with a separately-coded, letter-spaced "Y U S O R"
    line beneath it — not baked into the image, so it can take the right
    color for wherever it's placed (white in the sidebar, brown on the
    login page) via `wordmark_class`. Returns "" if the asset is missing.
    """
    uri = _brand_mark_data_uri(variant)
    if not uri:
        return ""
    return (
        f'<img src="{uri}" width="{size}" alt="YUSOR" class="yz-brand-mark" />'
        f'<div class="{wordmark_class}">Y U S O R</div>'
    )


_CIRCLED_DIGITS = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def circled_number(n: int) -> str:
    """A small circular badge for a count, using a native Unicode
    enclosed-digit glyph — st.radio's option text goes through
    Streamlit's markdown-lite label renderer, which doesn't support
    arbitrary HTML/CSS (no custom-colored pill achievable there without a
    fragile JS/DOM hack), so this is the honest, robust equivalent: a real
    circle around the real live count, in the same text color as the
    label. Falls back to "(n)" past 20, which the glyph set doesn't cover.
    """
    if 0 <= n < len(_CIRCLED_DIGITS):
        return _CIRCLED_DIGITS[n]
    return f"({n})"


@lru_cache(maxsize=1)
def _leaf_svg() -> str:
    path = _ASSETS_DIR / _LEAF_SVG_FILE
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def leaf_icon_html(css_class: str = "") -> str:
    """The one palm-leaf icon (app/ui/assets/leaf.svg, #462E24 with a
    #847860 accent, 40px) — used in exactly three places per the redesign
    spec: the welcome bar, the Ask Yusor page title, and the Ask Yusor
    card on the Dashboard. Nowhere else."""
    svg = _leaf_svg()
    if not svg:
        return ""
    cls = f' class="{css_class}"' if css_class else ""
    return f"<span{cls}>{svg}</span>"


# Time-of-day greeting icons — small linear (stroke-only) SVGs in #C8923E,
# inlined here rather than as asset files since only leaf.svg was asked
# for as a file; these are presentation-only accents for the greeting
# subtitle line.
_TIME_ICON_SVGS = {
    "sun": (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
        'stroke="#C8923E" stroke-width="1.8" stroke-linecap="round">'
        '<circle cx="12" cy="12" r="4.3"/>'
        '<path d="M12 2.5v2.6M12 18.9v2.6M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8'
        'M2.5 12h2.6M18.9 12h2.6M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/>'
        "</svg>"
    ),
    "sunset": (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
        'stroke="#C8923E" stroke-width="1.8" stroke-linecap="round">'
        '<path d="M3 18h18"/>'
        '<path d="M6 18a6 6 0 0 1 12 0"/>'
        '<path d="M12 7.5v3.3M6.7 10.3l1.7 1.7M17.3 10.3l-1.7 1.7"/>'
        "</svg>"
    ),
    "moon": (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
        'stroke="#C8923E" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"/>'
        "</svg>"
    ),
}

_TIME_GREETING_KEYS = {
    "morning": "greeting.morning",
    "afternoon": "greeting.afternoon",
    "evening": "greeting.evening",
}


def _time_of_day_riyadh() -> str:
    """Morning/afternoon/evening bucket by Asia/Riyadh local time (stdlib
    zoneinfo — no new dependency; confirmed working against the system tz
    database on the real deployment target). Falls back to server-local
    time if the tz database is somehow unavailable, rather than crashing
    the whole shell over a greeting."""
    try:
        from zoneinfo import ZoneInfo
        hour = datetime.now(ZoneInfo("Asia/Riyadh")).hour
    except Exception:
        hour = datetime.now().hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


@lru_cache(maxsize=1)
def _login_bg_data_uri() -> str | None:
    path = _ASSETS_DIR / _LOGIN_BG_FILE
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


# =============================================================
# Design system: colors, type scale, spacing, radii, components.
# Single design file for the whole team — see AGENTS.md/TEAM.md
# Phase 3. Everything below (including the legacy component
# classes hero()/stat_card()/queue_stats()/grievance_queue_stats()
# still call into) is restyled on this one light palette; there is
# no separate dark theme anymore.
# =============================================================

CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Source+Sans+3:wght@400;500;600;700&display=swap');

:root {
  /* Colors */
  --yz-ink: #241A14;
  --yz-sidebar: #2B2019;
  --yz-sidebar-2: #241A14;
  --yz-sidebar-line: rgba(255,255,255,.09);
  /* Lightened from #E9DFD3 — the sidebar background is now a translucent
     rgba(70,46,36,0.62) over the photo (not opaque), and against a lighter
     photo region that only measured 3.43:1. Pure white plus the text-shadow
     on section[data-testid="stSidebar"] below clears 4.5:1 with margin. */
  --yz-sidebar-text: #FFFFFF;
  --yz-sidebar-muted: #B8A996;
  --yz-tan: #C9A78F;
  --yz-tan-strong: #B4886A;
  --yz-bg: #F7F3EE;
  --yz-card: #FFFFFF;
  --yz-line: #E7DFD5;
  /* Darkened from the original #7A6F63 — measured at only 3.84:1 against
     the new background photo (floating captions/subtitles sit directly on
     it, not inside a card), below the 4.5:1 minimum. #4E463D clears ~7:1
     against light photo regions and ~5:1 against darker ones, while still
     reading as "muted" relative to full ink text. */
  --yz-muted: #4E463D;
  --yz-blue: #A8C3DE;
  --yz-blue-soft: #EEF4F9;
  --yz-brown: #6B4A38;
  --yz-brown-strong: #55392A;

  /* Glass panels over the full-bleed background photo (post-login shell) */
  --yz-glass-bg: rgba(255,252,247,0.72);
  --yz-glass-border: rgba(255,255,255,0.6);

  /* Type scale */
  --yz-font-display: "Outfit", sans-serif;
  --yz-font-body: "Source Sans 3", sans-serif;
  --yz-text-xs: .76rem;
  --yz-text-sm: .86rem;
  --yz-text-md: 1rem;
  --yz-text-lg: 1.2rem;
  --yz-text-xl: 1.55rem;
  --yz-text-2xl: 1.9rem;

  /* Spacing */
  --yz-space-xs: .4rem;
  --yz-space-sm: .75rem;
  --yz-space-md: 1.25rem;
  --yz-space-lg: 2rem;
  --yz-space-xl: 3rem;

  /* Radii */
  --yz-radius-sm: 8px;
  --yz-radius-md: 14px;
  --yz-radius-lg: 20px;
}

html, body, [class*="css"] {
  font-family: var(--yz-font-body);
}

.stApp {
  background: var(--yz-bg);
  color: var(--yz-ink);
}

[data-testid="stHeader"],
[data-testid="stToolbar"] {
  background: transparent !important;
}

.block-container {
  padding-top: 1.4rem;
  /* Generous enough to clear the fixed-position st.chat_input bar on Ask
     Yusor (2rem left its last message/suggestions hidden underneath it) —
     harmless extra whitespace on pages without a chat input. */
  padding-bottom: 7rem;
  max-width: 1240px;
}

h1, h2, h3,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
  font-family: var(--yz-font-display) !important;
  color: var(--yz-ink) !important;
  letter-spacing: -0.02em;
}

p, label, .stMarkdown, .stText {
  color: var(--yz-ink);
}

.stCaption, [data-testid="stCaptionContainer"],
.stCaption *, [data-testid="stCaptionContainer"] * {
  color: var(--yz-muted) !important;
}

/* Alert text (error/info/warning) must stay readable on its own tinted box,
   regardless of the page-wide ink color rule above. */
[data-testid="stAlert"],
[data-testid="stAlert"] p,
[data-testid="stAlert"] span {
  color: #241A14 !important;
}

/* Streamlit/BaseWeb nests an inner [data-baseweb="base-input"] div between
   every text input's outer wrapper and the actual <input>, with its own
   opaque dark background — it paints over any background color set on the
   wrapper unless neutralized here. This is what was showing up as a navy/
   dark-blue box behind every text field app-wide (search boxes, profile
   fields, the login form, browser autofill included). */
[data-baseweb="base-input"] {
  background: transparent !important;
}

/* Same inheritance gotcha for buttons: Streamlit wraps a button's label in
   its own <p>, which the plain `p { color: var(--yz-ink) }` rule above
   matches directly — a direct match always beats an inherited value, so
   `!important` on the <button> itself does NOT reach that inner <p>. Every
   button app-wide was rendering ink-dark label text instead of white
   without these. */
.stButton > button p,
.stButton > button span,
.stButton > button div,
.stFormSubmitButton > button p,
.stFormSubmitButton > button span,
.stFormSubmitButton > button div {
  color: white !important;
}

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"] {
  background: rgba(70,46,36,0.62) !important;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-right: 1px solid rgba(0,0,0,.25);
}

section[data-testid="stSidebar"] .block-container {
  padding-top: var(--yz-space-md);
  display: flex;
  flex-direction: column;
  min-height: 96vh;
}

section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] div {
  color: var(--yz-sidebar-text) !important;
  /* Extra real-world legibility margin: the sidebar is a translucent
     rgba(70,46,36,0.62) over a variable photo, not an opaque fill, so a
     light photo region behind it can bring contrast close to the 4.5:1
     floor even with white text — a shadow costs nothing measured by the
     contrast formula but helps actual readability. */
  text-shadow: 0 1px 2px rgba(0,0,0,.35);
}

.yz-sidebar-brand {
  font-family: var(--yz-font-display);
  font-size: var(--yz-text-xl);
  font-weight: 800;
  letter-spacing: 0.1em;
  color: #FFFFFF;
  margin: 0.1rem 0 0.15rem;
}

.yz-brand-mark {
  display: block;
  height: auto;
  margin: .2rem 0 .3rem;
}

.yz-brand-wordmark {
  font-family: var(--yz-font-display);
  font-weight: 800;
  letter-spacing: .4em;
  font-size: .95rem;
  margin: 0 0 .15rem;
}

.yz-sidebar-tagline {
  color: var(--yz-sidebar-muted) !important;
  font-size: var(--yz-text-xs);
  line-height: 1.35;
  margin-bottom: var(--yz-space-md);
}

.yz-sidebar-divider {
  border-top: 1px solid var(--yz-sidebar-line);
  margin: var(--yz-space-sm) 0 var(--yz-space-md);
}

.st-key-yz_sidebar_footer {
  margin-top: auto;
  padding-top: var(--yz-space-md);
}

/* Hide the native circle marker — nav reads as a list of rows, not a
   radio group; the row's own background carries the selected state.
   [data-baseweb="radio"] IS the option's <label> itself (confirmed via
   DOM inspection) — not a wrapper nested inside a further label — so the
   circle is its own first <div> child, not a descendant-of-descendant. */
section[data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {
  display: none;
}

section[data-testid="stSidebar"] [data-testid="stRadio"] label {
  background: transparent;
  border-radius: var(--yz-radius-sm);
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.2rem;
  border: 1px solid transparent;
  transition: all .15s ease;
}

section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
  background: rgba(255,255,255,.06);
}

section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
  background: #462E24;
  border-color: #462E24;
}

section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) p {
  color: #FFFFFF !important;
  font-weight: 700 !important;
}

section[data-testid="stSidebar"] .stButton > button {
  width: 100%;
  justify-content: flex-start;
  background: rgba(255,255,255,.04) !important;
  color: var(--yz-sidebar-text) !important;
  border: 1px solid rgba(255,255,255,.12) !important;
  box-shadow: none !important;
}

section[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(255,255,255,.1) !important;
  border-color: rgba(255,255,255,.22) !important;
}

.yz-profile {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding-bottom: var(--yz-space-sm);
}

.yz-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--yz-tan);
  color: #2B2019;
  font-family: var(--yz-font-display);
  font-weight: 700;
  flex-shrink: 0;
}

.yz-profile-name {
  font-family: var(--yz-font-display);
  font-weight: 700;
  font-size: var(--yz-text-sm);
  color: #FFFFFF !important;
}

.yz-profile-role {
  color: var(--yz-sidebar-muted) !important;
  font-size: var(--yz-text-xs);
}

/* ---------- Buttons & inputs ---------- */
.stButton > button,
.stFormSubmitButton > button {
  background: var(--yz-brown) !important;
  color: white !important;
  border: 1px solid var(--yz-brown) !important;
  border-radius: var(--yz-radius-sm) !important;
  font-family: var(--yz-font-display) !important;
  font-weight: 600 !important;
  box-shadow: 0 4px 12px rgba(107,74,56,.15);
}

.stButton > button:hover,
.stFormSubmitButton > button:hover {
  background: var(--yz-brown-strong) !important;
  border-color: var(--yz-brown-strong) !important;
  transform: translateY(-1px);
}

[data-testid="stForm"] {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: var(--yz-space-md);
  box-shadow: 0 10px 30px rgba(36,26,20,.06);
}

[data-testid="stTextInputRootElement"],
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInputContainer"],
[data-testid="stSelectbox"] > div {
  background: #FFFFFF !important;
  border-color: #D9CFC2 !important;
  border-radius: var(--yz-radius-sm) !important;
  color: var(--yz-ink) !important;
}

[data-testid="stTextArea"] textarea,
[data-testid="stTextInput"] input,
[data-baseweb="select"] * {
  color: var(--yz-ink) !important;
}

/* ---------- Tables ---------- */
[data-testid="stDataFrame"] {
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  overflow: hidden;
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  font-family: var(--yz-font-body);
}

[data-testid="stTable"] table {
  border-collapse: collapse;
  width: 100%;
}

[data-testid="stTable"] thead th {
  background: var(--yz-bg);
  color: var(--yz-ink);
  font-family: var(--yz-font-display);
  font-weight: 700;
  font-size: var(--yz-text-sm);
  border-bottom: 1px solid var(--yz-line);
  text-align: left;
  padding: .55rem .7rem;
}

[data-testid="stTable"] tbody td {
  border-bottom: 1px solid var(--yz-line);
  padding: .55rem .7rem;
  font-size: var(--yz-text-sm);
}

[data-testid="stTable"] tbody tr:hover {
  background: var(--yz-blue-soft);
}

/* ---------- Page header ---------- */
.yz-page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin: .1rem 0 var(--yz-space-md);
}

.yz-page-title {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: var(--yz-text-2xl);
  line-height: 1.08;
  color: var(--yz-ink);
}

.yz-page-subtitle {
  color: var(--yz-muted);
  margin-top: .35rem;
  max-width: 46rem;
}

/* ---------- Placeholder card (pages not yet built) ---------- */
.yz-placeholder {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px dashed var(--yz-glass-border);
  border-radius: 18px;
  padding: 2.4rem 2rem;
  text-align: center;
  color: var(--yz-muted);
}

.yz-placeholder-title {
  font-family: var(--yz-font-display);
  font-weight: 700;
  font-size: var(--yz-text-lg);
  color: var(--yz-ink);
  margin-bottom: .4rem;
}

/* ---------- Legacy components (hero/stat/queue/approval/team-insights) —
   restyled onto the light palette above. Kept under the same class names
   and Python function names so app/ui/views.py and streamlit_app.py don't
   need to change. ---------- */
.yusor-hero {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 1.45rem 1.55rem 1.3rem;
  margin-bottom: 1.25rem;
  border-radius: 18px;
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  box-shadow: 0 8px 24px rgba(36,26,20,.05);
}

.yusor-kicker {
  color: var(--yz-brown);
  text-transform: uppercase;
  letter-spacing: .15em;
  font-size: .68rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
}

.yusor-hero h1 {
  margin: 0 !important;
  font-size: 2.1rem !important;
}

.yusor-hero p {
  margin: 0;
  color: var(--yz-muted);
  max-width: 50rem;
}

.yusor-role {
  display: inline-block;
  background: var(--yz-blue);
  color: var(--yz-ink);
  border-radius: 999px;
  padding: 0.2rem 0.8rem;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.stat-card {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: 1.1rem 1.15rem 1rem;
  min-height: 122px;
  box-shadow: 0 8px 24px rgba(36,26,20,.05);
}

.stat-label {
  color: var(--yz-muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 0.72rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
}

.stat-value {
  font-family: var(--yz-font-display);
  font-size: 2.3rem;
  font-weight: 800;
  color: var(--yz-brown-strong);
  line-height: 1.1;
  margin-top: 0.4rem;
}

.stat-card--olive .stat-value { color: #7A6F4A; }
.stat-card--coffee .stat-value { color: var(--yz-brown); }

.stat-sub {
  color: var(--yz-muted);
  font-size: 0.82rem;
  margin-top: 0.15rem;
}

.queue-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.85rem;
  margin-bottom: 1.2rem;
}

.queue-stat {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: 0.95rem 1rem 0.85rem;
}

.queue-stat-label {
  color: var(--yz-muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 0.68rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
}

.queue-stat-value {
  font-family: var(--yz-font-display);
  font-size: 1.75rem;
  font-weight: 800;
  color: var(--yz-brown-strong);
  margin-top: 0.25rem;
}

.queue-stat--wait .queue-stat-value { color: #A6742F; }
.queue-stat--back .queue-stat-value { color: #B24A3D; }

.approval-card {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: 1rem 1.1rem 0.95rem;
  margin-bottom: 0.35rem;
}

.approval-card-top {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem 0.7rem;
  align-items: center;
  margin-bottom: 0.55rem;
}

.approval-who {
  font-family: var(--yz-font-display);
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--yz-ink);
  margin: 0 0 0.25rem;
}

.approval-summary {
  color: var(--yz-muted);
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.45;
}

.approval-meta {
  color: var(--yz-muted);
  font-size: 0.8rem;
  margin-top: 0.45rem;
}

.risk-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 0.18rem 0.7rem;
  font-size: 0.72rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.risk-pill--high { background: #F6DDDA; color: #8E342B; }
.risk-pill--medium { background: #F7E9CE; color: #7B5723; }
.risk-pill--low { background: var(--yz-blue-soft); color: #2E5B80; }

.status-pill {
  display: inline-block;
  border-radius: 999px;
  padding: 0.18rem 0.7rem;
  font-size: 0.72rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
  letter-spacing: 0.03em;
  background: var(--yz-bg);
  border: 1px solid var(--yz-line);
  color: var(--yz-ink);
}

.empty-catchup {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px dashed var(--yz-glass-border);
  border-radius: 18px;
  padding: 1.4rem 1.3rem;
  margin-top: 0.4rem;
}

.empty-catchup h3 {
  margin: 0 0 0.35rem !important;
  font-size: 1.2rem !important;
}

.empty-catchup p {
  margin: 0;
  color: var(--yz-muted);
}

/* ---------- Team Insights summary/gap cards ---------- */
.v2-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin: .2rem 0 1.1rem;
}

.v2-page-title {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 2rem;
  line-height: 1.05;
  color: var(--yz-ink);
}

.v2-page-subtitle {
  color: var(--yz-muted);
  margin-top: .35rem;
}

.v2-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: .8rem;
  margin: .9rem 0 1.25rem;
}

.v2-summary-card {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 14px;
  padding: 1rem 1.05rem;
  min-height: 118px;
}

.v2-summary-card--critical { background: #FAEFEE; }
.v2-summary-card--attention { background: #FBF3E7; }
.v2-summary-card--covered { background: var(--yz-blue-soft); }

.v2-summary-number {
  font-family: var(--yz-font-display);
  font-size: 1.95rem;
  font-weight: 800;
  color: var(--yz-brown-strong);
  line-height: 1;
}

.v2-summary-label {
  font-family: var(--yz-font-display);
  font-weight: 700;
  margin-top: .45rem;
}

.v2-summary-help {
  color: var(--yz-muted);
  font-size: .83rem;
  margin-top: .2rem;
}

.v2-section-title {
  font-family: var(--yz-font-display);
  font-size: 1.2rem;
  font-weight: 700;
  margin: 1.1rem 0 .65rem;
  color: var(--yz-ink);
}

.v2-gap-card {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 14px;
  padding: 1rem 1.05rem;
  margin-bottom: .75rem;
}

.v2-gap-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: .75rem;
}

.v2-gap-name {
  font-family: var(--yz-font-display);
  font-size: 1.03rem;
  font-weight: 700;
}

.v2-pill {
  display: inline-block;
  border-radius: 999px;
  padding: .2rem .65rem;
  font-size: .72rem;
  font-weight: 700;
}
.v2-pill--critical { background: #F6DDDA; color: #8E342B; }
.v2-pill--attention { background: #F7E9CE; color: #7B5723; }

.v2-gap-note {
  color: var(--yz-muted);
  font-size: .86rem;
  margin: .35rem 0 .7rem;
}

.v2-gap-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: .65rem;
}

.v2-mini-label { color: var(--yz-muted); font-size: .76rem; }
.v2-mini-value { font-family: var(--yz-font-display); font-weight: 700; font-size: 1.08rem; }

@media (max-width: 900px) {
  .v2-summary-grid, .v2-gap-stats { grid-template-columns: 1fr; }
}

/* ---------- Unified data table (My Requests, Team Insights, Users,
   Audit log — one visual language for every list in the app, built from
   real st.columns rows so a per-row View button can run real Python,
   never a plain st.dataframe grid). ---------- */
.dt-card {
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: .5rem .6rem .15rem;
  margin-bottom: 1rem;
}

.dt-head-cell {
  background: #F3EADF;
  color: #6B5A4A;
  font-size: 12.5px;
  font-weight: 600;
  font-family: var(--yz-font-display);
  padding: .5rem .7rem;
  border-radius: 8px;
  text-transform: uppercase;
  letter-spacing: .04em;
  margin-bottom: .3rem;
}

.dt-cell {
  padding: 14px .7rem;
  color: #1D1512;
  font-size: 14.5px;
  line-height: 1.4;
}

[class*="st-key-dtrow_"] {
  border-bottom: 1px solid #ECE3D7;
  border-radius: 8px;
  transition: background .12s ease;
}
[class*="st-key-dtrow_"]:hover {
  background: rgba(164,188,212,0.18);
}
[class*="st-key-dtrow_"] .stButton {
  padding-top: 8px;
}
[class*="st-key-dtrow_"] .stButton > button {
  border: 1px solid #462E24 !important;
  background: #462E24 !important;
  color: #FFFFFF !important;
  box-shadow: none !important;
  font-weight: 600 !important;
  white-space: nowrap !important;
  padding-left: .5rem !important;
  padding-right: .5rem !important;
}
[class*="st-key-dtrow_"] .stButton > button:hover {
  background: #35221A !important;
}
[class*="st-key-dtrow_"] .stButton > button p,
[class*="st-key-dtrow_"] .stButton > button span,
[class*="st-key-dtrow_"] .stButton > button div {
  color: #FFFFFF !important;
}

.dt-type-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.dt-pill {
  display: inline-flex;
  align-items: center;
  gap: .3rem;
  border-radius: 999px;
  padding: .25rem .7rem;
  font-size: .78rem;
  font-weight: 700;
  font-family: var(--yz-font-display);
  white-space: nowrap;
}

.dt-empty {
  padding: 1.2rem .75rem;
  color: var(--yz-muted);
  font-size: .92rem;
}

/* ---------- Payroll ---------- */
.yz-confidential-banner {
  background: #FBF3E7;
  border: 1px solid #E8D4BB;
  border-radius: 12px;
  padding: .75rem 1rem;
  margin-bottom: 1rem;
  color: #5B4C40;
  font-size: .88rem;
  line-height: 1.5;
}
.yz-confidential-banner strong {
  color: #462E24;
  margin-inline-end: .3rem;
}

.yz-file-card {
  display: flex;
  align-items: center;
  gap: .8rem;
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 14px;
  padding: .9rem 1.1rem;
  margin-bottom: .7rem;
}
.yz-file-card-icon { font-size: 1.6rem; }
.yz-file-card-name {
  font-weight: 700;
  color: #1D1512;
  font-family: var(--yz-font-display);
}
.yz-file-card-status {
  font-size: .82rem;
  color: #7A6D60;
  margin-top: .1rem;
}

/* ---------- Growth Opportunities ---------- */
[class*="st-key-growth_card_"] {
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 16px;
  padding: 1.1rem 1.3rem;
  margin-bottom: .3rem;
}
.yz-growth-card-title {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 1.15rem;
  color: #1D1512;
}

/* ---------- Dashboard stat tiles (role-scoped real counts) ---------- */
.yz-dash-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: .8rem;
  margin-bottom: 1rem;
}
.yz-dash-tile {
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 16px;
  padding: 1.1rem 1.2rem;
}
.yz-dash-tile-value {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 1.9rem;
  color: #1D1512;
  line-height: 1;
}
.yz-dash-tile-label {
  color: #7A6D60;
  font-size: .85rem;
  margin-top: .35rem;
}

/* ---------- Detail card (glass label/value grid) ---------- */
.dt-detail-card {
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 16px;
  padding: 1rem 1.1rem .3rem;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 1.5rem;
  margin: .5rem 0 1rem;
}
.dt-detail-cell {
  margin-bottom: .8rem;
}
.dt-detail-label {
  font-size: .74rem;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: #7A6D60;
  margin-bottom: .2rem;
}
.dt-detail-value {
  font-size: .95rem;
  color: #1D1512;
  line-height: 1.4;
}
@media (max-width: 700px) {
  .dt-detail-card { grid-template-columns: 1fr; }
}

/* ---------- Sources (Ask Yusor + Decision Brief) ---------- */
.dt-source-card {
  background: rgba(255,252,247,0.7);
  border: 1px solid var(--yz-glass-border);
  border-radius: 12px;
  padding: .65rem .9rem;
  margin: .5rem 0 .3rem;
}
.dt-source-title {
  font-family: var(--yz-font-display);
  font-weight: 700;
  font-size: .92rem;
  color: var(--yz-brown-strong);
}
.dt-source-rule {
  color: var(--yz-ink);
  font-size: .88rem;
  margin-top: .3rem;
  line-height: 1.45;
}

/* ---------- Secondary (outline) button variant — used for lower-emphasis
   actions like "Ask Yusor about this gap", as opposed to the solid brown
   default/primary button. Scope with st.container(key="yz_btn_secondary_*")
   per the established wildcard-key CSS pattern. ---------- */
[class*="st-key-yz_btn_secondary_"] .stButton > button {
  background: transparent !important;
  border: 1px solid #462E24 !important;
  color: #462E24 !important;
  box-shadow: none !important;
}
[class*="st-key-yz_btn_secondary_"] .stButton > button:hover {
  background: rgba(70,46,36,0.08) !important;
}
[class*="st-key-yz_btn_secondary_"] .stButton > button p,
[class*="st-key-yz_btn_secondary_"] .stButton > button span,
[class*="st-key-yz_btn_secondary_"] .stButton > button div {
  color: #462E24 !important;
}

/* ---------- Team readiness ring (manager Dashboard) ---------- */
.yz-readiness {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: 1.3rem 1.5rem;
  margin-bottom: 1rem;
}

.yz-readiness-ring {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.yz-readiness-ring-inner {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  background: #FBF8F3;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.yz-readiness-pct {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 1.7rem;
  color: #1D1512;
  line-height: 1;
}

.yz-readiness-status {
  font-size: .78rem;
  color: #7A6D60;
  margin-top: .25rem;
}

.yz-readiness-legend {
  display: flex;
  flex-direction: column;
  gap: .55rem;
}

.yz-legend-row {
  font-size: .92rem;
  color: #1D1512;
  display: flex;
  align-items: center;
  gap: .5rem;
}

.yz-legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* ---------- Dashboard's Ask Yusor card ---------- */
.yz-ask-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: rgba(255,252,247,0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-radius: 18px;
  padding: 1.2rem 1.4rem;
  margin-bottom: .6rem;
}

.yz-ask-card-leaf svg {
  display: block;
  flex-shrink: 0;
}

.yz-ask-card-title {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 1.25rem;
  color: #1D1512;
}

.yz-ask-card-sub {
  color: #7A6D60;
  font-size: .88rem;
  margin-top: .2rem;
}

.st-key-yz_dashboard_open_chat button {
  max-width: 220px;
}

/* ---------- Selectbox (Team Insights' Department picker etc.) — explicit
   light background + dark text/arrow so it stays legible regardless of
   the Streamlit base theme. ---------- */
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
  background: #FFFFFF !important;
  border-color: #D9CFC2 !important;
  color: var(--yz-ink) !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] svg {
  fill: var(--yz-ink) !important;
}
[data-baseweb="popover"] [data-baseweb="menu"] {
  background: #FFFFFF !important;
}
[data-baseweb="popover"] [data-baseweb="menu"] li {
  color: var(--yz-ink) !important;
}

/* ---------- Ask Yusor chat ---------- */
.yz-chat-header {
  margin: 0 0 .9rem;
}

.yz-chat-title {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 1.7rem;
  color: var(--yz-ink);
  line-height: 1.1;
}

.yz-chat-title--leaf {
  display: flex;
  align-items: center;
  gap: .5rem;
}
.yz-chat-leaf svg {
  display: block;
  flex-shrink: 0;
}

.yz-chat-subtitle {
  color: var(--yz-muted);
  font-size: .92rem;
  margin-top: .15rem;
}

.yz-chat-scroll {
  min-height: 20vh;
}

.yz-msg-row {
  display: flex;
  margin-bottom: .65rem;
}
.yz-msg-row--user { justify-content: flex-end; }
.yz-msg-row--assistant { justify-content: flex-start; }

.yz-bubble {
  max-width: 72%;
  padding: .75rem .95rem;
  border-radius: 16px;
  font-size: .95rem;
  line-height: 1.55;
}

.yz-bubble--assistant {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--yz-glass-border);
  border-bottom-left-radius: 4px;
  color: var(--yz-ink);
}

.yz-bubble--user {
  background: rgba(164,188,212,0.35);
  border: 1px solid rgba(164,188,212,0.55);
  border-bottom-right-radius: 4px;
  color: var(--yz-ink);
}

.yz-bubble[dir="rtl"] { text-align: right; }
.yz-bubble[dir="ltr"] { text-align: left; }

.yz-source-row {
  display: flex;
  flex-wrap: wrap;
  gap: .35rem;
  margin: .5rem 0 .9rem;
}

.yz-source-chip {
  display: inline-block;
  background: var(--yz-bg);
  border: 1px solid var(--yz-line);
  border-radius: 999px;
  padding: .15rem .6rem;
  font-size: .7rem;
  font-family: var(--yz-font-display);
  font-weight: 700;
  color: var(--yz-brown);
}

.yz-welcome {
  background: var(--yz-glass-bg);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px dashed var(--yz-glass-border);
  border-radius: 16px;
  padding: 1.4rem 1.5rem;
  color: var(--yz-muted);
  margin-bottom: 1rem;
}
.yz-welcome strong { color: var(--yz-ink); }

.yz-suggestions-label {
  font-size: .74rem;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--yz-muted);
  font-family: var(--yz-font-display);
  font-weight: 700;
  margin: .3rem 0 .45rem;
}

@keyframes yz-shake {
  0% { transform: translateX(0); }
  25% { transform: translateX(-3px); }
  50% { transform: translateX(3px); }
  75% { transform: translateX(-2px); }
  100% { transform: translateX(0); }
}

[class*="st-key-yz_sugg_"] button {
  background: #FFFFFF !important;
  border: 1px solid var(--yz-line) !important;
  font-weight: 600 !important;
  box-shadow: none !important;
}
[class*="st-key-yz_sugg_"] button p,
[class*="st-key-yz_sugg_"] button span,
[class*="st-key-yz_sugg_"] button div {
  color: var(--yz-brown-strong) !important;
}
[class*="st-key-yz_sugg_"] button:hover {
  background: var(--yz-blue-soft) !important;
  border-color: var(--yz-blue) !important;
}
[class*="st-key-yz_sugg_"] button:active {
  animation: yz-shake .25s ease-in-out;
}

[data-testid="stBottomBlockContainer"] {
  background: transparent !important;
}
[data-testid="stChatInput"] {
  background: rgba(255,252,247,0.85) !important;
  border: 1px solid #E8D4BB !important;
  border-radius: 16px !important;
}
[data-testid="stChatInput"] [data-baseweb="textarea"],
[data-testid="stChatInput"] [data-baseweb="base-input"] {
  background: transparent !important;
}
[data-testid="stChatInput"] textarea {
  min-height: 52px !important;
  background: transparent !important;
  color: #111312 !important;
}
[data-testid="stChatInput"] textarea::placeholder {
  color: #6F6B65 !important;
  opacity: 1;
}
[data-testid="stChatInputSubmitButton"] {
  background: #462E24 !important;
  border-radius: 50% !important;
  border: none !important;
}
[data-testid="stChatInputSubmitButton"] svg {
  fill: #FFFFFF !important;
  color: #FFFFFF !important;
}

/* ---------- Welcome bar (page_header) — transparent, no card, sits over
   the shell photo above every page's own title. ---------- */
.yz-welcome-row {
  display: flex;
  align-items: center;
  gap: .75rem;
}

.yz-welcome-leaf svg {
  display: block;
  flex-shrink: 0;
}

.yz-welcome-hello {
  font-family: var(--yz-font-display);
  font-weight: 800;
  font-size: 24px;
  color: #1D1512;
  line-height: 1.25;
}

.yz-welcome-role {
  font-size: .85rem;
  color: #7A6D60;
  margin-top: .15rem;
}

.yz-welcome-subtitle {
  display: flex;
  align-items: center;
  gap: .4rem;
  color: #5B4C40;
  font-size: .85rem;
  margin-top: .5rem;
}

.yz-welcome-subtitle-icon {
  display: inline-flex;
  flex-shrink: 0;
}
.yz-welcome-subtitle-icon svg {
  display: block;
}

.yz-welcome-date {
  font-size: .85rem;
  color: #5B4C40;
  text-align: right;
  font-weight: 600;
}

.yz-welcome-hijri {
  font-size: .72rem;
  color: #7A6D60;
  text-align: right;
  margin-top: .1rem;
}

.st-key-yz_page_header {
  margin-bottom: 24px;
}

.st-key-yz_header_refresh button {
  width: 40px !important;
  height: 40px !important;
  min-width: 40px !important;
  padding: 0 !important;
  margin-left: auto !important;
  border-radius: 10px !important;
  border: 1px solid #E8D4BB !important;
  background: rgba(255,252,247,0.55) !important;
  box-shadow: none !important;
  font-size: 17px !important;
  line-height: 1 !important;
}
.st-key-yz_header_refresh button:hover {
  background: rgba(255,252,247,0.9) !important;
}
.st-key-yz_header_refresh button p,
.st-key-yz_header_refresh button span,
.st-key-yz_header_refresh button div {
  color: #462E24 !important;
}

.st-key-yz_header_lang_toggle button {
  height: 40px !important;
  padding: 0 .85rem !important;
  border-radius: 10px !important;
  border: 1px solid #E8D4BB !important;
  background: rgba(255,252,247,0.55) !important;
  box-shadow: none !important;
  font-weight: 600 !important;
  white-space: nowrap !important;
  width: auto !important;
  min-width: 100%;
}
.st-key-yz_header_lang_toggle button:hover {
  background: rgba(255,252,247,0.9) !important;
}
.st-key-yz_header_lang_toggle button p,
.st-key-yz_header_lang_toggle button span,
.st-key-yz_header_lang_toggle button div {
  color: #462E24 !important;
}

.st-key-yz_header_action {
  margin-top: .5rem;
}
.st-key-yz_header_action button {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  font-weight: 600 !important;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.st-key-yz_header_action button:hover {
  transform: none !important;
}
.st-key-yz_header_action button p,
.st-key-yz_header_action button span,
.st-key-yz_header_action button div {
  color: #462E24 !important;
}
</style>
"""


def inject() -> None:
    """The whole app's CSS. Safe on every page, including login."""
    st.markdown(CSS, unsafe_allow_html=True)


def login_page_style() -> None:
    """Everything the login screen needs on top of inject(): full-bleed photo
    background on stAppViewContainer, the header bar hidden, the glass card
    styling applied straight to st.form itself (so there's exactly one card,
    not a white box inside a glass box), input/button styling (including the
    browser-autofill override), and the fixed brand mark outside the card.
    """
    uri = _login_bg_data_uri()
    bg_rule = (
        f"""
        [data-testid="stAppViewContainer"] {{
          background-image:
            linear-gradient(180deg, rgba(36,26,20,.20) 0%, rgba(36,26,20,.38) 100%),
            url('{uri}');
          background-size: cover;
          background-position: center;
          background-repeat: no-repeat;
        }}
        """
        if uri
        else ""
    )

    st.markdown(
        f"""
        <style>
        {bg_rule}

        [data-testid="stHeader"] {{ display: none !important; }}

        /* The card IS the form — one box, not a white box nested in a glass one. */
        [data-testid="stForm"] {{
          background: rgba(255,250,244,0.40) !important;
          backdrop-filter: blur(10px);
          -webkit-backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.55) !important;
          border-radius: 20px !important;
          padding: 32px !important;
          box-shadow: 0 24px 60px rgba(20,14,10,.28);
          width: min(94vw, 500px);
          margin: 12vh auto 6vh !important;
        }}

        [data-testid="stForm"] label p {{
          color: #241A14 !important;
          font-weight: 600;
        }}

        .yz-login-kicker {{
          font-family: var(--yz-font-display);
          font-weight: 800;
          letter-spacing: 0.18em;
          font-size: var(--yz-text-sm);
          color: #55392A;
        }}

        .yz-login-title {{
          font-family: var(--yz-font-display);
          font-weight: 800;
          font-size: var(--yz-text-2xl);
          margin: .35rem 0 .3rem;
          color: #241A14;
        }}

        .yz-login-sub {{
          color: #3B2C22;
          font-size: var(--yz-text-md);
          margin-bottom: var(--yz-space-md);
        }}

        [data-testid="stForm"] [data-testid="stTextInputRootElement"] {{
          background: rgba(255,255,255,0.75) !important;
          border-color: rgba(255,255,255,0.8) !important;
          border-radius: 10px !important;
          height: 48px !important;
        }}

        [data-testid="stForm"] [data-testid="stTextInput"] input {{
          background: transparent !important;
          color: #111312 !important;
          height: 46px !important;
        }}

        [data-testid="stForm"] [data-testid="stTextInput"] input::placeholder {{
          color: #6F6B65 !important;
          opacity: 1;
        }}

        /* Chrome/Edge autofill paints its own background + text color (the
           grey-blue-with-white-text look) via a UA layer that ordinary
           background/color rules can't reach — this is the standard
           override: an inset box-shadow big enough to cover the field
           fakes a background color, and -webkit-text-fill-color overrides
           the autofill text color. Matches the field's normal (non-autofill)
           styling above so Username looks identical to Password in every
           state. */
        [data-testid="stForm"] [data-testid="stTextInput"] input:-webkit-autofill,
        [data-testid="stForm"] [data-testid="stTextInput"] input:-webkit-autofill:hover,
        [data-testid="stForm"] [data-testid="stTextInput"] input:-webkit-autofill:focus {{
          -webkit-text-fill-color: #111312 !important;
          -webkit-box-shadow: 0 0 0 1000px rgba(255,255,255,0.75) inset !important;
          box-shadow: 0 0 0 1000px rgba(255,255,255,0.75) inset !important;
          caret-color: #111312;
          transition: background-color 5000s ease-in-out 0s;
        }}

        /* Password show/hide eye icon: keep it dark and visible. */
        [data-testid="stForm"] [data-testid="stTextInput"] button svg {{
          fill: #462E24 !important;
        }}

        [data-testid="stForm"] .stFormSubmitButton > button,
        [data-testid="stForm"] .stFormSubmitButton > button p,
        [data-testid="stForm"] .stFormSubmitButton > button span,
        [data-testid="stForm"] .stFormSubmitButton > button div {{
          color: #FFFFFF !important;
        }}

        [data-testid="stForm"] .stFormSubmitButton > button {{
          background: #462E24 !important;
          border-color: #462E24 !important;
          height: 48px !important;
          width: 100%;
        }}

        .yz-login-brand {{
          position: fixed;
          top: 2rem;
          left: 2.4rem;
          z-index: 5;
        }}

        .yz-login-brand-en {{
          font-family: var(--yz-font-display);
          font-weight: 800;
          letter-spacing: .35em;
          font-size: 1.05rem;
          color: #462E24;
          margin: .1rem 0 .15rem;
        }}

        .yz-login-brand-tagline {{
          color: rgba(70,46,36,.85);
          font-size: .85rem;
          margin-top: .4rem;
          font-weight: 600;
        }}
        </style>

        <div class="yz-login-brand">
          {brand_logo("brown", size=180, wordmark_class="yz-login-brand-en")}
          <div class="yz-login-brand-tagline">People. Processes.<br>A Smarter Tomorrow.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def shell_background() -> None:
    """Full-bleed background photo for every authenticated page. Separate
    from login_page_style()'s background (that one uses a darker overlay
    tuned for the glass login card); this is the lighter wash the whole
    signed-in shell sits on, with sidebar/cards/tables/chat bubbles going
    translucent so the photo shows through them slightly.
    """
    uri = _login_bg_data_uri()
    if not uri:
        return
    st.markdown(
        f"""
        <style>
        [data-testid="stAppViewContainer"] {{
          background-image:
            linear-gradient(rgba(248,244,238,0.55), rgba(248,244,238,0.35)),
            url('{uri}');
          background-size: cover;
          background-position: center bottom;
          background-attachment: fixed;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


_HIJRI_MONTHS = [
    "Muharram", "Safar", "Rabi' al-Awwal", "Rabi' al-Thani",
    "Jumada al-Awwal", "Jumada al-Thani", "Rajab", "Sha'ban",
    "Ramadan", "Shawwal", "Dhu al-Qi'dah", "Dhu al-Hijjah",
]


def _gregorian_to_hijri(d: date) -> tuple[int, int, int]:
    """Pure-arithmetic tabular Hijri conversion (the public-domain "Kuwaiti
    algorithm") — no calendar library is installed in this environment, and
    the request said to skip the Hijri date rather than add one. This is an
    approximation of the official Umm al-Qura civil calendar and can drift
    a day or two around month boundaries; it's used only for a small
    secondary date label, never for anything date-logic-sensitive.
    """
    jd = d.toordinal() + 1721425
    ll = jd - 1948440 + 10632
    n = (ll - 1) // 10631
    ll = ll - 10631 * n + 354
    j = ((10985 - ll) // 5316) * ((50 * ll) // 17719) + (ll // 5670) * ((43 * ll) // 15238)
    ll = ll - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    month = (24 * ll) // 709
    day = ll - (709 * month) // 24
    year = 30 * n + j - 30
    return year, month, day


def _hijri_date_text(d: date) -> str:
    year, month, day = _gregorian_to_hijri(d)
    if not (1 <= month <= 12):
        return ""
    return f"{day:02d} {_HIJRI_MONTHS[month - 1]} {year} AH"


def page_header(
    display_name: str,
    role_label: str,
    *,
    name_rtl: bool = False,
    action_text: str | None = None,
    action_page: str | None = None,
) -> None:
    """Global welcome bar shown above every authenticated page. Left: greeting
    + role, from the real signed-in user (caller passes already-resolved
    values, same pattern as hero()/page_head()). Right: today's date + an
    approximate Hijri date, and a 40x40 icon-only refresh button (identical
    behavior to the old sidebar "Refresh data" button — st.rerun()). Below
    that, an optional single clickable line for a real, role-scoped nudge
    (pending decisions for managers/HR, latest request status change for
    everyone else) — the caller computes action_text/action_page from real
    API data; this function only renders and handles the click/navigation.
    No card background — transparent over the shell photo.
    """
    today = date.today()
    date_text = today.strftime("%A, %d %b %Y")
    hijri_text = _hijri_date_text(today)
    name_dir = "rtl" if name_rtl else "ltr"
    name_html = f'<span dir="{name_dir}">{html.escape(display_name)}</span>'
    lang = i18n.get_lang()

    period = _time_of_day_riyadh()
    greeting_text = i18n.t(_TIME_GREETING_KEYS[period], name=name_html)
    time_icon = _TIME_ICON_SVGS[{"morning": "sun", "afternoon": "sunset", "evening": "moon"}[period]]

    with st.container(key="yz_page_header"):
        left, right = st.columns([3, 2])
        with left:
            st.markdown(
                '<div class="yz-welcome-row">'
                f'{leaf_icon_html("yz-welcome-leaf")}'
                "<div>"
                f'<div class="yz-welcome-hello">{greeting_text}</div>'
                f'<div class="yz-welcome-role">{html.escape(role_label)}</div>'
                "</div>"
                "</div>"
                '<div class="yz-welcome-subtitle">'
                f'<span class="yz-welcome-subtitle-icon">{time_icon}</span>'
                f"{html.escape(i18n.t('greeting.subtitle'))}"
                "</div>",
                unsafe_allow_html=True,
            )
        with right:
            date_col, toggle_col, refresh_col = st.columns([3.4, 1.9, 1])
            with date_col:
                hijri_html = (
                    f'<div class="yz-welcome-hijri">{hijri_text}</div>' if hijri_text else ""
                )
                st.markdown(
                    f'<div class="yz-welcome-date">{date_text}</div>{hijri_html}',
                    unsafe_allow_html=True,
                )
            with toggle_col:
                with st.container(key="yz_header_lang_toggle"):
                    toggle_label = (
                        i18n.t("lang.toggle_to_en")
                        if lang == "ar"
                        else i18n.t("lang.toggle_to_ar")
                    )
                    if st.button(toggle_label):
                        i18n.set_lang("en" if lang == "ar" else "ar")
                        st.rerun()
            with refresh_col:
                with st.container(key="yz_header_refresh"):
                    if st.button("⟳", help=i18n.t("welcome.refresh_tooltip")):
                        st.rerun()

        if action_text and action_page:
            with st.container(key="yz_header_action"):
                # Streamlit forbids writing st.session_state["nav_page"]
                # once the sidebar's st.radio(key="nav_page") has already
                # been instantiated in this same run (it has, by the time
                # page_header() renders) — so the click stashes the target
                # in a plain, non-widget-bound key instead, and _shell()
                # resolves it into nav_page at the top of the *next* run,
                # before the sidebar is built.
                if st.button(action_text):
                    st.session_state["_yz_pending_nav"] = action_page
                    st.rerun()


def stat_tiles_html(items: list[tuple[str, object]]) -> str:
    """A row of simple glass stat tiles for the role-scoped Dashboard —
    label above, big real number below. `items` is caller-computed from
    real API data (pending items, open grievances, active users, ...)."""
    if not items:
        return ""
    cells = "".join(
        '<div class="yz-dash-tile">'
        f'<div class="yz-dash-tile-value">{html.escape(str(value))}</div>'
        f'<div class="yz-dash-tile-label">{html.escape(str(label))}</div>'
        "</div>"
        for label, value in items
    )
    return f'<div class="yz-dash-tiles">{cells}</div>'


def readiness_ring_html(critical: int, other: int, covered: int) -> str:
    """Team readiness donut for the manager Dashboard — pure CSS
    conic-gradient, no charting library. `critical`/`other`/`covered` are
    real skill counts aggregated across every department's real
    GET /experience-gap/{id} response (caller's job — this function only
    renders). Returns "" when there's nothing to show (caller should skip
    rendering entirely rather than show an empty/zero ring, per the
    redesign spec: "don't show the circle if the data is incomplete").
    """
    total = critical + other + covered
    if total <= 0:
        return ""
    pct = round(covered / total * 100)
    if pct < 50:
        ring_color = "#B5564A"
        status_key = "dashboard.status_low"
    elif pct < 80:
        ring_color = "#D19A3F"
        status_key = "dashboard.status_moderate"
    else:
        ring_color = "#5E8C5A"
        status_key = "dashboard.status_strong"
    status_label = html.escape(i18n.t(status_key))

    return (
        '<div class="yz-readiness">'
        f'<div class="yz-readiness-ring" style="background: conic-gradient({ring_color} {pct}%, #E8D4BB {pct}% 100%);">'
        '<div class="yz-readiness-ring-inner">'
        f'<div class="yz-readiness-pct">{pct}%</div>'
        f'<div class="yz-readiness-status">{status_label}</div>'
        "</div>"
        "</div>"
        '<div class="yz-readiness-legend">'
        '<div class="yz-legend-row"><span class="yz-legend-dot" style="background:#B5564A"></span>'
        f'{html.escape(i18n.t("dashboard.readiness_critical"))} <strong>{critical}</strong></div>'
        '<div class="yz-legend-row"><span class="yz-legend-dot" style="background:#D19A3F"></span>'
        f'{html.escape(i18n.t("dashboard.readiness_attention"))} <strong>{other}</strong></div>'
        '<div class="yz-legend-row"><span class="yz-legend-dot" style="background:#5E8C5A"></span>'
        f'{html.escape(i18n.t("dashboard.readiness_covered"))} <strong>{covered}</strong></div>'
        "</div>"
        "</div>"
    )


def apply_language(lang: str) -> None:
    """RTL/font switch for the authenticated shell when Arabic is selected.
    Called once, early in _shell(), before the sidebar/content render.

    Flipping `direction: rtl` on the app's outer flex container is what
    moves the sidebar to the right — Streamlit lays the sidebar and main
    content out as flex siblings, and `direction` on their parent reverses
    visual flex order, not just text direction. The same rule cascades
    into every st.columns() row app-wide (data_table headers/rows, the
    welcome bar's left/right split, decision forms, etc.), which is
    exactly what's wanted for a fully-RTL page — no per-component
    reordering needed. English mode renders nothing here (default LTR).
    """
    if lang != "ar":
        return
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700;800&display=swap');

        [data-testid="stAppViewContainer"] { direction: rtl; }
        section[data-testid="stSidebar"] { direction: rtl; text-align: right; }
        section[data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {
          margin-right: 0;
        }
        .main .block-container { direction: rtl; text-align: right; }

        html, body, [class*="css"], .stApp,
        .stMarkdown, .stText, p, span, div, label, button,
        input, textarea, select {
          font-family: 'IBM Plex Sans Arabic', 'Source Sans 3', sans-serif;
        }
        h1, h2, h3,
        .stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
        .yz-page-title, .yz-chat-title, .v2-page-title,
        .yz-sidebar-brand, .yz-welcome-hello, .dt-head-cell {
          font-family: 'IBM Plex Sans Arabic', 'Outfit', sans-serif !important;
        }

        .yz-welcome-date, .yz-welcome-hijri { text-align: left; }
        .dt-type-dot { margin-inline-end: 6px; margin-inline-start: 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_head(kicker: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"""
        <div class="yz-page-head">
          <div>
            <div class="yusor-kicker">{kicker}</div>
            <div class="yz-page-title">{title}</div>
            <div class="yz-page-subtitle">{subtitle}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def placeholder(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="yz-placeholder">
          <div class="yz-placeholder-title">{title}</div>
          <div>{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="yusor-hero">
          <div class="yusor-kicker">{kicker}</div>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def stat_card(label: str, value, variant: str = "blue") -> str:
    return (
        f'<div class="stat-card stat-card--{variant}">'
        f'<div class="stat-label">{label}</div>'
        f'<div class="stat-value">{value if value is not None else "—"}</div>'
        f'<div class="stat-sub">days remaining</div>'
        f"</div>"
    )


def queue_stats(waiting: int, approved: int, sent_back: int) -> str:
    return (
        '<div class="queue-row">'
        f'<div class="queue-stat queue-stat--wait"><div class="queue-stat-label">Waiting</div>'
        f'<div class="queue-stat-value">{waiting}</div></div>'
        f'<div class="queue-stat"><div class="queue-stat-label">Approved</div>'
        f'<div class="queue-stat-value">{approved}</div></div>'
        f'<div class="queue-stat queue-stat--back"><div class="queue-stat-label">Sent back</div>'
        f'<div class="queue-stat-value">{sent_back}</div></div>'
        "</div>"
    )


def grievance_queue_stats(
    waiting: int,
    submitted: int,
    sent_back: int,
) -> str:
    return (
        '<div class="queue-row">'
        f'<div class="queue-stat queue-stat--wait"><div class="queue-stat-label">Waiting</div>'
        f'<div class="queue-stat-value">{waiting}</div></div>'
        f'<div class="queue-stat"><div class="queue-stat-label">Submitted</div>'
        f'<div class="queue-stat-value">{submitted}</div></div>'
        f'<div class="queue-stat queue-stat--back"><div class="queue-stat-label">Sent back</div>'
        f'<div class="queue-stat-value">{sent_back}</div></div>'
        "</div>"
    )


# =============================================================
# Unified data table — replaces every st.dataframe / ad-hoc list in the
# app (My Requests, Team Insights' covered-skills table, Users, Audit
# log; Waiting on You and Grievances can adopt it too). One visual
# language, one Python component.
# =============================================================

def status_pills() -> dict[str, tuple[str, str, str, str]]:
    """Built fresh (not a module-level constant) so pill labels follow the
    current i18n.get_lang() — called on every render, not cached."""
    return {
        # key -> (label, icon, background, text color)
        "approved": (i18n.t("status.approved"), "✓", "#E3EEDC", "#35592A"),
        "pending": (i18n.t("status.pending"), "◔", "#EDE6DA", "#5E5241"),
        "waiting": (i18n.t("status.pending"), "◔", "#EDE6DA", "#5E5241"),
        # The DB's "rejected" status has always meant "sent back to the
        # requester to revise" in this app (see page_approvals' own copy:
        # "Sent back to {who}. Your note is on the record.") — not a final
        # denial — so it maps to the Sent back pill, not Rejected.
        "rejected": (i18n.t("status.sent_back"), "↩", "#F6E7CF", "#7A4E12"),
        "sent_back": (i18n.t("status.sent_back"), "↩", "#F6E7CF", "#7A4E12"),
        # Reserved for a genuinely final rejection — no current status value
        # produces this, but callers can map one via status_styles.
        "denied": (i18n.t("status.rejected"), "✕", "#F6DDDA", "#8E2622"),
        "active": (i18n.t("status.active"), "✓", "#E3EEDC", "#35592A"),
        "inactive": (i18n.t("status.inactive"), "✕", "#F1E9DE", "#6B5A4A"),
    }


# TYPE_BADGES: colored-dot + label used by the "Waiting on you" inbox's Type
# column — colors are fixed (not language-dependent), labels are supplied
# translated by the caller via type_badge_html()'s `label` argument.
TYPE_BADGES: dict[str, str] = {
    "leave": "#A4BCD4",
    "grievance": "#C8923E",
    "approval": "#847860",
}


def type_badge_html(kind: str, label: str) -> str:
    color = TYPE_BADGES.get(kind, "#847860")
    return (
        f'<span class="dt-type-dot" style="background:{color}"></span>'
        f"{html.escape(label)}"
    )


def status_pill_html(raw_status, styles_map: dict) -> str:
    key = str(raw_status or "").strip().lower().replace(" ", "_")
    label, icon, bg, color = styles_map.get(
        key, (str(raw_status or "Unknown").title(), "•", "#F1E9DE", "#6B5A4A")
    )
    return (
        f'<span class="dt-pill" style="background:{bg};color:{color};">'
        f"{icon} {html.escape(label)}</span>"
    )


def data_table(
    rows: list[dict],
    columns: list[tuple[str, str]],
    *,
    status_key: str | None = None,
    status_styles: dict | None = None,
    raw_html_keys: set[str] | None = None,
    on_view: Callable[[dict], None] | None = None,
    view_label: str | None = None,
    row_id: str = "id",
    key: str = "table",
    empty_message: str = "No records.",
) -> None:
    """Glass-card table used everywhere a list of records is shown.

    `columns` is `[(row_dict_key, column_label), ...]` in display order —
    callers pre-format each cell's display value (dates, "type" casing,
    etc.); this function only handles the shared chrome: header, dividers,
    hover, the status pill (when `status_key` names a column), an optional
    already-built-HTML column (`raw_html_keys`, e.g. the inbox's colored
    type badge — caller is responsible for escaping any raw data in it),
    and an optional real "View"/"Review" button per row via `on_view(row)`.
    """
    styles_map = {**status_pills(), **(status_styles or {})}
    view_label = view_label or i18n.t("grievances.col_view")
    weights = [1.0] * len(columns) + ([0.65] if on_view else [])

    st.markdown('<div class="dt-card">', unsafe_allow_html=True)

    header_cols = st.columns(weights)
    for col, (_, label) in zip(header_cols, columns):
        col.markdown(f'<div class="dt-head-cell">{html.escape(label)}</div>', unsafe_allow_html=True)
    if on_view:
        header_cols[-1].markdown('<div class="dt-head-cell">&nbsp;</div>', unsafe_allow_html=True)

    if not rows:
        st.markdown(f'<div class="dt-empty">{html.escape(empty_message)}</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        return

    for i, row in enumerate(rows):
        with st.container(key=f"dtrow_{key}_{i}"):
            cols = st.columns(weights)
            for col, (col_key, _) in zip(cols, columns):
                if status_key and col_key == status_key:
                    col.markdown(status_pill_html(row.get(col_key), styles_map), unsafe_allow_html=True)
                elif raw_html_keys and col_key in raw_html_keys:
                    value = row.get(col_key)
                    col.markdown(
                        f'<div class="dt-cell">{value if value else "—"}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    value = row.get(col_key)
                    text = str(value) if value not in (None, "") else "—"
                    col.markdown(f'<div class="dt-cell">{html.escape(text)}</div>', unsafe_allow_html=True)
            if on_view:
                with cols[-1]:
                    if st.button(view_label, key=f"dtviewbtn_{key}_{i}", use_container_width=True):
                        on_view(row)

    st.markdown("</div>", unsafe_allow_html=True)


# =============================================================
# Detail card — replaces the old Field/Value st.dataframe pattern
# (_kv_table) everywhere: grievance/request/decision-brief detail views.
# =============================================================

class RawHTML(str):
    """Marker subclass: a detail_card()/data_table() value that is already
    safe, pre-built HTML and must not be escaped again. Build with raw()."""


def raw(html_str: str) -> "RawHTML":
    return RawHTML(html_str)


def detail_card(pairs: list[tuple[str, object]]) -> None:
    """Glass 2-column label/value grid for a record's details — replaces
    the plain Field/Value st.dataframe previously used for grievance
    details, leave-proposal details, decision-brief precedent counts, and
    generic proposal payloads. A value built with styles.raw(...) (e.g. a
    status pill, or "🔒 Anonymous") renders as-is; anything else is escaped
    plain text, with an em dash for empty/None.
    """
    if not pairs:
        return
    cells = []
    for label, value in pairs:
        if isinstance(value, RawHTML):
            value_html = value
        else:
            text = str(value) if value not in (None, "") else "—"
            value_html = html.escape(text)
        cells.append(
            f'<div class="dt-detail-cell">'
            f'<div class="dt-detail-label">{html.escape(str(label))}</div>'
            f'<div class="dt-detail-value">{value_html}</div>'
            f"</div>"
        )
    st.markdown(f'<div class="dt-detail-card">{"".join(cells)}</div>', unsafe_allow_html=True)


# =============================================================
# Sources — one renderer for every place the app shows agent-provided
# sources (Ask Yusor chat citations, Decision Brief policy sources).
# Titles are parsed from the backend's own `display_name` field (built
# server-side in consultant_agent.py — "Saudi Labor Law — Article N: T" /
# "Internal Policy — Section N: T") and re-rendered through i18n so it
# follows the interface language; never invented client-side. Technical
# fields (score, relevance_score, filename, source_table, id) are
# deliberately never shown — internal-only, per the redesign spec.
# =============================================================

_LAW_SOURCE_RE = re.compile(r"^Saudi Labor Law\s*—\s*Article\s+(\S+):\s*(.*)$")
_POLICY_SOURCE_RE = re.compile(r"^Internal Policy\s*—\s*Section\s+(\S+):\s*(.*)$")


def _source_title(display_name: str) -> str:
    display_name = (display_name or "").strip()
    m = _LAW_SOURCE_RE.match(display_name)
    if m:
        return i18n.t("sources.law_title", article=m.group(1), title=m.group(2))
    m = _POLICY_SOURCE_RE.match(display_name)
    if m:
        return i18n.t("sources.policy_title", section=m.group(1), title=m.group(2))
    return display_name or i18n.t("sources.generic_title")


def _source_rule(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line.lower().startswith("rule:"):
            return line.split(":", 1)[1].strip()
    return ""


def render_sources(sources: list, *, key: str = "src") -> None:
    """`sources` items are either a plain string (an HR-data reference like
    "leave_balances:EMP-0002" — no title/text to show, rendered as a small
    id chip) or a dict from the agent (id/display_name/text/source_table/
    filename/score/relevance_score — only display_name and text are ever
    shown)."""
    if not sources:
        return

    plain_ids = [s.strip() for s in sources if isinstance(s, str) and s.strip()]
    dict_sources = [s for s in sources if isinstance(s, dict)]

    if plain_ids:
        chips = "".join(f'<span class="yz-source-chip">{html.escape(sid)}</span>' for sid in plain_ids)
        st.markdown(f'<div class="yz-source-row">{chips}</div>', unsafe_allow_html=True)

    for i, source in enumerate(dict_sources):
        text = str(source.get("text") or "")
        title = _source_title(str(source.get("display_name") or ""))
        rule = _source_rule(text)
        rule_html = (
            f'<div class="dt-source-rule">'
            f'<strong>{html.escape(i18n.t("sources.rule_label"))}:</strong> {html.escape(rule)}'
            f"</div>"
            if rule
            else ""
        )
        st.markdown(
            f'<div class="dt-source-card">'
            f'<div class="dt-source-title">{html.escape(title)}</div>'
            f"{rule_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
        if text:
            with st.expander(i18n.t("sources.show_full_text"), expanded=False):
                st.write(text)
