"""Yusor evaluation harness (Phase 0).

Runs the golden cases in eval/cases/*.jsonl against the real agents. Every
case gets its own fresh copy of agentic_hr.db, so nothing here ever writes
to the real database and cases can't affect each other.

Usage (from the repo root):
    python -m eval.run_eval                 # all cases (needs LLM + Qdrant)
    python -m eval.run_eval --offline       # only cases that need no LLM/Qdrant
    python -m eval.run_eval --owner hr      # one teammate's cases
    python -m eval.run_eval --id h-06 -v    # one case, print full output

Outcomes:
    PASS   all expectations met
    FAIL   an expectation failed (regression / new bug)
    KNOWN  failed, but the case is tagged with a known finding (e.g. "H1")
    FIXED  passed although tagged as a known finding: remove the tag
    ERROR  the agent raised an exception
Exit code is 1 when any case is FAIL or ERROR.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = Path(__file__).resolve().parent / "cases"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
SOURCE_DB = ROOT / "agentic_hr.db"

sys.path.insert(0, str(ROOT))

# Background translation prefetch (app.agents.translation) would outlive
# each case's temporary DB copy and add LLM calls to every run.
os.environ.setdefault("YUSOR_PREFETCH_TRANSLATIONS", "0")

OFFLINE_TARGETS = {"hr", "manager"}
_CITATION_RE = re.compile(r"\[Source:\s*([^\]]+)\]", re.I)
_POLICY_ID_RE = re.compile(r"\b(?:LAW\d{3}|AAM-POL-\d{3}|WPS\d{3})\b")


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name}:{line_no}: invalid JSON ({exc})")
            case.setdefault("owner", path.stem)
            case["_file"] = path.name
            cases.append(case)
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"Duplicate case ids: {sorted(dupes)}")
    return cases


def needs_online(case: dict) -> bool:
    return case["target"] not in OFFLINE_TARGETS or bool(case.get("llm"))


def build_user(case: dict) -> dict:
    """`"user": "EMP-0002"` -> real user row from the DB; `role` may override."""
    eid = case.get("user")
    if not eid:
        return {}
    conn = sqlite3.connect(SOURCE_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT user_id, employee_id, username, role FROM users WHERE employee_id = ?",
        (eid,),
    ).fetchone()
    conn.close()
    user = dict(row) if row else {"user_id": eid, "employee_id": eid, "username": eid, "role": "employee"}
    if case.get("role"):
        user["role"] = case["role"]
    return user


# ---------------------------------------------------------------------------
# Running a case
# ---------------------------------------------------------------------------

def _db_count(db_path: Path, sql: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _call_agent(case: dict, user: dict) -> dict:
    target = case["target"]
    query = case.get("query", "")
    extra = case.get("input") or {}

    if target == "orchestrator":
        from app.agents.orchestrator import OrchestratorAgent
        payload = {"query": query, "user": user}
        if "identity_visible" in case:
            payload["identity_visible"] = case["identity_visible"]
        return OrchestratorAgent().run(payload)

    if target == "hr":
        from app.agents.hr_agent import HRAgent
        payload = {"query": query, "user": user, "employee_id": case.get("employee_id") or user.get("employee_id")}
        payload.update(extra)
        return HRAgent().run(payload)

    if target == "consultant":
        from app.agents.consultant_agent import ConsultantAgent
        payload = dict(extra) if extra else {"query": query}
        return ConsultantAgent().run(payload)

    if target == "manager":
        from app.agents.manager_agent import ManagerAgent
        payload = {"query": query, "user": user}
        payload.update(extra)
        return ManagerAgent().run(payload)

    raise ValueError(f"Unknown target {target!r}")


def run_case(case: dict, workdir: Path) -> dict:
    db_path = workdir / f"{case['id']}.db"
    shutil.copyfile(SOURCE_DB, db_path)
    # get_settings() reads SQLITE_PATH on every call, so this redirects all
    # agent DB access (and _submit_for_approval writes) to the copy.
    os.environ["SQLITE_PATH"] = str(db_path)

    expect = case.get("expect") or {}
    deltas = expect.get("db_delta") or []
    before = [_db_count(db_path, d["sql"]) for d in deltas]
    user = build_user(case)

    output: dict = {}
    error = None
    captured = io.StringIO()
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(captured):
            for _ in range(int(case.get("repeat", 1))):
                output = _call_agent(case, user)
    except Exception as exc:  # noqa: BLE001 - report every agent crash as ERROR
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - start) * 1000

    after = [_db_count(db_path, d["sql"]) for d in deltas] if not error else before
    failures = [] if error else check(case, output, before, after)

    known = case.get("known_issue")
    if error:
        outcome = "ERROR"
    elif failures:
        outcome = "KNOWN" if known else "FAIL"
    else:
        outcome = "FIXED" if known else "PASS"

    return {
        "id": case["id"],
        "owner": case["owner"],
        "target": case["target"],
        "query": case.get("query") or json.dumps(case.get("input", {}))[:80],
        "outcome": outcome,
        "known_issue": known,
        "failures": failures,
        "error": error,
        "latency_ms": round(latency_ms, 1),
        "output": _summarize(case["target"], output),
        # Fuller view for eval/run_llm_judge.py: the summary above drops
        # fact values and cuts the response at 600 chars.
        "judge_output": _judge_view(output),
    }


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------

def _get(obj, dotted: str):
    """Dotted lookup; returns (found, value)."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _response_text(target: str, out: dict) -> str:
    if target == "consultant":
        return str(out.get("recommendation") or "")
    return str(out.get("response") or "")


def _source_ids(out: dict) -> list[str]:
    ids = []
    for src in out.get("sources") or []:
        ids.append(str(src.get("id")) if isinstance(src, dict) else str(src))
    return ids


def check(case: dict, out: dict, before: list[int], after: list[int]) -> list[str]:
    exp = case.get("expect") or {}
    target = case["target"]
    fails: list[str] = []
    text = _response_text(target, out)
    low = text.lower()
    sources = _source_ids(out)
    facts = out.get("facts") or {}
    action = out.get("proposed_action")

    simple = {
        "status": out.get("status"),
        "intent": out.get("intent"),
        "execution_order": out.get("execution_order"),
        "decision": out.get("decision"),
        "success": out.get("success"),
        "error_type": (out.get("error") or {}).get("type") if isinstance(out.get("error"), dict) else None,
        "request_status": (facts.get("request_assessment") or {}).get("status"),
    }
    for key, actual in simple.items():
        if key in exp and exp[key] != actual:
            fails.append(f"{key}: expected {exp[key]!r}, got {actual!r}")

    if exp.get("response_nonempty") and not text.strip():
        fails.append("response is empty")
    for s in exp.get("response_contains", []):
        if s.lower() not in low:
            fails.append(f"response missing {s!r}")
    if exp.get("response_contains_any") and not any(s.lower() in low for s in exp["response_contains_any"]):
        fails.append(f"response has none of {exp['response_contains_any']}")
    for s in exp.get("response_not_contains", []):
        if s.lower() in low:
            fails.append(f"response should not contain {s!r}")

    if exp.get("cites_any") and not set(exp["cites_any"]) & set(sources):
        fails.append(f"sources {sources} contain none of {exp['cites_any']}")
    if exp.get("citations_valid"):
        # A citation is either a [Source: X] tag or a bare policy id the
        # answer mentions ("Law ID: LAW038"); every one must be retrieved.
        cited = {c.strip() for c in _CITATION_RE.findall(text)} | set(_POLICY_ID_RE.findall(text))
        if not cited:
            fails.append("response cites no policy id")
        elif not cited <= set(sources):
            fails.append(f"cites unretrieved sources {sorted(cited - set(sources))}")

    if "conflicts_empty" in exp and exp["conflicts_empty"] != (not out.get("conflicts")):
        fails.append(f"conflicts: {out.get('conflicts')}")

    if exp.get("facts_empty") and facts:
        fails.append(f"facts should be empty, got keys {sorted(facts)}")
    for path in exp.get("facts_has", []):
        if not _get(facts, path)[0]:
            fails.append(f"facts missing {path}")
    for path in exp.get("facts_not_has", []):
        if _get(facts, path)[0]:
            fails.append(f"facts should not have {path}")
    for path, value in (exp.get("facts_equals") or {}).items():
        found, actual = _get(facts, path)
        if not found or actual != value:
            fails.append(f"facts.{path}: expected {value!r}, got {actual!r}")
    for path, sub in (exp.get("facts_contains") or {}).items():
        found, actual = _get(facts, path)
        if not found or sub.lower() not in str(actual).lower():
            fails.append(f"facts.{path} should contain {sub!r}, got {actual!r}")

    if "proposed_action_type" in exp:
        actual = action.get("action_type") if isinstance(action, dict) else None
        if actual != exp["proposed_action_type"]:
            fails.append(f"proposed_action: expected {exp['proposed_action_type']!r}, got {actual!r}")
    payload = (action or {}).get("payload") or {}
    for key, value in (exp.get("payload_equals") or {}).items():
        if payload.get(key) != value:
            fails.append(f"payload.{key}: expected {value!r}, got {payload.get(key)!r}")
    for key in exp.get("payload_nonnull", []):
        if payload.get(key) in (None, ""):
            fails.append(f"payload.{key} is empty")

    reasons = [str(r) for r in out.get("reasons") or []]
    for s in exp.get("reasons_contain", []):
        if not any(s.lower() in r.lower() for r in reasons):
            fails.append(f"reasons {reasons} missing {s!r}")
    if exp.get("reasons_unique") and len(reasons) != len(set(reasons)):
        fails.append(f"duplicate reasons {reasons}")

    for spec, b, a in zip(exp.get("db_delta") or [], before, after):
        if a - b != spec["delta"]:
            fails.append(f"db delta {a - b} (expected {spec['delta']}): {spec['sql']}")

    return fails


def _summarize(target: str, out: dict) -> dict:
    keep = {k: out.get(k) for k in ("status", "intent", "execution_order", "decision", "reasons", "success") if k in out}
    text = _response_text(target, out)
    if text:
        keep["response"] = text[:600]
    if out.get("sources"):
        keep["source_ids"] = _source_ids(out)
    if target == "hr":
        keep["fact_keys"] = sorted((out.get("facts") or {}).keys())
        keep["request_assessment"] = (out.get("facts") or {}).get("request_assessment")
        keep["proposed_action"] = out.get("proposed_action")
    if out.get("conflicts"):
        keep["conflicts"] = out["conflicts"]
    if isinstance(out.get("error"), dict):
        keep["error"] = out["error"]
    return keep


def _clip(value, limit: int = 1500):
    """Bound strings and lists so one case can't blow the judge's context."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + " …[cut]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        clipped = [_clip(v, limit) for v in value[:10]]
        return clipped + [f"…[{len(value) - 10} more]"] if len(value) > 10 else clipped
    return value


def _judge_view(out: dict) -> dict:
    view = {k: v for k, v in out.items() if k not in ("sources", "response", "recommendation")}
    for key in ("response", "recommendation"):
        if out.get(key):
            view[key] = _clip(out[key], 4000)
    if out.get("sources"):
        view["sources"] = [
            {"id": s.get("id"), "text": str(s.get("text") or "")[:300]} if isinstance(s, dict) else s
            for s in out["sources"]
        ]
    return _clip(view)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def report(results: list[dict], cases_by_id: dict, verbose: bool) -> None:
    print(f"\n{'ID':<7} {'OWNER':<13} {'OUTCOME':<7} {'MS':>7}  DETAIL")
    print("-" * 100)
    for r in results:
        detail = r["error"] or (r["failures"][0] if r["failures"] else "")
        if r["known_issue"]:
            detail = f"[{r['known_issue']}] {detail}".strip()
        print(f"{r['id']:<7} {r['owner']:<13} {r['outcome']:<7} {r['latency_ms']:>7.0f}  {detail[:70]}")
        if verbose:
            for f in r["failures"][1:]:
                print(f"{'':<37}{f[:90]}")
            print(json.dumps(r["output"], ensure_ascii=False, indent=2))

    print("\nBy owner")
    owners = sorted({r["owner"] for r in results})
    for owner in owners:
        rs = [r for r in results if r["owner"] == owner]
        count = {k: sum(r["outcome"] == k for r in rs) for k in ("PASS", "FAIL", "KNOWN", "FIXED", "ERROR")}
        print(f"  {owner:<13} " + "  ".join(f"{k} {v}" for k, v in count.items()) + f"   ({len(rs)} cases)")

    intent_cases = [r for r in results if "intent" in (cases_by_id[r["id"]].get("expect") or {}) and r["outcome"] != "ERROR"]
    if intent_cases:
        hits = sum(r["output"].get("intent") == cases_by_id[r["id"]]["expect"]["intent"] for r in intent_cases)
        print(f"\nIntent accuracy:     {hits}/{len(intent_cases)} ({100 * hits / len(intent_cases):.0f}%)")

    cite_cases = [r for r in results if (cases_by_id[r["id"]].get("expect") or {}).get("cites_any") and r["outcome"] != "ERROR"]
    if cite_cases:
        hits = sum(bool(set(cases_by_id[r["id"]]["expect"]["cites_any"]) & set(r["output"].get("source_ids") or [])) for r in cite_cases)
        print(f"Retrieval hit-rate:  {hits}/{len(cite_cases)} ({100 * hits / len(cite_cases):.0f}%)")

    print("\nLatency (ms)")
    for target in sorted({r["target"] for r in results}):
        lat = [r["latency_ms"] for r in results if r["target"] == target and r["outcome"] != "ERROR"]
        if lat:
            print(f"  {target:<13} p50 {statistics.median(lat):>7.0f}   p95 {_pct(lat, 0.95):>7.0f}   (n={len(lat)})")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run the Yusor golden evaluation set.")
    parser.add_argument("--owner", help="only this owner's cases (orchestrator|hr|consultant|manager)")
    parser.add_argument("--target", help="only cases for this agent")
    parser.add_argument("--id", action="append", help="only these case ids (repeatable)")
    parser.add_argument("--offline", action="store_true", help="skip cases that need the LLM or Qdrant")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every failure and the agent output")
    args = parser.parse_args()

    cases = load_cases()
    if args.owner:
        cases = [c for c in cases if c["owner"] == args.owner]
    if args.target:
        cases = [c for c in cases if c["target"] == args.target]
    if args.id:
        cases = [c for c in cases if c["id"] in set(args.id)]
    if args.offline:
        cases = [c for c in cases if not needs_online(c)]
    if not cases:
        print("No cases selected.")
        return 1

    print(f"Running {len(cases)} case(s) against copies of {SOURCE_DB.name}…")
    results = []
    with tempfile.TemporaryDirectory(prefix="yusor-eval-") as tmp:
        for case in cases:
            results.append(run_case(case, Path(tmp)))
            print(".", end="", flush=True)
    print()

    report(results, {c["id"]: c for c in cases}, args.verbose)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull results: {out_path.relative_to(ROOT)}")

    return 1 if any(r["outcome"] in ("FAIL", "ERROR") for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
