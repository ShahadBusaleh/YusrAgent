"""Regulation update agent (Phase 4). Every test runs on a temporary copy of
agentic_hr.db; the network, the LLM, RAG retrieval and Qdrant upserts are
stubbed, so nothing real is fetched, written or indexed.

    python -m unittest eval.test_regulations -v
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import regulation_agent as agent
from app.api.deps import CurrentUser
from app.api.routers import regulations as router_mod
from app.config import ROOT
from app.db import records as records_db
from app.db import regulations as reg_db
from app.db.approvals import decide_approval

REAL_DB = ROOT / "agentic_hr.db"
EXCERPT = agent.DATA_DIR / "boe_labor_law_excerpt.html"
NEW_020 = "During Ramadan, actual working hours for Muslim employees are reduced to no more than five hours per day or 30 hours per week."
NEW_POL_004 = "Working hours are reduced to five hours per day (30 hours per week) for Muslim employees during Ramadan."
EDITED_POL_004 = "During Ramadan, Muslim employees work at most five hours per day and 30 hours per week."


def fake_llm(system: str, data: dict) -> dict:
    if system == agent._LAW_PROMPT:
        rows = []
        for row in data["rows"]:
            if data["change"] == "removed":
                rows.append({**row, "changed": True, "rule": f"Repealed: Article {data['article_number']} was removed."})
            elif row["law_id"] == "LAW020":
                rows.append({**row, "changed": True, "rule": NEW_020})
            else:
                rows.append({**row, "changed": False})
        new_row = None
        if data["change"] == "added":
            new_row = {"category": "Working Hours", "title": "New test article", "rule": "A new rule.", "conditions": "-", "exceptions": "-"}
        return {"summary_en": "Ramadan hours reduced to 5 h/day (30 h/week).", "summary_ar": "تخفيض ساعات رمضان.",
                "law_rows": rows, "new_law_row": new_row}
    policies = [
        {"policy_id": "AAM-POL-004", "status": "conflict", "reason": "The policy allows 6 hours; the law now allows 5.",
         "proposed_rule": NEW_POL_004, "cited_law_ids": ["LAW020", "LAW999"]},
        {"policy_id": "AAM-POL-999", "status": "conflict", "reason": "not a candidate", "proposed_rule": "x"},
    ]
    return {"policies": policies}


def fake_retrieve(query: str, top_k: int = 5) -> list[dict]:
    return [
        {"id": "AAM-POL-004", "source_table": "company_policies"},
        {"id": "LAW020", "source_table": "saudi_labor_law"},
    ]


def _tree_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*.txt")):
        digest.update(path.as_posix().encode() + path.read_bytes())
    return digest.hexdigest()


class RegulationTestBase(unittest.TestCase):
    mode = "live"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yusor_reg_"))
        self.db = self.tmp / "demo_agentic_hr.db"
        shutil.copy(REAL_DB, self.db)
        # Clean slate in the COPY: the real DB may already hold a live
        # baseline from a real startup check.
        with sqlite3.connect(self.db) as clean:
            clean.execute("DROP TABLE IF EXISTS regulation_versions")
            clean.execute("DELETE FROM audit_log WHERE event_type LIKE 'regulation%'")
        self.demo_dir = self.tmp / "demo_policy_texts"
        env = {
            "SQLITE_PATH": str(self.db),
            "REGULATION_SOURCE": self.mode,
            "QDRANT_COLLECTION": "yusor_policies_demo_test",
            "REGULATION_DEMO_POLICY_DIR": str(self.demo_dir),
            "REGULATION_AUTO_CHECK": "0",
        }
        self.upserted: list[dict] = []
        patchers = [
            patch.dict(os.environ, env),
            patch.object(agent, "_llm_json", side_effect=fake_llm),
            patch.object(agent, "_retrieve", side_effect=fake_retrieve),
            patch.object(agent, "_upsert", side_effect=lambda chunks: self.upserted.extend(chunks) or len(chunks)),
            patch.object(agent, "fetch_hrsd", return_value=[]),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tracked_before = _tree_hash(reg_db.TRACKED_POLICY_DIR)
        self.real_db_before = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(_tree_hash(reg_db.TRACKED_POLICY_DIR), self.tracked_before, "tracked policy_texts/ changed")
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_db_before, "real DB changed")

    def conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(conn.close)
        return conn

    def proposal(self, conn, proposal_id) -> dict:
        row = conn.execute("SELECT * FROM proposed_actions WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload_json"])}

    def approval_id(self, conn, proposal_id) -> str:
        return conn.execute("SELECT approval_id FROM pending_approvals WHERE proposal_id = ?", (proposal_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Parsing and normalisation (no DB)
# ---------------------------------------------------------------------------

class ParserCases(unittest.TestCase):
    def test_excerpt_of_real_page(self):
        articles = {a["number"]: a for a in agent.parse_boe_labor_law(EXCERPT.read_text(encoding="utf-8"))}
        self.assertIn("98", articles)
        self.assertIn("ست ساعات في اليوم", articles["98"]["text"])
        self.assertEqual(articles["98"]["label"], "المادة الثامنة والتسعون")
        # A changed article's previous versions (popup) are not its text.
        self.assertTrue(all("تعديلات المادة" not in a["text"] for a in articles.values()))

    def test_article_numbers(self):
        cases = {
            "المادة الأولى :": "1", "المادة الحادية عشرة": "11", "المادة الثامنة والتسعون :": "98",
            "المادة المائة": "100", "المادة التاسعة بعد المائة :": "109",
            "المادة الخامسة والأربعون بعد المائتين :": "245",
            "الفصل الأول : التعريفاتالمادة الأولى :": "1",
            "( التاسعة والسبعين مكرر )": "79bis",
            "(المادة الحادية والثلاثون بعد المائتين حالياً) (المادة الأربعون بعد المائتين سابقاً) :": "231",
            "الباب الثاني عشر: العمل في المناجم والمحاجر": None,
        }
        for title, expected in cases.items():
            self.assertEqual(agent.article_key(title), expected, title)

    def test_normalised_hash_ignores_diacritics_and_spacing(self):
        a = "لا يجوز تشغيل العامل  تشغيلًا فعليًّا ٨ ساعات"
        b = "لا يجوز تشغيل العامل تشغيلا فعليا 8 ساعات"
        self.assertEqual(agent.text_hash(a), agent.text_hash(b))
        self.assertNotEqual(agent.text_hash(a), agent.text_hash(a.replace("٨", "٧")))

    def test_user_agent_has_no_personal_data(self):
        self.assertNotRegex(agent.USER_AGENT, r"@|\+?\d{7,}|mailto|gmail")
        with agent._http_client() as client:
            self.assertEqual(client.headers["User-Agent"], agent.USER_AGENT)


# ---------------------------------------------------------------------------
# Live mode: detection
# ---------------------------------------------------------------------------

def _live(articles: list[dict]) -> dict:
    return {"source": "boe_live", "url": agent.BOE_URL, "fetched_at": reg_db.now_iso(),
            "articles": [{**a, "hash": agent.text_hash(a["text"])} for a in articles]}


BASE = [
    {"number": "84", "label": "المادة الرابعة والثمانون", "text": "نص المادة الرابعة والثمانين."},
    {"number": "98", "label": "المادة الثامنة والتسعون", "text": "بحيث لا تزيد على ست ساعات في اليوم."},
    {"number": "109", "label": "المادة التاسعة بعد المائة", "text": "إجازة سنوية لا تقل عن واحد وعشرين يومًا."},
]
CHANGED_98 = [BASE[0], {**BASE[1], "text": "بحيث لا تزيد على خمس ساعات في اليوم."}, BASE[2]]


class LiveDetectionCases(RegulationTestBase):
    def check(self, articles):
        with patch.object(agent, "fetch_boe", return_value=_live(articles)):
            return agent.run_check("test")

    def test_first_run_stores_baseline_without_requests(self):
        result = self.check(BASE)
        self.assertEqual(result["baseline_stored"], 3)
        self.assertEqual(result["requests_created"], [])

    def test_no_change_run(self):
        self.check(BASE)
        result = self.check(BASE)
        self.assertEqual((result["changes_found"], result["requests_created"]), (0, []))
        self.assertEqual(reg_db.last_check(self.conn())["changes_found"], 0)

    def test_changed_article_creates_one_high_risk_request(self):
        self.check(BASE)
        result = self.check(CHANGED_98)
        self.assertEqual(len(result["requests_created"]), 1)
        conn = self.conn()
        p = self.proposal(conn, result["requests_created"][0])
        self.assertEqual((p["action_type"], p["risk_level"], p["employee_id"]), ("regulation_update", "HIGH", None))
        self.assertEqual(p["payload"]["requested_by"], "system")
        self.assertEqual(p["payload"]["change_kind"], "changed")
        self.assertFalse(p["payload"]["simulated"])
        approval = conn.execute("SELECT * FROM pending_approvals WHERE proposal_id = ?", (p["proposal_id"],)).fetchone()
        self.assertEqual((approval["status"], approval["employee_id"]), ("pending", None))

    def test_removed_and_added_articles(self):
        self.check(BASE)
        result = self.check([BASE[0], BASE[1], {"number": "300", "label": "مادة جديدة", "text": "نص جديد."}])
        conn = self.conn()
        kinds = sorted(self.proposal(conn, pid)["payload"]["change_kind"] for pid in result["requests_created"])
        self.assertEqual(kinds, ["added", "removed"])
        removed = next(self.proposal(conn, pid)["payload"] for pid in result["requests_created"]
                       if self.proposal(conn, pid)["payload"]["change_kind"] == "removed")
        self.assertEqual(removed["new_text"], "")
        self.assertTrue(all(r["changed"] for r in removed["law_rows"]))

    def test_mass_removal_is_a_source_error_not_repeal(self):
        self.check(BASE * 1)
        result = self.check([BASE[0]])  # 2 of 3 missing
        self.assertEqual(result["requests_created"], [])
        self.assertTrue(result["errors"])

    def test_duplicate_protection(self):
        self.check(BASE)
        first = self.check(CHANGED_98)
        second = self.check(CHANGED_98)
        self.assertEqual(len(first["requests_created"]), 1)
        self.assertEqual(second["requests_created"], [])
        self.assertEqual(second["skipped"][0]["reason"], "already pending")
        # The DB-level unique index also refuses a second pending version.
        conn = self.conn()
        payload = self.proposal(conn, first["requests_created"][0])["payload"]
        self.assertIsNone(agent.create_request(conn, payload))
        conn.commit()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM proposed_actions WHERE action_type = 'regulation_update'").fetchone()[0], 1)

    def test_llm_down_falls_back_to_needs_review(self):
        self.check(BASE)
        with patch.object(agent, "_llm_json", side_effect=RuntimeError("LLM down")):
            result = self.check(CHANGED_98)
        payload = self.proposal(self.conn(), result["requests_created"][0])["payload"]
        self.assertTrue(payload["analysis"]["fallback"])
        self.assertEqual({p["status"] for p in payload["policies"]}, {"needs_review"})
        self.assertTrue(all(p["proposed_rule"] == p["old_rule"] for p in payload["policies"]))


# ---------------------------------------------------------------------------
# Simulate mode: the conflict demo, decisions, four-eyes, re-index
# ---------------------------------------------------------------------------

class SimulatedCases(RegulationTestBase):
    mode = "simulated"

    def create(self) -> str:
        result = agent.run_check("test")
        self.assertEqual(result.get("status"), "ok", result)
        self.assertEqual(len(result["requests_created"]), 1)
        return result["requests_created"][0]

    def test_conflict_case(self):
        pid = self.create()
        payload = self.proposal(self.conn(), pid)["payload"]
        self.assertTrue(payload["simulated"])
        self.assertIn("SIMULATED", payload["source_label"])
        self.assertEqual(payload["article_no"], "98")
        self.assertIn("خمس ساعات", payload["new_text"])
        self.assertIn("ست ساعات", payload["old_text"])
        policy = payload["policies"][0]
        self.assertEqual((policy["policy_id"], policy["status"]), ("AAM-POL-004", "conflict"))
        self.assertEqual(policy["cited_law_ids"], ["LAW020"])        # LAW999 was never retrieved: dropped
        self.assertEqual(len(payload["policies"]), 1)                   # AAM-POL-999 not a candidate: dropped
        self.assertEqual(payload["impact_level"], "high")
        self.assertEqual([r["law_id"] for r in payload["law_rows"] if r["changed"]], ["LAW020"])
        self.assertTrue(all(c["source_url"] for c in payload["citations"]))

    def test_approve_applies_versions_audit_and_reindex(self):
        pid = self.create()
        conn = self.conn()
        old_law = dict(conn.execute("SELECT * FROM saudi_labor_law WHERE id = 'LAW020'").fetchone())
        decide_approval(conn, self.approval_id(conn, pid), decision="approve", decided_by="EMP-0001", decision_note=None)
        conn.commit()
        self.assertEqual(conn.execute("SELECT rule FROM saudi_labor_law WHERE id = 'LAW020'").fetchone()[0], NEW_020)
        self.assertEqual(conn.execute("SELECT rule FROM company_policies WHERE id = 'AAM-POL-004'").fetchone()[0], NEW_POL_004)
        law_versions = conn.execute(
            "SELECT status, text FROM regulation_versions WHERE kind = 'law_row' AND ref_id = 'LAW020' ORDER BY version_id").fetchall()
        self.assertEqual([v["status"] for v in law_versions], ["superseded", "current"])
        self.assertEqual(json.loads(law_versions[0]["text"])["rule"], old_law["rule"])
        self.assertEqual(conn.execute(
            "SELECT status FROM regulation_versions WHERE kind = 'article' AND ref_id = 'art:98' ORDER BY version_id DESC").fetchone()[0], "current")
        final = self.proposal(conn, pid)["payload"]["final"]
        self.assertEqual(final["outcome"], "approved")
        self.assertEqual(final["reindex"]["status"], "pending")
        self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE event_type = 'regulation_update_applied' AND details LIKE ?", (f"%{pid}%",)).fetchone())

        info = agent.reindex_proposal(conn, pid)
        self.assertEqual(info["status"], "done")
        self.assertEqual(sorted(c["id"] for c in self.upserted), ["AAM-POL-004", "LAW020"])
        self.assertIn(NEW_020, (self.demo_dir / "saudi_labor_law" / "LAW020.txt").read_text(encoding="utf-8"))
        self.assertIn(NEW_POL_004, (self.demo_dir / "company_policies" / "AAM-POL-004.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.proposal(conn, pid)["payload"]["final"]["reindex"]["collection"], "yusor_policies_demo_test")
        # Applied: the same change is not proposed again.
        self.assertEqual(agent.run_check("test")["requests_created"], [])

    def test_failed_reindex_is_retried(self):
        pid = self.create()
        conn = self.conn()
        decide_approval(conn, self.approval_id(conn, pid), decision="approve", decided_by="EMP-0001", decision_note=None)
        conn.commit()
        with patch.object(agent, "_upsert", side_effect=RuntimeError("Qdrant down")):
            self.assertEqual(agent.reindex_proposal(conn, pid)["status"], "failed")
        self.assertEqual([r["status"] for r in agent.reindex_pending(conn)], ["done"])

    def test_reject_is_never_reproposed(self):
        pid = self.create()
        conn = self.conn()
        decide_approval(conn, self.approval_id(conn, pid), decision="reject", decided_by="EMP-0001", decision_note="no")
        conn.commit()
        self.assertEqual(conn.execute("SELECT rule FROM company_policies WHERE id = 'AAM-POL-004'").fetchone()[0][:24],
                         "Working hours are reduce")
        self.assertIn("six hours", conn.execute("SELECT rule FROM company_policies WHERE id = 'AAM-POL-004'").fetchone()[0])
        self.assertEqual(self.proposal(conn, pid)["payload"]["final"]["outcome"], "rejected")
        again = agent.run_check("test")
        self.assertEqual(again["requests_created"], [])
        self.assertEqual(again["skipped"][0]["reason"], "rejected earlier")


class ApiCases(RegulationTestBase):
    """Through the router: edit, four-eyes, send back, guard."""

    mode = "simulated"

    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(router_mod.router)
        self.user = CurrentUser(user_id="U1", employee_id="EMP-0001", username="EMP-0001", role="hr_manager")
        app.dependency_overrides[router_mod._MANAGERS] = lambda: self.user
        self.client = TestClient(app)  # no context manager: startup checks don't run
        result = agent.run_check("test")
        self.pid = result["requests_created"][0]

    def as_user(self, employee_id: str) -> None:
        self.user = CurrentUser(user_id=employee_id, employee_id=employee_id, username=employee_id, role="hr_manager")

    def test_edit_then_approve_needs_a_second_person(self):
        r = self.client.put(f"/regulations/requests/{self.pid}/proposed-text",
                            json={"target": "policy", "id": "AAM-POL-004", "text": EDITED_POL_004})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "approve"})
        self.assertEqual(r.status_code, 403)
        conn = self.conn()
        self.assertEqual(self.proposal(conn, self.pid)["status"], "pending_approval")

        self.as_user("EMP-0091")
        r = self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "approve"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["reindex"]["status"], "done")
        self.assertEqual(conn.execute("SELECT rule FROM company_policies WHERE id = 'AAM-POL-004'").fetchone()[0], EDITED_POL_004)
        payload = self.proposal(conn, self.pid)["payload"]
        self.assertEqual(payload["edits"][0]["by"], "EMP-0001")
        self.assertEqual(payload["final"]["decided_by"], "EMP-0091")
        self.assertIn(EDITED_POL_004, (self.demo_dir / "company_policies" / "AAM-POL-004.txt").read_text(encoding="utf-8"))

    def test_four_eyes_backstop_on_generic_approvals_path(self):
        self.client.put(f"/regulations/requests/{self.pid}/proposed-text",
                        json={"target": "law_row", "id": "LAW020", "text": "Edited law text."})
        conn = self.conn()
        with self.assertRaises(reg_db.FourEyesError):
            decide_approval(conn, self.approval_id(conn, self.pid), decision="approve", decided_by="EMP-0001", decision_note=None)
        conn.rollback()
        self.assertEqual(self.proposal(conn, self.pid)["status"], "pending_approval")
        self.assertNotEqual(conn.execute("SELECT rule FROM saudi_labor_law WHERE id = 'LAW020'").fetchone()[0], "Edited law text.")

    def test_send_back_is_reanalysed_and_linked(self):
        r = self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "send_back", "decision_note": "redo"})
        self.assertEqual(r.json()["outcome"], "sent_back")
        again = agent.run_check("test")
        self.assertEqual(len(again["requests_created"]), 1)
        new = self.proposal(self.conn(), again["requests_created"][0])
        self.assertEqual(new["payload"]["previous_proposal_id"], self.pid)
        self.assertEqual(new["related_request_id"], self.pid)

    def test_decided_twice_is_refused(self):
        self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "reject"})
        r = self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)

    def test_apply_refused_when_not_in_demo_environment(self):
        with patch.dict(os.environ, {"QDRANT_COLLECTION": "yusor_policies"}):
            r = self.client.post(f"/regulations/requests/{self.pid}/decide", json={"decision": "approve"})
            self.assertEqual(r.status_code, 409)
            conn = self.conn()
            with self.assertRaises(reg_db.RegulationGuardError):
                decide_approval(conn, self.approval_id(conn, self.pid), decision="approve", decided_by="EMP-0091", decision_note=None)
            conn.rollback()
        self.assertEqual(self.proposal(self.conn(), self.pid)["status"], "pending_approval")

    def test_pending_list_includes_system_requests(self):
        rows = self.client.get("/regulations/pending").json()
        self.assertEqual([r["proposal_id"] for r in rows], [self.pid])
        self.assertIsNone(rows[0]["employee_id"])
        self.assertEqual(rows[0]["proposal"]["payload_json"]["article_no"], "98")

    def test_records_display(self):
        conn = self.conn()
        record = records_db.get_record(conn, self.pid)
        self.assertEqual((record["subject_id"], record["requested_by"]), ("Art. 98", "system"))
        self.assertIn("[SIMULATED]", record["summary"])
        listed = records_db.list_records(conn, types=["regulation_update"])
        self.assertEqual([i["proposal_id"] for i in listed["items"]], [self.pid])
        self.assertEqual(len(records_db.export_records(conn, types=["regulation_update"])), 1)
        r = self.client.get(f"/regulations/requests/{self.pid}")
        self.assertEqual(r.json()["payload"]["policies"][0]["status"], "conflict")


class GuardCases(RegulationTestBase):
    mode = "simulated"

    def test_refused_against_the_real_db_writes_nothing(self):
        with patch.object(reg_db, "_REAL_DBS", {self.db.resolve()}):
            result = agent.run_check("test")
        self.assertEqual(result["status"], "refused")
        conn = self.conn()
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'regulation_versions'").fetchone())
        self.assertIsNone(conn.execute("SELECT 1 FROM audit_log WHERE event_type = 'regulation_check'").fetchone())

    def test_shared_collection_and_tracked_folder_are_refused(self):
        with patch.dict(os.environ, {"QDRANT_COLLECTION": "yusor_policies"}):
            self.assertEqual(agent.run_check("test")["status"], "refused")
        with patch.dict(os.environ, {"REGULATION_DEMO_POLICY_DIR": str(reg_db.TRACKED_POLICY_DIR / "x")}):
            self.assertEqual(agent.run_check("test")["status"], "refused")
        with patch.dict(os.environ, {"REGULATION_DEMO_POLICY_DIR": ""}):
            self.assertEqual(agent.run_check("test")["status"], "refused")

    def test_last_check_is_per_mode(self):
        conn = self.conn()
        reg_db.log_check(conn, {"mode": "live", "changes_found": 0}, "system")
        reg_db.log_check(conn, {"mode": "simulated", "changes_found": 1}, "system")
        reg_db.log_check(conn, {"mode": "live", "changes_found": 2}, "system")
        conn.commit()
        self.assertEqual(reg_db.last_check(conn, "simulated")["changes_found"], 1)
        self.assertEqual(reg_db.last_check(conn, "live")["changes_found"], 2)

    def test_live_is_the_default_mode(self):
        with patch.dict(os.environ, {"REGULATION_SOURCE": ""}):
            self.assertEqual(reg_db.source_mode(), "live")


if __name__ == "__main__":
    unittest.main()
