# Yusor — SQLite Database (agentic_hr.db)

Built from your 11 CSV exports, extending the tables already defined in your
working-draft schema, plus a login/RBAC layer added afterward. 14 original tables,
3 seed/placeholder tables for Experience Gap Insight, and later additive tables for
payroll/attendance, grievances, Phase 4 (regulations, CVs), growth plans and
translation caching — 24 tables in total (see "Tables added later" below).
Existing columns are never changed; new features only add tables.
## Authentication & RBAC (added)

- **users** — separate table, linked 1:1 to `employees` via `employee_id`. Kept
  separate rather than adding columns to `employees` so the auth layer (password,
  active/inactive, last login) doesn't mix with pure HR profile data — this also
  covers the self-service case (an employee logging in just to check their own
  leave balance) without any extra tables.
  Columns: `user_id, employee_id (FK, unique), username, password_hash, role (FK→roles),
  is_active, created_at, last_login_at`.
- **roles** — lookup table: `employee`, `hr_specialist`, `hr_manager`, `admin`, each
  with a description. `users.role` FKs into it.

**Seed data (one account per employee — 500 today; the breakdown below is from the
original 80-employee seed):**
- `admin` (1) — placeholder pick, `EMP0037` (Engineering Manager) — arbitrary, not
  derived from any "admin" field in the data. Reassign to whoever should actually
  hold this before real use.
- `hr_manager` (11) — HR dept employee with job_title `HR Manager`, **or** any
  employee outside HR with `is_hr_approver = 1` (these are the department/team
  managers who approve their team's requests in `pending_approvals`).
- `hr_specialist` (4) — HR dept employees who aren't the HR Manager (People
  Partners, HR Specialist) — can view/update employee records, can't approve.
- `employee` (64) — everyone else, self-service only.
- **Passwords are placeholders** — every seeded account uses the same dev/test
  password (`ChangeMe123!`), stored as a plain SHA-256 hash. This is **not**
  production-grade (no salt, weak algorithm) — swap in bcrypt/argon2 with per-user
  salts before this touches anything real.

## From your original working draft (unchanged in role, columns adapted to the real data)

- **employees** — real columns differ from the draft (no username/password_hash/role/
  active — this data has no login fields). Instead it carries the actual HR fields your
  CSV has: national_id, department_id (FK), job_title, employment_status, hire_date,
  manager_id (self-FK), bank details, salary, is_hr_approver. If you still need
  login/auth fields for the MVP, they'd need to be added separately — this table is
  purely HR profile data as exported.
- **leave_balances** — one row per employee (snapshot as_of_date), not one row per
  leave type as the draft sketched. Matches your CSV's wide format
  (annual/sick/emergency entitlement, used, remaining).
  **Entitlement/remaining columns are backfilled placeholder demo data**
  (same convention as the `ChangeMe123!` password note above) —
  `*_used` came from the original seed, but `annual_entitlement`,
  `annual_remaining`, `sick_entitlement`, `sick_remaining`,
  `emergency_entitlement`, `emergency_used`, and `emergency_remaining`
  were all NULL until `python -m app.seed_leave_balances` filled them:
  annual = 21 days/year (30 after 5+ years of service, per LAW037/LAW038),
  sick = 30 days/year (per LAW060), emergency = 5 days/year flat (no
  statutory citation — a placeholder company benefit). Re-running that
  script after a reseed only fills gaps; it never overwrites a value
  already set.
- **leave_requests** — matches the draft closely: request_id, employee_id, type,
  dates, days, status, submitted/decided timestamps, decided_by.
- **proposed_actions** — matches the draft. `payload_json` stored as-is (TEXT); parse
  it at query time. `related_request_id` is polymorphic — see note below.
- **pending_approvals** — matches the draft, FK to proposed_actions and employees.
- **audit_log** — matches the draft (component→actor, event→event_type in your CSV
  naming).
- **tasks** — kept from the draft but created **empty**, since no tasks.csv exists yet.
  This is where your Orchestrator/Manager Agent would write task state
  (intent, current_state, retry_count, status) once the system is running.

## New tables (from the extra CSVs, not in the original draft)

- **departments** — small lookup table (12 rows today, `DEP-01` … `DEP-12`). `employees.department_id` now has a
  real FK into it instead of being a free-text field.
- **personal_info_update_requests** — low-risk field changes (mobile, address, etc.),
  separate from proposed_actions since the CSV already tracks these as their own
  request type with before/after values.
- **sensitive_change_requests** — high-risk changes (bank account, etc.), same idea —
  kept as its own table since it carries bank-specific columns proposed_actions
  doesn't have.
- **company_policies** and **saudi_labor_law** — these were meant to live as flat
  files for the RAG layer per your draft (`policies/*.txt`). Since you have them as
  structured CSVs already (with id, rule, conditions, exceptions columns), they're
  loaded as normal DB tables here for structured queries. If your RAG pipeline still
  wants plain text chunks, say so and I'll also export each row as a `.txt` file per
  policy/article — both can coexist (DB for lookups, text files for embedding).

## Experience Gap Insight tables (added, seed/placeholder data)

- **skills** — lookup table: `skill_id, skill_name, category`.
- **department_requirements** — which skills a department needs:
  `department_id (FK), skill_id (FK), minimum_headcount, is_critical`. Tied
  to the department rather than to a `job_title`, so a skill nobody was ever
  hired for (e.g. Cyber Security in IT) still shows up as a gap instead of
  having nothing to check against. `minimum_headcount` is hand-curated
  seed data, not a real staffing target, so the app no longer uses it to
  compute status — coverage is MISSING/OK purely from real
  `current_headcount` (0 vs. >0). The column is kept for schema history but
  is otherwise unread.
- **skill_job_titles** — which job titles plausibly carry a skill:
  `skill_id (FK), job_title`. A department's `current_headcount` for a skill
  is the count of its employees whose `job_title` appears here for that
  skill.

**This is synthetic seed data, not a real skills assessment** — same
convention as the `ChangeMe123!` password note above.
`department_requirements` was hand-curated (~5-8 skills per department,
including at least one deliberately uncovered skill so the `MISSING` case is
demoable) and `skill_job_titles` was hand-curated per skill (plausible
titles, not generated per employee). Populated by `app/seed_experience_gap.py`.
An earlier draft used a per-employee `employee_skills` table with a
`current_level` (1-5) score; that was replaced by `skill_job_titles` since a
fabricated per-employee score over synthetic data wasn't adding real signal.

## Tables added later (additive, no existing columns changed)

- **payroll_monthly** — one row per employee per month (`pay_period`), basic,
  allowances, overtime, gross, GOSI deduction, net pay. Read by the HR Agent
  and the Payroll page (PDF export).
- **attendance_leave_monthly** — one row per employee per month: working days,
  present/absent, leave days by type.
- **grievances** — `grievance_id, employee_id, identity_visible, complaint,
  consultant_recommendation, sources (JSON), status, submitted_at, decided_at,
  decided_by, hr_response`. Written by the Orchestrator after Manager PASS;
  `employee_id` is kept but hidden from reviewers when `identity_visible = 0`.
- **regulation_versions** (Phase 4) — every version of a labor-law article or
  policy text: `version_id, kind, ref_id, version_no, label, text, text_hash,
  status, source, source_url, fetched_at, change_key, proposal_id, created_at,
  decided_by, decided_at`. The first run stores the baseline; an approved
  `regulation_update` adds the new text before the row in `saudi_labor_law` /
  `company_policies` is updated. Rows are never deleted.
- **employee_cvs** (Phase 4) — `employee_id, filename, cv_text, uploaded_at,
  source`. Written only when a `new_hire` proposal is approved. Created on
  first use.
- **candidate_growth_plans** — `plan_id, employee_id, department_id, skill_id,
  cv_filename, cv_text (PII-masked), plan_text, created_at`. LLM growth plans
  from the Growth Opportunities page.
- **translation_cache** — `text_hash, target_lang, translated_text,
  created_at` (key: hash + language). Stores Arabic/English translations of
  stored free text so each text is translated once. Created on first use.

New `proposed_actions.action_type` values since the original draft:
`new_hire`, `termination`, `regulation_update` (all high risk, approval
required). A `regulation_update` has `employee_id` NULL (requester "system").

## `related_request_id` — not a hard foreign key

`proposed_actions.related_request_id` points to either
`personal_info_update_requests.request_id` (prefix `PI...`) or
`sensitive_change_requests.request_id` (prefix `SC...`), depending on
`action_type`. SQLite can't express an FK to "one of two tables," so it's left as
plain TEXT — resolve it in application code by checking the prefix, or with a
`LEFT JOIN` against both tables like the example below.

## Relationships (verified — zero foreign key violations)

```
departments
  └─ employees (department_id)
       ├─ employees (manager_id, self-referencing)
       ├─ users (employee_id, 1:1)  ── role → roles
       ├─ leave_balances
       ├─ leave_requests
       ├─ personal_info_update_requests
       ├─ sensitive_change_requests
       ├─ payroll_monthly
       ├─ attendance_leave_monthly
       ├─ grievances
       ├─ employee_cvs
       ├─ candidate_growth_plans ── skill_id → skills
       ├─ proposed_actions
       │    ├─ pending_approvals
       │    └─ regulation_versions (proposal_id)
       ├─ tasks
       └─ audit_log

saudi_labor_law / company_policies ── regulation_versions (kind + ref_id)
departments ── department_requirements ── skills ── skill_job_titles
```

## Row counts

Snapshot of the local `agentic_hr.db` on 2026-09-26. Transactional tables
(requests, approvals, audit, grievances) change as the app is used.

| table | rows |
|---|---|
| departments | 12 |
| employees | 500 |
| users | 500 (1 admin, 458 employee, 29 hr_specialist, 12 hr_manager — placeholder passwords, see above) |
| roles | 4 |
| leave_balances | 500 |
| leave_requests | 4 |
| payroll_monthly | 7,774 |
| attendance_leave_monthly | 7,774 |
| proposed_actions | 17 |
| pending_approvals | 17 |
| personal_info_update_requests | 0 |
| sensitive_change_requests | 3 |
| grievances | 7 |
| audit_log | 198 |
| company_policies | 27 |
| saudi_labor_law | 81 (70 original + 11 from `app/seed_labor_law_2025.py`) |
| regulation_versions | 245 (baseline article versions) |
| skills | 72 (seed/placeholder — see above) |
| department_requirements | 72 (seed/placeholder — see above) |
| skill_job_titles | 135 (seed/placeholder — see above) |
| candidate_growth_plans | 0 |
| employee_cvs, translation_cache | created on first use |
| tasks | 0 (empty, ready for the running system) |

**Current admin:** Sara Al Ghamdi (`EMP-0048`, Operations Manager). She replaces
the original `EMP0037` placeholder described above. `agentic_hr.db` is a local
file, so any copy made before this change has no admin until the role is set
(Users page, or `UPDATE users SET role = 'admin' WHERE employee_id = 'EMP-0048'`).

## Example queries

```sql
-- An employee's leave balance with department
SELECT e.full_name, d.department_name, lb.annual_remaining, lb.sick_remaining
FROM employees e
JOIN departments d ON d.department_id = e.department_id
JOIN leave_balances lb ON lb.employee_id = e.employee_id
WHERE e.employee_id = 'EMP-0001';

-- Resolve a proposed action's related request (polymorphic join)
SELECT pa.proposal_id, pa.action_type, pa.status,
       pi.field_name, pi.old_value, pi.new_value,
       sc.change_type, sc.old_iban, sc.new_iban
FROM proposed_actions pa
LEFT JOIN personal_info_update_requests pi ON pi.request_id = pa.related_request_id
LEFT JOIN sensitive_change_requests sc ON sc.request_id = pa.related_request_id
WHERE pa.proposal_id = 'PA00001';
```
