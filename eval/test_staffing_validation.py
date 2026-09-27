"""New hire / End of Service validation (Phase 4). Every test runs on a
temporary copy of agentic_hr.db, and the real DB is checked unchanged
afterwards. The LLM reason review is stubbed except in the Live* cases,
which need LLM_API_KEY (they call the real model, like Ask Yusor does).

    python -m unittest eval.test_staffing_validation -v
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app.agents import separation_review
from app.agents.hr_agent import HRAgent, get_article_80_grounds, get_salary_scale
from app.config import ROOT, get_settings
from app.db.approvals import decide_approval

REAL_DB = ROOT / "agentic_hr.db"
HR_USER = {"user_id": "t", "employee_id": "EMP-0001", "username": "EMP-0001", "role": "hr_manager"}
APPROVER = "EMP-0091"
HAS_LLM = bool(get_settings().llm_api_key)
CONSISTENT = {"verdict": "consistent", "explanation_en": "", "explanation_ar": "", "suggested_type": None}
GOOD_REASON = "The employee resigned by letter to take a job in another city."


def hire_query(**overrides) -> str:
    fields = {
        "full_name": "Test Hire",
        "nationality": "Egyptian",
        "department_id": "DEP-01",
        "job_title": "Support Analyst",
        "job_grade": "G02",
        "hire_date": date.today().strftime("%d-%m-%Y"),
        "basic_salary": "4000",
        "housing_allowance": "1000",
        "transport_allowance": "400",
    }
    fields.update(overrides)
    return "Hire new employee\n" + "; ".join(f"{k}: {v}" for k, v in fields.items() if v is not None)


def end_query(employee_id: str, **overrides) -> str:
    fields = {
        "employee_id": employee_id,
        "termination_type": "resignation",
        "termination_date": (date.today() + timedelta(days=30)).strftime("%d-%m-%Y"),
        "reason": GOOD_REASON,
    }
    fields.update(overrides)
    return "Terminate employee\n" + "; ".join(f"{k}: {v}" for k, v in fields.items() if v is not None)


class StaffingBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yusor_staff_"))
        self.db = self.tmp / "agentic_hr_copy.db"
        shutil.copy(REAL_DB, self.db)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = patch.dict(os.environ, {"SQLITE_PATH": str(self.db)})
        env.start()
        self.addCleanup(env.stop)
        self.llm = patch.object(separation_review, "_ask_llm", return_value=CONSISTENT)
        self.llm_mock = self.llm.start()
        self.addCleanup(self.llm.stop)
        self.real_db_before = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        self.target = self._free_employee()

    def tearDown(self) -> None:
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_db_before, "real DB changed")

    def conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def _free_employee(self) -> str:
        """An active, non-HR employee with no end of service in progress,
        hired well before today."""
        conn = self.conn()
        busy = set()
        for (payload,) in conn.execute(
            "SELECT payload_json FROM proposed_actions WHERE action_type = 'termination'"
        ):
            busy.add(json.loads(payload).get("employee_id"))
        for row in conn.execute(
            """
            SELECT e.employee_id FROM employees e JOIN users u ON u.employee_id = e.employee_id
            WHERE e.employment_status = 'Active' AND u.role = 'employee'
              AND e.termination_date IS NULL ORDER BY e.employee_id
            """
        ):
            if row[0] not in busy:
                return row[0]
        raise AssertionError("no free employee in the DB copy")

    def hr(self, query: str) -> dict:
        return HRAgent().run({"query": query, "user": HR_USER, "employee_id": HR_USER["employee_id"]})

    def assert_blocked(self, result: dict, pattern: str) -> None:
        assessment = result["facts"]["request_assessment"]
        self.assertEqual(assessment["status"], "NEEDS_INFORMATION", assessment)
        self.assertIsNone(result["proposed_action"])
        self.assertRegex(" ".join(assessment["notes"]), pattern)

    def ready(self, result: dict) -> dict:
        assessment = result["facts"]["request_assessment"]
        self.assertEqual(assessment["status"], "READY_FOR_APPROVAL", assessment)
        return result["proposed_action"]["payload"]

    @staticmethod
    def codes(payload: dict) -> list[str]:
        return [w["code"] for w in payload.get("warnings") or []]


# ---------------------------------------------------------------------------
# 1. New hire pay
# ---------------------------------------------------------------------------

class NewHirePay(StaffingBase):
    def test_salary_one_blocked(self):
        self.assert_blocked(self.hr(hire_query(basic_salary="1")), r"basic_salary 1\.00 SAR is too low")

    def test_salary_zero_blocked(self):
        self.assert_blocked(self.hr(hire_query(basic_salary="0")), r"basic_salary must be a positive amount")

    def test_negative_allowance_blocked(self):
        self.assert_blocked(self.hr(hire_query(housing_allowance="-100")), r"housing_allowance cannot be negative")
        self.assert_blocked(self.hr(hire_query(transport_allowance="-1")), r"transport_allowance cannot be negative")

    def test_job_grade_required_and_known(self):
        result = self.hr(hire_query(job_grade=None))
        self.assert_blocked(result, r"job_grade")
        self.assertIn("job_grade", result["facts"]["request_assessment"]["missing_information"])
        self.assert_blocked(self.hr(hire_query(job_grade="G99")), r"job_grade must be one of: G02")

    def test_saudi_3500_nitaqat_warning(self):
        payload = self.ready(self.hr(hire_query(nationality="Saudi", basic_salary="3500", housing_allowance="0", transport_allowance="350")))
        warning = next(w for w in payload["warnings"] if w["code"] == "nitaqat_half")
        self.assertEqual(warning["threshold"], 4000)
        self.assertEqual(warning["wage"], 3500)
        self.assertIn("0.5 in Nitaqat", warning["text"])

    def test_nitaqat_uses_basic_plus_housing(self):
        # 3,500 + 875 housing = 4,375: counts fully.
        payload = self.ready(self.hr(hire_query(nationality="Saudi", basic_salary="3500", housing_allowance="875", transport_allowance="350")))
        self.assertNotIn("nitaqat_half", self.codes(payload))
        # A non-Saudi below the threshold: Nitaqat doesn't apply.
        payload = self.ready(self.hr(hire_query(nationality="Indian", basic_salary="3500", housing_allowance="0", transport_allowance="350")))
        self.assertNotIn("nitaqat_half", self.codes(payload))

    def test_outside_grade_range_warning(self):
        conn = self.conn()
        low, high = conn.execute(
            "SELECT MIN(basic_salary_sar), MAX(basic_salary_sar) FROM employees WHERE employment_status = 'Active' AND job_grade = 'G05'"
        ).fetchone()
        payload = self.ready(self.hr(hire_query(job_grade="G05", basic_salary="5000", housing_allowance="1250", transport_allowance="500")))
        warning = next(w for w in payload["warnings"] if w["code"] == "grade_range")
        self.assertEqual((warning["min"], warning["max"]), (low, high))
        self.assertIn(f"{low:,.2f}", warning["text"])

    def test_inside_range_typical_allowances_no_warning(self):
        payload = self.ready(self.hr(hire_query(basic_salary="4000", housing_allowance="1000", transport_allowance="400")))
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["new_employee"]["job_grade"], "G02")

    def test_allowance_ratio_warnings_from_db(self):
        scale = get_salary_scale(self.conn())
        g02 = scale["grades"]["G02"]
        self.assertEqual((g02["housing_ratio"], g02["transport_ratio"]), (0.25, 0.1))
        payload = self.ready(self.hr(hire_query(basic_salary="4000", housing_allowance="0", transport_allowance="2000")))
        warnings = {w["code"]: w for w in payload["warnings"]}
        self.assertEqual(warnings["housing_ratio"]["typical"], 0.25)
        self.assertEqual(warnings["transport_ratio"]["ratio"], 0.5)

    def test_approved_hire_writes_job_grade(self):
        payload = self.ready(self.hr(hire_query()))
        from app.agents.manager_agent import _submit_for_approval

        note = _submit_for_approval(HR_USER["employee_id"], "new_hire", payload, "HIGH")
        proposal_id = re.search(r"PA\d+", note).group(0)
        conn = self.conn()
        approval_id = conn.execute("SELECT approval_id FROM pending_approvals WHERE proposal_id = ?", (proposal_id,)).fetchone()[0]
        decide_approval(conn, approval_id, decision="approve", decided_by=APPROVER, decision_note="ok")
        conn.commit()
        new_id = conn.execute("SELECT related_request_id FROM proposed_actions WHERE proposal_id = ?", (proposal_id,)).fetchone()[0]
        self.assertEqual(conn.execute("SELECT job_grade FROM employees WHERE employee_id = ?", (new_id,)).fetchone()[0], "G02")


# ---------------------------------------------------------------------------
# 2. End-of-service reason
# ---------------------------------------------------------------------------

class EndOfServiceReason(StaffingBase):
    def test_one_letter_blocked(self):
        self.assert_blocked(self.hr(end_query(self.target, reason="a")), r"at least 20 characters")
        self.llm_mock.assert_not_called()

    def test_asdfasdf_blocked(self):
        self.assert_blocked(self.hr(end_query(self.target, reason="asdfasdf")), r"at least 20 characters")
        # Long enough, but no real words.
        for junk in ("asdfasdf asdfasdf asdfasdf", "!!!!!!!!!!!!!!!!!!!!!!!!", "test test test test test"):
            self.assert_blocked(self.hr(end_query(self.target, reason=junk)), r"real words")
        self.llm_mock.assert_not_called()

    def test_real_reason_ready_and_reviewed(self):
        payload = self.ready(self.hr(end_query(self.target)))
        self.assertEqual(payload["warnings"], [])
        sent = self.llm_mock.call_args.args[0]
        self.assertEqual((sent["type"], sent["reason"]), ("resignation", GOOD_REASON))

    def test_llm_warning_saved(self):
        self.llm_mock.return_value = {
            "verdict": "contradicts_type", "explanation_en": "Describes a resignation.",
            "explanation_ar": "يصف استقالة.", "suggested_type": "resignation",
        }
        payload = self.ready(self.hr(end_query(self.target, termination_type="termination_by_employer",
                                               termination_date=(date.today() + timedelta(days=60)).strftime("%d-%m-%Y"))))
        self.assertEqual(self.codes(payload), ["reason_contradicts_type"])
        self.assertEqual(payload["warnings"][0]["text_ar"], "يصف استقالة.")

    def test_llm_down_is_a_warning_not_a_block(self):
        self.llm_mock.side_effect = RuntimeError("down")
        payload = self.ready(self.hr(end_query(self.target)))
        self.assertEqual(self.codes(payload), ["reason_review_unavailable"])


# ---------------------------------------------------------------------------
# 3. Article 80
# ---------------------------------------------------------------------------

class Article80(StaffingBase):
    def art80(self, **fields) -> dict:
        return self.hr(end_query(self.target, termination_type="article_80", reason=None, termination_date=None, **fields))

    def test_grounds_come_from_law075_row(self):
        conn = self.conn()
        rule = conn.execute("SELECT rule FROM saudi_labor_law WHERE id = 'LAW075'").fetchone()[0]
        article = get_article_80_grounds(conn)
        self.assertEqual(len(article["grounds"]), len(re.findall(r"\(\d+\)", rule)))
        for ground in article["grounds"]:
            self.assertIn(ground["text"], rule)
            kinds = [r["kind"] for r in ground["requirements"]]
            self.assertIn("objection", kinds)  # LAW075 conditions: every ground
        by_number = {g["number"]: [r["kind"] for r in g["requirements"]] for g in article["grounds"]}
        self.assertIn("written_warning", by_number[2])  # "despite written warning"
        self.assertIn("authority_report", by_number[4])  # "reported to the authorities"
        self.assertIn("written_warning", by_number[7])  # exceptions: "For absence, ..."

    def test_edited_law_text_is_followed(self):
        conn = self.conn()
        conn.execute("UPDATE saudi_labor_law SET rule = rule || ' (10) a test ground added by amendment.' WHERE id = 'LAW075'")
        conn.commit()
        self.assertEqual(get_article_80_grounds(conn)["grounds"][-1]["text"], "a test ground added by amendment")

    def test_without_ground_blocked(self):
        self.assert_blocked(self.art80(article_80_details="He hit his supervisor during the morning shift."), r"article_80_ground is required.*\(1\) assaults")

    def test_short_details_blocked(self):
        self.assert_blocked(self.art80(article_80_ground="3", article_80_details="bad", article_80_objection_date=date.today().strftime("%d-%m-%Y")), r"at least 20 characters")

    def test_missing_procedure_blocked(self):
        today = date.today().strftime("%d-%m-%Y")
        details = "Absent without notice from 1 to 20 August, 20 consecutive working days."
        self.assert_blocked(self.art80(article_80_ground="7", article_80_details=details, article_80_objection_date=today),
                            r"article_80_warning_date")
        future = (date.today() + timedelta(days=3)).strftime("%d-%m-%Y")
        self.assert_blocked(self.art80(article_80_ground="7", article_80_details=details, article_80_objection_date=today,
                                       article_80_warning_date=future), r"cannot be in the future")

    def test_complete_request_and_settlement(self):
        today = date.today().strftime("%d-%m-%Y")
        warned = (date.today() - timedelta(days=10)).strftime("%d-%m-%Y")
        details = "Absent without notice from 1 to 20 August, 20 consecutive working days."
        payload = self.ready(self.art80(article_80_ground="(7)", article_80_details=details,
                                        article_80_objection_date=today, article_80_warning_date=warned))
        art = payload["article_80"]
        self.assertEqual((art["law_id"], art["article"], art["ground_number"]), ("LAW075", "80", 7))
        self.assertTrue(art["ground_text"].startswith("is absent without legitimate reason"))
        self.assertEqual({p["kind"]: p["date"] for p in art["procedures"]}, {"written_warning": warned, "objection": today})
        self.assertEqual(payload["reason"], details)

        # Approve with a warning on the request: the note and the warnings
        # the approver saw are saved together, and the settlement cites the ground.
        payload["warnings"] = [{"code": "reason_vague", "source": "llm", "text": "AI review (vague): x"}]
        from app.agents.manager_agent import _submit_for_approval

        proposal_id = re.search(r"PA\d+", _submit_for_approval(self.target, "termination", payload, "HIGH")).group(0)
        conn = self.conn()
        approval_id = conn.execute("SELECT approval_id FROM pending_approvals WHERE proposal_id = ?", (proposal_id,)).fetchone()[0]
        decide_approval(conn, approval_id, decision="approve", decided_by=APPROVER, decision_note="Read the warning; dates checked.")
        conn.commit()
        saved = json.loads(conn.execute("SELECT payload_json FROM proposed_actions WHERE proposal_id = ?", (proposal_id,)).fetchone()[0])
        self.assertEqual(saved["decision"]["note"], "Read the warning; dates checked.")
        self.assertEqual(saved["decision"]["warnings_acknowledged"], ["reason_vague"])
        self.assertEqual(saved["final_settlement"]["article_80"]["ground_number"], 7)


# ---------------------------------------------------------------------------
# 4. Live LLM review and Ask Yusor chat (real orchestrator + manager)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_LLM, "LLM_API_KEY not set")
class LiveReasonReview(StaffingBase):
    def test_contradictory_reason_warns(self):
        self.llm.stop()
        details = "He submitted a resignation letter because he accepted a better job offer abroad."
        payload = self.ready(self.hr(end_query(
            self.target, termination_type="article_80", reason=None, termination_date=None,
            article_80_ground="3", article_80_details=details,
            article_80_objection_date=date.today().strftime("%d-%m-%Y"),
        )))
        self.llm.start()
        self.assertEqual(self.codes(payload), ["reason_contradicts_type"], payload["warnings"])
        self.assertTrue(payload["warnings"][0]["text_ar"])


@unittest.skipUnless(HAS_LLM, "LLM_API_KEY not set")
class AskYusorChat(StaffingBase):
    def chat(self, query: str) -> tuple[dict, list]:
        from app.agents.orchestrator import OrchestratorAgent

        conn = self.conn()
        before = {r[0] for r in conn.execute("SELECT proposal_id FROM proposed_actions")}
        result = OrchestratorAgent().run({"query": query, "user": HR_USER})
        created = [
            dict(r) for r in conn.execute("SELECT * FROM proposed_actions")
            if r["proposal_id"] not in before
        ]
        return result, created

    def assert_chat_blocked(self, query: str, pattern: str) -> None:
        result, created = self.chat(query)
        self.assertEqual(created, [], result)
        self.assertRegex(result["response"], pattern)

    def test_salary_one_and_zero_blocked(self):
        self.assert_chat_blocked(hire_query(basic_salary="1"), r"too low")
        self.assert_chat_blocked(hire_query(basic_salary="0"), r"positive amount")

    def test_saudi_3500_warning_saved(self):
        result, created = self.chat(hire_query(nationality="Saudi", basic_salary="3500", housing_allowance="0", transport_allowance="350"))
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(len(created), 1)
        self.assertIn("nitaqat_half", self.codes(json.loads(created[0]["payload_json"])))

    def test_outside_grade_warning_saved(self):
        result, created = self.chat(hire_query(job_grade="G05", basic_salary="5000", housing_allowance="1250", transport_allowance="500"))
        self.assertEqual(len(created), 1, result)
        self.assertIn("grade_range", self.codes(json.loads(created[0]["payload_json"])))

    def test_bad_reasons_blocked(self):
        self.assert_chat_blocked(end_query(self.target, reason="a"), r"at least 20 characters")
        self.assert_chat_blocked(end_query(self.target, reason="asdfasdf"), r"at least 20 characters")

    def test_article_80_without_ground_blocked(self):
        self.assert_chat_blocked(
            end_query(self.target, termination_type="article_80", reason="He hit his supervisor during the morning shift."),
            r"article_80_ground is required",
        )

    def test_contradictory_reason_warning_saved(self):
        self.llm.stop()
        result, created = self.chat(end_query(
            self.target, termination_type="end_of_contract",
            reason="He was caught stealing company laptops from the warehouse last week.",
        ))
        self.llm.start()
        self.assertEqual(len(created), 1, result)
        codes = self.codes(json.loads(created[0]["payload_json"]))
        self.assertTrue(codes and codes[0] in {"reason_contradicts_type", "reason_unrelated"}, codes)


# ---------------------------------------------------------------------------
# 5. UI: "End of Service" in both languages, old records still display
# ---------------------------------------------------------------------------

def _ui_script(lang: str, record: dict, payload: dict) -> None:
    import streamlit as st

    from app.ui import exports, i18n, views

    i18n.set_lang(lang)
    st.markdown("TYPE:" + views._record_type_label("termination"))
    st.markdown("TAB:" + i18n.t("staffing.tab_term"))
    st.markdown("TITLE:" + i18n.t("staff.terminate_employee"))
    views._render_termination_details(record["payload"])
    views._render_request_warnings(payload, for_approver=True)
    doc = views._settlement_document(record)
    st.markdown("PDFLABELS:" + "|".join(label for label, _ in doc["sections"][0]["pairs"]))
    st.markdown("PDFBYTES:" + str(len(exports.settlement_pdf(doc, rtl=i18n.is_rtl()))))


class UiEndOfService(StaffingBase):
    def run_ui(self, lang: str, record: dict, payload: dict):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_function(_ui_script, args=(lang, record, payload), default_timeout=60)
        app.run()
        self.assertFalse(app.exception, app.exception)
        return app

    def old_record(self) -> dict:
        from app.db.records import get_record

        conn = self.conn()
        for row in conn.execute(
            "SELECT proposal_id, payload_json FROM proposed_actions WHERE action_type = 'termination' AND status = 'approved'"
        ):
            if "final_settlement" in json.loads(row[1]) and "warnings" not in json.loads(row[1]):
                return get_record(conn, row[0])
        self.skipTest("no pre-change approved end of service in the DB copy")

    def test_both_languages(self):
        record = self.old_record()
        warnings = {"warnings": [
            {"code": "nitaqat_half", "wage": 3500, "threshold": 4000},
            {"code": "reason_contradicts_type", "source": "llm", "text": "AI review", "explanation": "Describes a resignation.",
             "text_ar": "يصف استقالة.", "suggested_type": "resignation"},
        ]}
        expected = {
            "en": ("End of Service", "0.5 in Nitaqat", "Describes a resignation.", "End-of-service type"),
            "ar": ("إنهاء الخدمة", "نصف موظف في نطاقات", "يصف استقالة.", "نوع إنهاء الخدمة"),
        }
        for lang, (label, nitaqat, llm_text, type_label) in expected.items():
            app = self.run_ui(lang, record, warnings)
            text = "\n".join(m.value for m in app.markdown)
            self.assertIn(f"TYPE:{label}", text)
            self.assertIn(f"TAB:{label}", text)
            self.assertIn(f"TITLE:{label}", text)
            self.assertNotRegex(text, r"(?i)\bterminat")
            warning_text = "\n".join(w.value for w in app.warning)
            self.assertIn(nitaqat, warning_text)
            self.assertIn(llm_text, warning_text)
            self.assertIn(type_label, re.search(r"PDFLABELS:(.*)", text).group(1))
            self.assertGreater(int(re.search(r"PDFBYTES:(\d+)", text).group(1)), 1000)


if __name__ == "__main__":
    unittest.main()
