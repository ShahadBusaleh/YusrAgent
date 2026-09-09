# Yusor — HR Multi-Agent Assistant
Policy-grounded HR assistant: **HR Agent** (SQLite facts) + **Consultant Agent** (RAG over policies) + **Manager Agent** (PASS/FAIL), wired by an **Orchestrator**.

Team work, setup, and **rules for your AI** are in **[TEAM.md](TEAM.md)**. Any AI working in this repo must also follow **[AGENTS.md](AGENTS.md)**.

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env`: `QDRANT_URL`, `QDRANT_API_KEY`, `LLM_API_KEY` (and optional `LLM_BASE_URL` / `LLM_MODEL`). Never commit `.env`.

## RAG (already built — do not redo)

```powershell
python -m app.rag.ingest
python -m app.rag.retrieve "What is the minimum annual leave per year?"
python -m app.rag.generate "What is the minimum annual leave per year?"
```

Ingest once (or after policy files change). Expected retrieve hit for that question: `LAW037`.

## Run the app

```powershell
uvicorn app.api.main:app --reload
streamlit run app/ui/streamlit_app.py
```

`POST /agent/query` calls `OrchestratorAgent` (stub until Member 4 implements it).

## What not to do

- Do not redesign the database (`DATABASE_SCHEMA.md` is final).
- Do not implement a fifth “RAG agent.” RAG is infrastructure; Consultant uses it.
- Do not edit another member’s agent file. See `TEAM.md`.
