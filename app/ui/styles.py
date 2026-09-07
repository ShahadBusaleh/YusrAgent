CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Source+Sans+3:wght@400;600;700&display=swap');

:root {
  --dark-coffee: #462e24;
  --grey-olive: #847860;
  --pale-oak: #e8d4bb;
  --onyx: #111312;
  --powder-blue: #a4bcd4;
  --powder-vivid: #7eb4e0;
  --ink: #f4f7fb;
  --muted: #c5d0d8;
}

html, body, [class*="css"] {
  font-family: "Source Sans 3", sans-serif;
}

.stApp {
  background:
    radial-gradient(1200px 520px at 12% -10%, rgba(126, 180, 224, 0.28), transparent 55%),
    radial-gradient(900px 480px at 100% 0%, rgba(70, 46, 36, 0.85), transparent 50%),
    linear-gradient(165deg, #161a1c 0%, var(--onyx) 45%, #1c1410 100%);
  color: var(--ink);
}

[data-testid="stHeader"] {
  background: transparent !important;
}
[data-testid="stToolbar"] {
  background: transparent !important;
}

.block-container {
  padding-top: 1.6rem;
  max-width: 1180px;
}

h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
  font-family: "Outfit", sans-serif !important;
  color: var(--ink) !important;
  letter-spacing: -0.03em;
  font-weight: 700 !important;
}

.stCaption, [data-testid="stCaptionContainer"] {
  color: var(--muted) !important;
}

section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #2b1c16 0%, var(--dark-coffee) 40%, #1a100c 100%) !important;
  border-right: 1px solid rgba(164, 188, 212, 0.18);
}
section[data-testid="stSidebar"] .block-container {
  padding-top: 1.4rem;
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] div {
  color: var(--ink) !important;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] label {
  background: rgba(17, 19, 18, 0.28);
  border: 1px solid rgba(164, 188, 212, 0.12);
  border-radius: 10px;
  padding: 0.45rem 0.7rem;
  margin-bottom: 0.35rem;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
  border-color: var(--powder-vivid);
  background: rgba(126, 180, 224, 0.12);
}
section[data-testid="stSidebar"] .stButton > button {
  background: var(--powder-vivid) !important;
  color: var(--onyx) !important;
  border: none !important;
  width: 100%;
}

.stButton > button,
.stFormSubmitButton > button {
  background: linear-gradient(180deg, var(--powder-vivid), #6aa3d0) !important;
  color: var(--onyx) !important;
  border: 0 !important;
  border-radius: 12px !important;
  font-family: "Outfit", sans-serif !important;
  font-weight: 700 !important;
  letter-spacing: 0.02em;
  padding: 0.55rem 1.15rem !important;
  box-shadow: 0 8px 24px rgba(126, 180, 224, 0.28);
}
.stButton > button:hover,
.stFormSubmitButton > button:hover {
  background: linear-gradient(180deg, #93c4ea, var(--powder-vivid)) !important;
  transform: translateY(-1px);
}

[data-testid="stForm"] {
  background: rgba(12, 16, 18, 0.72);
  border: 1px solid rgba(126, 180, 224, 0.28);
  border-radius: 22px;
  padding: 1.15rem 1.1rem 1rem;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.28);
}

[data-testid="stTextInputRootElement"],
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInputContainer"],
[data-testid="stSelectbox"] > div {
  background: #1b242c !important;
  border: 1px solid rgba(126, 180, 224, 0.38) !important;
  border-radius: 12px !important;
}
div[data-testid="stTextInput"] button,
div[data-testid="stTextInput"] [data-testid="stBaseButton-secondary"] {
  background: #1b242c !important;
  color: var(--powder-vivid) !important;
  border: 1px solid rgba(126, 180, 224, 0.38) !important;
  box-shadow: none !important;
}
div[data-testid="stTextArea"] label,
div[data-testid="stSelectbox"] label,
div[data-testid="stDateInput"] label,
div[data-testid="stNumberInput"] label {
  color: var(--muted) !important;
  font-weight: 600 !important;
}

[data-testid="stDataFrame"] {
  border: 1px solid rgba(164, 188, 212, 0.2);
  border-radius: 14px;
  overflow: hidden;
}

.yusor-hero {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  padding: 1.6rem 1.7rem 1.4rem;
  margin-bottom: 1.4rem;
  border-radius: 22px;
  background:
    linear-gradient(135deg, rgba(126, 180, 224, 0.22), rgba(70, 46, 36, 0.35)),
    rgba(17, 19, 18, 0.55);
  border: 1px solid rgba(164, 188, 212, 0.28);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
}
.yusor-kicker {
  color: var(--powder-vivid);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.72rem;
  font-weight: 700;
  font-family: "Outfit", sans-serif;
}
.yusor-hero h1 {
  margin: 0 !important;
  font-size: 2.4rem !important;
}
.yusor-hero p {
  margin: 0;
  color: var(--muted);
  max-width: 46rem;
  font-size: 1.02rem;
}

.yusor-brand {
  font-family: "Outfit", sans-serif;
  font-size: 2rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--powder-vivid);
  margin-bottom: 0.15rem;
}
.yusor-brand-sub {
  color: var(--muted) !important;
  font-size: 0.85rem;
  letter-spacing: 0.04em;
  margin-bottom: 0.8rem;
}
.yusor-role {
  display: inline-block;
  background: var(--powder-vivid);
  color: var(--onyx);
  border-radius: 999px;
  padding: 0.2rem 0.8rem;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: "Outfit", sans-serif;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.stat-card {
  background: linear-gradient(180deg, rgba(70, 46, 36, 0.92), rgba(17, 19, 18, 0.92));
  border: 1px solid rgba(164, 188, 212, 0.22);
  border-radius: 18px;
  padding: 1.15rem 1.2rem 1rem;
  min-height: 132px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.22);
  position: relative;
  overflow: hidden;
}
.stat-card:after {
  content: "";
  position: absolute;
  inset: auto -20px -40px auto;
  width: 110px;
  height: 110px;
  border-radius: 50%;
  background: rgba(126, 180, 224, 0.18);
}
.stat-card--olive:after { background: rgba(132, 120, 96, 0.28); }
.stat-card--coffee:after { background: rgba(70, 46, 36, 0.0); border: 0; }
.stat-label {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 0.72rem;
  font-weight: 700;
  font-family: "Outfit", sans-serif;
}
.stat-value {
  font-family: "Outfit", sans-serif;
  font-size: 2.6rem;
  font-weight: 800;
  color: var(--powder-vivid);
  line-height: 1.1;
  margin-top: 0.45rem;
}
.stat-card--olive .stat-value { color: #d7c6a4; }
.stat-card--coffee .stat-value { color: #f0d7b8; }
.stat-sub {
  color: var(--muted);
  font-size: 0.85rem;
  margin-top: 0.15rem;
}

.login-panel {
  background: rgba(17, 19, 18, 0.62);
  border: 1px solid rgba(164, 188, 212, 0.28);
  border-radius: 24px;
  padding: 1.6rem 1.5rem 1.2rem;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}
.login-mark {
  font-family: "Outfit", sans-serif;
  font-weight: 800;
  font-size: 2.6rem;
  letter-spacing: 0.14em;
  color: var(--powder-vivid);
  line-height: 0.95;
}
.login-tag {
  color: var(--ink);
  font-size: 1.35rem;
  font-family: "Outfit", sans-serif;
  font-weight: 600;
  margin: 0.7rem 0 0.4rem;
}
.login-copy {
  color: var(--muted);
  font-size: 1.02rem;
  max-width: 26rem;
}
</style>
"""


def inject() -> None:
    import streamlit as st

    st.markdown(CSS, unsafe_allow_html=True)


def hero(kicker: str, title: str, subtitle: str) -> None:
    import streamlit as st

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
