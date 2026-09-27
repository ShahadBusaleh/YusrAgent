# Yusor evaluation harness

Golden test cases for the four agents. Every case runs against a **fresh copy** of `agentic_hr.db`, so the real database is never modified and cases can't affect each other.

```bash
python -m eval.run_eval                  # everything (needs LLM + Qdrant from .env)
python -m eval.run_eval --offline        # HR + Manager only, no API calls, ~1 s
python -m eval.run_eval --owner hr       # just your cases
python -m eval.run_eval --id h-06 -v     # one case, with full output
```

Full results are saved to `eval/results/` (git-ignored).

## Who owns what

Each teammate edits **only their own case file**, the same split as TEAM.md:

| File | Owner | Runs |
|---|---|---|
| `cases/orchestrator.jsonl` | Orchestrator (Member 4) | full pipeline, `OrchestratorAgent.run` |
| `cases/hr.jsonl` | HR (Member 2) | `HRAgent.run` directly |
| `cases/consultant.jsonl` | Consultant (Member 1) | `ConsultantAgent.run` directly |
| `cases/manager.jsonl` | Manager (Member 3) | `ManagerAgent.run` with fixed inputs |

Manager cases feed hand-written `hr_result` / `consultant_result`, so Manager can be tested without waiting on HR or Consultant changes.

## Outcomes

| Outcome | Meaning | What to do |
|---|---|---|
| PASS | all expectations met | — |
| FAIL | an expectation broke | regression or new bug: fix it |
| KNOWN | failed, tagged with a review finding (`"known_issue": "H1"`) | expected until that finding is fixed |
| FIXED | tagged case now passes | **delete the `known_issue` tag** so it guards against regressions |
| ERROR | the agent raised an exception | fix it |

Exit code is 1 if anything is FAIL or ERROR.

## Writing a case

One JSON object per line. `//` lines are comments.

```json
{"id": "h-01", "target": "hr", "user": "EMP-0002", "query": "How many annual leave days do I have remaining?",
 "expect": {"facts_equals": {"annual_remaining": 17.0}}}
```

Case fields: `id`, `target` (`orchestrator|hr|consultant|manager`), `user` (employee id — role comes from the `users` table, override with `role`), `query`, `input` (extra agent input, e.g. `hr_result`), `employee_id`, `identity_visible`, `repeat`, `llm: true` (HR/Manager case that needs the LLM), `known_issue`, `notes`.

Expectations (all optional):

- **Status:** `status`, `intent`, `execution_order`, `decision`, `success`, `error_type`, `request_status`
- **Response text** (`response`, or `recommendation` for Consultant; case-insensitive): `response_nonempty`, `response_contains`, `response_contains_any`, `response_not_contains`
- **Sources:** `cites_any` (a retrieved source id), `citations_valid` (every `[Source: X]` in the text was actually retrieved)
- **HR facts** (dotted paths such as `payroll.net_pay_sar`): `facts_has`, `facts_not_has`, `facts_equals`, `facts_contains`, `facts_empty`
- **Proposed action:** `proposed_action_type` (use `null` for none), `payload_equals`, `payload_nonnull`
- **Manager:** `conflicts_empty`, `reasons_contain`, `reasons_unique`
- **Side effects:** `db_delta: [{"sql": "SELECT COUNT(*) FROM …", "delta": 1}]`

## Test data

EMP-0002 (Rana Al Harbi, employee): 17 annual days remaining, hired 26-05-2026, June 2026 net pay 5684.82 SAR.
EMP-0001 is an `hr_manager`.

## Other evaluation tools

| Command | What it does | Needs LLM / Qdrant |
|---|---|---|
| `python -m unittest discover -s eval -p "test_*.py" -v` | Offline unit tests (Arabic answers and translation, query expansion, regulations parser, staffing, growth API, supporting modules — see below). Model calls are mocked. | No |
| `python -m eval.build_ragas_dataset` | Runs the Consultant on `golden/consultant_rag.jsonl` and saves answers + retrieved contexts to `golden/consultant_rag_results.jsonl`. | Yes |
| `python -m eval.run_ragas` | Scores those results with Ragas: faithfulness, context precision, context recall, answer relevancy → `golden/ragas_results.jsonl`. | Yes |
| `python -m eval.run_llm_judge` | LLM-as-a-Judge over the latest `results/eval-*.json` (all four agents) → `results/llm-judge-*.json`. Uses the fuller `judge_output` saved by `run_eval.py`. Options: `--input`, `--model`, `--id`. | Yes |

`ragas` is not in `requirements.txt`; install it separately (`pip install ragas`) before running the Ragas scripts. The LLM-based tools use the shared provider quota (Groq: 8k tokens/minute, 200k/day). Prefer `--offline` and the unit tests for day-to-day checks.

### Supporting-module regression checks

These offline cases mock model responses and do not query or update the database.
They cover invalid leave dates, inclusive date-based duration, submission wording,
Arabic/Persian digits, lost/duplicated translation placeholders, altered numbers,
CV injection filtering, PII masking, refused/incomplete plans, and Arabic PDF output.

Payroll uses installed Arial on Windows or DejaVu Sans on Linux, with HarfBuzz
shaping. Otherwise set `PAYROLL_FONT_REGULAR` and `PAYROLL_FONT_BOLD` in the process
environment to Arabic-capable TrueType fonts. Do not commit proprietary font files.

CV filtering and pattern-based PII masking reduce exposure but do not detect every
possible instruction or unlabeled personal detail. Refused or malformed model
responses raise before the caller saves a plan. API cases verify that the same
employee-aware sanitized CV text is used for generation and persistence, failures
return actionable errors without saving, and instruction-only CVs are rejected.
Translation falls back to the
digit-normalised original if placeholders or numbers fail validation.

Live model quality and a visual review of Arabic PDF layout are separate checks.
