"""Offline guards for Phase 4 staffing wiring in the Orchestrator.

fe67d6e was edited from a pre-Phase-4 copy of orchestrator.py and silently
dropped the staffing fast path and the Decision Brief's termination_profile
(the UI still renders it). No test covered either, so nothing failed.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import orchestrator as orch

ROOT = Path(__file__).resolve().parent.parent


class StaffingFastPathCases(unittest.TestCase):
    def test_staffing_requests_route_to_hr_without_the_llm(self):
        agent = orch.OrchestratorAgent()
        with patch.object(agent.client.chat.completions, "create") as llm:
            for query in (
                "Please terminate employee EMP-0005, reason: repeated absence without notice",
                "Hire new employee Sara as a data analyst",
                "Add new employee to the finance department",
            ):
                self.assertEqual(agent.classify_intent(query), "HR", query)
            llm.assert_not_called()

    def test_teammates_both_fast_path_still_works(self):
        agent = orch.OrchestratorAgent()
        with patch.object(agent.client.chat.completions, "create") as llm:
            self.assertEqual(agent.classify_intent("Am I entitled to 30 days of annual leave?"), "BOTH")
            llm.assert_not_called()


class DecisionBriefProfileCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "brief.db")
        shutil.copyfile(ROOT / "agentic_hr.db", self.db)
        env = patch.dict(os.environ, {"SQLITE_PATH": self.db, "YUSOR_PREFETCH_TRANSLATIONS": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def pending(self, action_type, payload):
        from app.db.approvals import create_pending_approval
        from app.db.connection import get_connection
        from app.db.proposed_actions import create_proposed_action

        conn = get_connection()
        try:
            proposal = create_proposed_action(
                conn, employee_id="EMP-0003", action_type=action_type, payload=payload, risk_level="high"
            )
            create_pending_approval(
                conn, proposal_id=proposal["proposal_id"], employee_id="EMP-0003",
                action_summary=action_type, risk_level="high",
            )
            conn.commit()
        finally:
            conn.close()
        return proposal["proposal_id"]

    def brief(self, proposal_id):
        agent = orch.OrchestratorAgent()
        stub_hr = {"facts": {}, "proposed_action": None, "sources": []}
        stub_consultant = {"recommendation": "", "conflicts": [], "sources": []}
        stub_manager = {"decision": "PASS", "reasons": [], "response": "AI Recommendation: MANAGER REVIEW"}
        with patch.object(agent.hr_agent, "run", return_value=stub_hr), \
                patch.object(agent.consultant_agent, "run", return_value=stub_consultant), \
                patch.object(agent.manager_agent, "run", return_value=stub_manager), \
                patch.object(orch, "get_termination_profile", return_value={"profile": "stub"}) as profile:
            return agent.explain_pending_approval(proposal_id), profile

    def test_termination_brief_has_profile(self):
        pid = self.pending("termination", {"employee_id": "EMP-0003", "termination_type": "resignation"})
        result, profile = self.brief(pid)
        self.assertEqual(result["status"], "SUCCESS", result)
        self.assertEqual(result["brief"]["termination_profile"], {"profile": "stub"})
        profile.assert_called_once()

    def test_other_briefs_have_no_profile(self):
        pid = self.pending("certificate_request", {"employee_id": "EMP-0003"})
        result, profile = self.brief(pid)
        self.assertIsNone(result["brief"]["termination_profile"])
        profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
