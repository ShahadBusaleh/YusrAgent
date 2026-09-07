# Yusor — SQLite Database (agentic_hr.db)

Built from your 11 CSV exports, extending the tables already defined in your
working-draft schema, plus a login/RBAC layer added afterward. 14 tables total.

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

**Seed data (80 accounts, one per employee):**
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

- **departments** — small lookup table (8 rows). `employees.department_id` now has a
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
       ├─ proposed_actions
       │    └─ pending_approvals
       ├─ tasks
       └─ audit_log
```

## Row counts

| table | rows |
|---|---|
| departments | 8 |
| employees | 80 |
| leave_balances | 80 |
| leave_requests | 125 |
| proposed_actions | 20 |
| pending_approvals | 12 |
| personal_info_update_requests | 18 |
| sensitive_change_requests | 12 |
| audit_log | 20 |
| company_policies | 27 |
| saudi_labor_law | 70 |
| tasks | 0 (empty, ready for the running system) |
| roles | 4 |
| users | 80 (seeded, placeholder passwords — see above) |

## Example queries

```sql
-- An employee's leave balance with department
SELECT e.full_name, d.department_name, lb.annual_remaining, lb.sick_remaining
FROM employees e
JOIN departments d ON d.department_id = e.department_id
JOIN leave_balances lb ON lb.employee_id = e.employee_id
WHERE e.employee_id = 'EMP0001';

-- Resolve a proposed action's related request (polymorphic join)
SELECT pa.proposal_id, pa.action_type, pa.status,
       pi.field_name, pi.old_value, pi.new_value,
       sc.change_type, sc.old_iban, sc.new_iban
FROM proposed_actions pa
LEFT JOIN personal_info_update_requests pi ON pi.request_id = pa.related_request_id
LEFT JOIN sensitive_change_requests sc ON sc.request_id = pa.related_request_id
WHERE pa.proposal_id = 'PA00001';
```
