# Cursor Build Prompt — Yusor (HR Multi-Agent Assistant, Scaffolding Only)


---

## Context

I'm building **Yusor**, an HR Multi-Agent Assistant — a policy-grounded HR system with three
agents (HR/Execution Agent, Consultant/Policy Agent, Manager/Validation Agent)
orchestrated by an Orchestrator Agent, sitting behind a Security & Governance layer
with human-in-the-loop approval for high-risk actions.

Full project context (architecture, workflow, schema) is in `context/HR_Multi-Agent_Assistant_context.txt`
in this repo — read it before generating anything.

**Tech stack:** Streamlit (UI) · FastAPI (API layer) · Pydantic (validation) ·
SQLite (data — schema already built, see below) · Qdrant Cloud + qdrant-client +
sentence-transformers (RAG retrieval) · python-dotenv · requirements.txt

**Database:** `agentic_hr.db` already exists with this schema — use it as-is, do not
redesign it:
`departments, employees, leave_balances, leave_requests, tasks, proposed_actions,
pending_approvals, personal_info_update_requests, sensitive_change_requests,
audit_log, company_policies, saudi_labor_law, roles, users`

**Auth/RBAC (already in the DB, seeded with 80 test accounts):**
- `users`: `user_id, employee_id (FK, unique), username, password_hash, role, is_active, created_at, last_login_at`
- `roles`: `employee`, `hr_specialist`, `hr_manager`, `admin` (with descriptions in the table)
- All seeded accounts share one dev password (`ChangeMe123!`), hashed with plain
  SHA-256 — **not production-grade**, replace with bcrypt/argon2 + per-user salt
  before this is anything but local dev.

**RAG source documents:** `policy_texts/company_policies/*.txt` and
`policy_texts/saudi_labor_law/*.txt` — one chunk per file, ready to embed as-is.

**UI color palette (use these, don't invent a different palette):**
```
--dark-coffee:  #462e24
--grey-olive:   #847860
--pale-oak:     #e8d4bb
--onyx:         #111312
--powder-blue:  #a4bcd4
```
Treat `dark-coffee`/`onyx` as text/dark surfaces, `pale-oak` as light background,
`powder-blue` as the primary accent (buttons, links, active states), `grey-olive`
for secondary/muted text and borders.

---

## What I want you to build (scaffolding + infrastructure only)

1. **Project structure**
   ```
   /app
     /api            FastAPI app: routers, dependency injection, main.py
     /db             SQLite connection layer, models/schema access (matches agentic_hr.db)
     /security       Governance & Security Layer (see spec below)
     /rag             Qdrant client setup, embedding + retrieval functions
     /ui              Streamlit app, styled with the palette above
     /agents          Agent INTERFACES ONLY — see "Do not build" below
   /policy_texts      (already exists — don't touch)
   /context           (already exists — don't touch)
   requirements.txt
   .env.example
   ```

2. **FastAPI layer** (`/app/api`)
   - **Auth**: `POST /auth/login` (username + password → JWT access token, short
     expiry + a refresh token), `POST /auth/logout`, `GET /auth/me`. Password
     verification against `users.password_hash` — write it so swapping the hashing
     scheme later (bcrypt/argon2) only touches one function.
   - **RBAC middleware/dependency**: a `require_role(*roles)` FastAPI dependency
     that reads the JWT, loads the user's role, and 403s if it's not in the
     allowed set. Apply per-route, e.g.:
     - `employee`+ can read their **own** leave balance/requests only (filter by
       the authenticated user's `employee_id`, never trust an `employee_id` from
       the request body/query for read-your-own-data endpoints).
     - `hr_specialist`+ can read/update any employee's profile fields.
     - `hr_manager`+ can read/decide on `pending_approvals`.
     - `admin` only: manage `users`/`roles`.
   - Routers for: employees, leave (balance + requests), proposed actions,
     pending approvals, audit log — thin CRUD/read endpoints over `agentic_hr.db`,
     each gated by the role rules above.
   - Pydantic models for request/response validation matching the DB columns
     (never expose `password_hash` in any response model).
   - A single `/agent/query` endpoint (any authenticated role) that accepts a
     free-text HR request and currently just calls a stub (see below) — don't
     implement the actual routing/reasoning.

3. **DB layer** (`/app/db`)
   - Connection/session helper for the existing SQLite file.
   - Read/write functions per table needed by the routers above.
   - No schema migrations needed — the schema is final.

4. **RAG layer** (`/app/rag`)
   - Script to chunk-load `policy_texts/**/*.txt` into Qdrant (each file is already
     one chunk — just embed and upsert with metadata: id, source table, filename).
   - A `retrieve(query: str, top_k: int)` function using sentence-transformers +
     Qdrant hybrid/vector search. Return chunks with their source id so answers can
     cite `company_policies.id` / `saudi_labor_law.id`.
   - This is retrieval infrastructure only — not the Consultant Agent's reasoning
     about what the retrieved policy means.

5. **Security & Governance layer** (`/app/security`)
   - `classify_risk(action_type: str, payload: dict) -> Literal["low","medium","high"]`
     — a rule-based function (if/else per the workflow doc), not an LLM call.
   - `check_authorization(user, resource)`, `mask_pii(text)`, `validate_output(response, sources) -> bool`
     as separate testable functions, per the six checks in the architecture doc.
   - These are policy/rule functions the Manager Agent will call — implement the
     rules engine, not the agent that decides when to call it.

6. **Streamlit UI** (`/app/ui`)
   - **Login page** first: username + password against `/auth/login`, store the
     JWT in session state, redirect on success.
   - Show/hide pages based on the logged-in user's role:
     - `employee`: chat-style request input, own leave balance/requests.
     - `hr_specialist`: + employee record lookup/update.
     - `hr_manager`: + pending approvals queue (approve/reject).
     - `admin`: + user management, audit log viewer.
   - Style using the palette above — CSS variables or a Streamlit theme config,
     not default Streamlit styling.

7. **Agent interfaces only** (`/app/agents`) — see next section.

---

## Do NOT build: agent reasoning/orchestration logic

We are implementing the actual agents (Orchestrator, HR/Execution Agent,
Consultant/Policy Agent, Manager/Validation Agent) **by hand**, on purpose — that's
the core learning/design work of this project.

Instead, generate **empty interfaces/stubs only**, so the scaffolding compiles and
runs end-to-end with placeholder behavior, and we can drop our own logic in without
restructuring anything:

```python
# app/agents/base.py
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    @abstractmethod
    def run(self, input: dict) -> dict:
        """Implemented manually per agent. Do not fill in."""
        raise NotImplementedError
```

```python
# app/agents/orchestrator.py
class OrchestratorAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        raise NotImplementedError("Manual implementation pending")
```

Create matching empty stubs for `HRAgent`, `ConsultantAgent`, `ManagerAgent` with
the same pattern — class + `run()` signature + docstring describing its expected
inputs/outputs (per the architecture doc), but **no logic inside**, not even a
mock/simplified version. The `/agent/query` endpoint should call
`OrchestratorAgent().run(...)` and let the `NotImplementedError` surface — that's
expected and correct until we write it ourselves.

---

## Constraints

- Do add authentication/login and RBAC as specified above — `users`/`roles`
  tables already exist in the DB for this.
- Don't modify `agentic_hr.db`'s schema.
- Don't write agent prompts, system prompts, or LLM call logic anywhere — that's
  part of the manual agent work too.
- Keep functions small and testable; this is meant to be inspected and extended by
  hand, not treated as a finished app.
- Add a `requirements.txt` with pinned-ish versions for everything listed in the
  tech stack above.
