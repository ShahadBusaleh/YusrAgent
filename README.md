# Yusor — HR Multi-Agent Assistant
Bilingual (Arabic / English), policy-grounded HR assistant. An **Orchestrator** routes each request to the **HR Agent** (SQLite facts and proposed actions) and/or the **Consultant Agent** (RAG over Saudi Labor Law, company policies and WPS documents); the **Manager Agent** validates the result (PASS/FAIL) before it reaches the user. Risky actions wait for human approval.

- Architecture (components, data flow, diagram): **[ARCHITECTURE.md](ARCHITECTURE.md)**
- Running it locally: **[RUNNING.md](RUNNING.md)**
- Database: **[DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)**
- Evaluation: **[eval/README.md](eval/README.md)**
- Team rules and frozen agent contracts: **[TEAM.md](TEAM.md)**, **[AGENTS.md](AGENTS.md)**

## Features

- **Ask Yusor** chat in Arabic or English: leave balance, payroll, attendance, profile, policy / labor-law questions with `[Source: ID]` citations.
- **Self-service requests** through chat: leave, personal-info update, bank/IBAN change, certificate. Each becomes a proposed action for approval.
- **Approvals** with an AI **Decision Brief** (employee context, historical precedent, policy citation, termination profile).
- **Grievances** (anonymous or named), reviewed by HR.
- **Onboarding & Offboarding**: new hire (with CV auto-fill) and termination (notice period, final settlement per Labor Law Art. 74–88).
- **Regulation Update Agent**: watches laws.boe.gov.sa / hrsd.gov.sa, proposes text updates, four-eyes approval, re-indexes RAG.
- **Team Insights** (experience gap per department) and **Growth Opportunities** (CV-based growth plans).
- **Records** archive, **Payroll** PDFs, Excel/PDF exports, **Users** and **Audit log** for admins.

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env`: `QDRANT_URL`, `QDRANT_API_KEY`, and `LLM_API_KEY` (or `LLM_API_KEYS="k1,k2"` to rotate several keys), plus `LLM_BASE_URL` / `LLM_MODEL` from the **same** provider. Never commit `.env`.

## RAG (already built — do not redo)

```powershell
python -m app.rag.ingest
python -m app.rag.retrieve "What is the minimum annual leave per year?"
python -m app.rag.generate "What is the minimum annual leave per year?"
```

Ingest once, or after files under `policy_texts/` change. Expected retrieve hit for that question: `LAW037`. Retrieval is hybrid: query expansion → Qdrant vectors + BM25 keywords → Reciprocal Rank Fusion.

## Run the app

```powershell
uvicorn app.api.main:app --reload
streamlit run app/ui/streamlit_app.py
```

`POST /agent/query` calls `OrchestratorAgent.run`. Details and troubleshooting in [RUNNING.md](RUNNING.md).

## What not to do

- Do not change columns of existing tables. New tables only (see `DATABASE_SCHEMA.md`).
- Do not implement a separate "RAG agent". RAG is infrastructure; the Consultant uses it.
- Do not rename the frozen agent contract keys in `TEAM.md`.
