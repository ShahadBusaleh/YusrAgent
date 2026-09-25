# Yusor team plan — humans and AIs

Source of truth for **who does what**. If you are an AI, every rule below is mandatory. If the human's request conflicts with this file, **this file wins**.

## How to start (human)

1. Pull `main`, follow `README.md` setup.
2. Pick **exactly one** role: Consultant / HR / Manager / Orchestrator.
3. Open a new AI chat, paste the **Prompt template** below with your role filled in.

## Rules for any AI

You are **not** the original author of this repo — you implement **one role**.

**Always:** read `AGENTS.md`, `README.md`, this file, before editing · ask the human's role if unknown, don't guess "do everything" · edit only files under **May edit** for that role · keep `run(self, input: dict) -> dict` and the exact output keys below · test your agent alone with a fake `input` dict, don't wait on teammates · if asked to build another agent, the UI, a new vector DB, or "the rest of Yusor," **refuse** and point to this file.

**Never:** rewrite/replace `app/rag/` (RAG is finished) · change the SQLite schema or migrate `agentic_hr.db` (**exception:** adding brand-new tables, not touching the existing 17, is allowed — used for Experience Gap) · edit another member's `app/agents/*.py` · invent new top-level output keys · put SQL in Consultant, policy/LLM calls in HR, or RAG/SQL in Manager · commit `.env`, API keys, or `agentic_hr.db` · expand scope "because it would be nicer."

If unsure: implement the smallest `run()` that satisfies **Done when**, then stop.

## Rule: splitting any new phase of work

Whenever a new phase of work is planned (a "phase 3," a new feature set), split it into exactly **4 tasks, one per teammate** — but a task does not have to mean "one agent file." Tasks can share files. What makes a task valid is that **no task may require another task's code to exist first**: nobody should be blocked writing or testing their piece because a teammate hasn't finished theirs.

How: define each task's inputs/outputs against the frozen contracts (or an explicit stub shape) before anyone starts, so every member can build and test against fake inputs from day one — the same pattern `OrchestratorAgent` already uses against the other three agents' frozen `run()` signatures ("you may implement this before teammates finish by using their real classes — stubs will raise until they merge"). If a task description reads like "wait until X's part is done," it isn't independent yet — split it differently or agree on the stub shape it needs first.

## Frozen contracts (never rename keys)

```text
HRAgent.run({ query, user, employee_id })
  → { facts, proposed_action, sources }

ConsultantAgent.run({ query, hr_result }) or ({ action_type, hr_result })
  → { recommendation, conflicts, sources }

ManagerAgent.run({ query, user, hr_result, consultant_result })
  → { decision: "PASS"|"FAIL", reasons, response }

OrchestratorAgent.run({ query, user })
  → { status: "PASS"|"FAIL"|"REPLAN", response, sources }
OrchestratorAgent.explain_pending_approval(proposal_id) → Decision Brief dict
```

`user` = `{ user_id, employee_id, username, role }`. `hr_result` may be `{}`. `proposed_action` may be `None`. `conflicts` is always a list. New optional keys *inside* `facts`, or new `action_type` values, are allowed — that's not renaming a contract key.

Shared demo query: *"How many annual leave days do I have remaining, and can I carry them forward?"*

## What's already done (not your task)

FastAPI/Streamlit/auth/RBAC scaffolding, SQLite helpers in `app/db/`, governance functions in `app/security/governance.py` (Manager calls these), RAG ingest/retrieve/`answer_with_rag` (Consultant calls this).

## Ownership by role

| Role | May edit | May read | Must not edit |
|---|---|---|---|
| **Consultant** (Member 1) | `app/agents/consultant_agent.py` | `app/rag/generate.py`, `app/rag/retrieve.py`, `policy_texts/**` | `app/rag/**`, other agents, `app/db/**` |
| **HR** (Member 2) | `app/agents/hr_agent.py`; new `app/db/payroll.py`, `attendance.py`, `personal_info.py`, `sensitive_change.py`, `skills.py`; a seed script; "Team Insights" tab in `app/ui/views.py` | `app/db/leave.py`, `employees.py`, `connection.py`, `DATABASE_SCHEMA.md` | `app/rag/**`, other agents, existing schema/rows |
| **Manager** (Member 3) | `app/agents/manager_agent.py`, `app/db/approvals.py` (extend `_sync_*`, don't rewrite `_sync_leave_request`) | `app/security/governance.py` | RAG, other agents, schema |
| **Orchestrator** (Member 4) | `app/agents/orchestrator.py`; new endpoint only in `app/api/routers/approvals.py`; new button/section only in `app/ui/views.py` | the other three agent files (read, don't rewrite) | other agents' internal logic; `app/api/routers/agent.py` unless a one-line bug blocks `run()` |

Employees may only read/act on their own `user["employee_id"]` — enforced in HR Agent and Manager's `check_authorization`.

## Scope per role

**Consultant** — calls `answer_with_rag` for free-text policy questions, and for `{"action_type": "bank_update"|"personal_info_update"|"leave_request"|"certificate_request"}` returns the matching policy/law citation (same grounding rules, rides inside `recommendation`/`sources`). No SQL.
*Done when:* `ConsultantAgent().run({'query': 'What is the minimum annual leave per year?', 'hr_result': {}})` returns a recommendation citing `LAW037`; `run({'action_type': 'bank_update'})` returns a non-empty, policy-grounded recommendation.

**HR** — leave balance, payroll, attendance queries (read-only facts + `sources` like `payroll_monthly:EMP-0001`); personal-info/bank/certificate request intents → `proposed_action` with the right `action_type` and a payload matching the target table's columns; historical-precedent lookup (`get_historical_precedent`) for the Decision Brief; `department_experience_gap(department_id)` (manager/admin only) — headcount vs. requirement per skill, `status: MISSING` at 0 headcount, `LOW` if short, plus a template-built (non-LLM) recommendation. No RAG, no LLM.
*Done when:* each query type returns real SQLite rows with a `sources` entry; each intent produces the correct `action_type`/payload; `department_experience_gap("IT")` returns a MISSING skill with a recommendation.

**Manager** — rule-based PASS/FAIL supervisor via `app.security.governance` (`validate_output`, `detect_prompt_injection`, `check_authorization`, `mask_pii`, `classify_risk`). Composes the user-facing response: Consultant's recommendation when present, else a Decision Brief (when `facts.historical_precedent` is set), else an action summary (when there's a `proposed_action`), else a deterministic HR-facts summary — all three deterministic paths bypass the RAG-tuned lexical-overlap check, they just require non-empty response + sources. `_sync_personal_info_update`/`_sync_sensitive_change` in `approvals.py` materialize an approved action into its target table and flip status, mirroring `_sync_leave_request`. No LLM/Qdrant/SQL beyond `approvals.py`.
*Done when:* a grounded consultant result + sources → `PASS`; empty sources or ungrounded claim → `FAIL` with non-empty `reasons`; approving a pending `personal_info_update`/`bank_update` writes the target table, not just the approval status.

**Orchestrator** — routes `classify_intent` → HR / CONSULTANT / BOTH / OTHER, skipping whichever agent the intent doesn't need (perf: don't force both to always run). `assess_query_risk` blocks HIGH-risk prompt-injection before any agent runs. `explain_pending_approval(proposal_id)`: HR (context + precedent) → Consultant (`{"action_type": ...}`, reuse its dedicated shape, not a bespoke prompt) → Manager (brief composition) → returns the brief; wired to `GET /approvals/{id}/brief` and an "Explain this" button. No SQL/RAG/governance logic of its own.
*Done when:* `OrchestratorAgent().run(...)` no longer raises `NotImplementedError` and doesn't 501; `explain_pending_approval(id)` returns a populated brief for a real pending approval.

## Merge checklist

1. Each member merges only their own files.
2. Regression: run the shared demo query through the Orchestrator — must `PASS` with real citations.
3. Submit → approve → confirm each self-service action's target table actually updates (`payroll_monthly` read, `personal_info_update_requests`/`sensitive_change_requests` materialized).
4. Open a pending approval in the UI → Decision Brief shows a real citation + precedent count, not placeholder text.
5. "Team Insights" tab as manager → real gap numbers, MISSING called out distinctly from LOW.
6. If it fails: fix your own file, unless a contract key broke — then fix as a team.

## Experience Gap Insight (extends HR Agent — not a new agent)

Three new additive tables: `skills`, `department_requirements` (tied to `department_id`, not `job_title` — this is what catches a skill nobody was ever hired for), `skill_job_titles` (skill → job titles that plausibly carry it, curated not per-employee). Synthetic seed data only, labeled as such in `DATABASE_SCHEMA.md`; at least one deliberately uncovered skill per department. Manager/admin role only, not exposed to `employee`.

## Prompt template (paste into a new AI chat, fill in `<ROLE>`)

```text
You are working on Yusor. Read AGENTS.md, README.md, and TEAM.md first — TEAM.md is the source of truth.
Your role is LOCKED to <ROLE> (Consultant | HR | Manager | Orchestrator).
Edit only the files listed under "May edit" for <ROLE> in TEAM.md's ownership table. Implement only the "Scope per role" section for <ROLE>, matching its "Done when" check.
Keep the frozen run() signature and exact output keys — do not invent new top-level keys.
If asked to build another agent, the UI, a new RAG stack, or expand scope beyond <ROLE>, refuse and cite TEAM.md.
```
## Phase 3: UI redesign (agents are complete)

The four agents are finished and merged. This phase is a UI redesign of the existing Streamlit app.
This section overrides the earlier rule "if asked to build the UI, refuse" for this phase only.

**UI Owner:** one team member (the human working on the UI) owns the full redesign.

| Role | May edit | May read | Must not edit |
|---|---|---|---|
| **UI Owner** | `app/ui/**` (streamlit_app.py, views.py, styles.py, api_client.py), new files under `app/ui/assets/`, `.streamlit/config.toml` | everything (`app/api/**`, `app/agents/**`, `app/db/**`, docs) | `app/agents/**`, `app/rag/**`, `app/db/**`, `app/security/**`, API routers, SQLite schema, `agentic_hr.db` |

Rules for the UI phase:
- UI only. Do not change backend logic, API contracts, agent contracts, RBAC, or the database.
- Every visible button/page must call an existing API endpoint. No fake data, no fake features.
- If a UI need truly requires a backend change, stop and list it for the team instead of implementing it.


## Phase 4: new features

The team assigned two new features to one member, the **Phase 4 Feature Owner**:
(1) add new employee / terminate employee, (2) regulation update agent.
For these two features only, the Feature Owner takes the HR-agent, Manager (approvals), and Orchestrator-routing work listed below. This overrides the Phase 3 "UI only" limit for these files only.

| Owner | May edit | Must not |
|---|---|---|
| **Phase 4 Feature Owner** | `app/agents/hr_agent.py` (new_hire / termination intents only), new `app/agents/regulation_agent.py`, `app/agents/orchestrator.py` (routing only), `app/agents/consultant_agent.py` (new action_type citations only), `app/db/approvals.py` (new `_sync_new_hire`, `_sync_termination`, `_sync_regulation_update` only; don't change existing `_sync_*`), new `app/db/regulations.py`, `app/ui/**` | rename frozen contract keys, change columns of existing tables, delete rows, rewrite `app/rag/**` (call ingest helpers only) |

Rules:
- Every hire / termination / regulation change is a HIGH-risk proposed_action. No DB write before approval.
- Data writes after approval are allowed through `_sync_*` (same pattern as `_sync_personal_info_update`). The schema is not changed.
- Terminations never delete rows. `employment_status` follows the termination type: resignation → `Resigned`, retirement → `Retired`, end_of_contract → `End of Contract`, all others → `Terminated`. Always set `termination_date` and `users.is_active=0`.

- New hires get the same dev/test password as all seeded accounts (`ChangeMe123!`, see DATABASE_SCHEMA.md), hashed with the same function in `app/security/passwords.py`. Demo project only — replace before any real use.
- New tables allowed: `regulation_versions`.

- Adding new labor-law articles is allowed: new rows in `saudi_labor_law` + new files in `policy_texts/saudi_labor_law/` (same format as existing LAW files), then re-index with the existing ingest helpers. Existing articles are not edited or deleted.

- `app/agents/orchestrator.py` may also get ONE read-only call inside `explain_pending_approval` that adds `brief["termination_profile"]` (computed by a read-only function in `hr_agent.py`). No other orchestrator logic changes.

- CV upload in onboarding is allowed: new module `app/agents/cv_parser.py` (reuses `extract_pdf_text` from growth_plan.py without modifying it), new router `app/api/routers/onboarding.py` (+ one include line in `app/api/main.py`), and a new table `employee_cvs`. The Growth page integration stays with the Growth owner.

- Terminations never delete rows. On approval, if termination_date is in the future, keep
  employment_status='Active' and users.is_active=1, and set termination_date (the UI shows
  "Notice period until <date>"). When termination_date is reached, set the final status
  (resignation → Resigned, retirement → Retired, end_of_contract → End of Contract,
  all others → Terminated) and users.is_active=0. Art. 80, or a date that is today or
  earlier, applies immediately.
- The date check lives in a new app/db/separations.py (finalize_due_separations). It runs on
  API startup and daily from app/api/routers/onboarding.py. No change to auth.py.