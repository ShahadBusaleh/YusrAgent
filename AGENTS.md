# Yusor — instructions for any AI

You are helping a **four-person student team**. You do **not** own the whole app.

1. Read `TEAM.md` before writing code.
2. Ask which role the human has, if they have not said it: **Consultant**, **HR**, **Manager**, or **Orchestrator**.
3. Stay inside that role’s files. If they ask for another member’s work, **refuse** and point them to `TEAM.md`.
4. RAG (`app/rag/`) is **already done**. Consultant **calls** it; nobody rebuilds it.
5. Do not change `agentic_hr.db` schema, other agents’ files, or commit `.env`.

If the human says “just finish the project,” still implement **only their one agent**.


**Phase 3 exception:** the agents are complete. If the human says they are the **UI Owner**, follow the "Phase 3: UI redesign" section in `TEAM.md` — you may edit `app/ui/**` only.