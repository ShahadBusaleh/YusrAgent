import re

from app.agents.consultant_agent import ConsultantAgent
from app.agents.hr_agent import HRAgent
from app.agents.leave_intent import detects_leave_submission_intent
from app.agents.manager_agent import ManagerAgent

_EMPTY_HR = {
    "facts": {},
    "proposed_action": None,
    "sources": [],
}

# Word boundaries so "me" does not match inside "employment".
_EMPLOYEE_FACT_PATTERNS = (
    r"\bmy\b",
    r"\bme\b",
    r"\bi have\b",
    r"\bi've\b",
    r"\bdo i have\b",
    r"\bremaining\b",
    r"\bbalance\b",
    r"\bsalary\b",
)


class OrchestratorAgent:
    def run(self, input: dict) -> dict:
        """Plan and call HR → Consultant → Manager. No SQL, RAG, or governance here.

        Expected input:
            query (str)
            user (dict): { user_id, employee_id, username, role }

        Expected output:
            status: PASS | FAIL | REPLAN
            response (str)
            sources (list)
        """
        query = str(input.get("query") or "")
        user = input.get("user") or {}

        hr_result = (
            self._run_hr(query, user)
            if self._needs_hr(query)
            else dict(_EMPTY_HR)
        )

        consultant_result = ConsultantAgent().run(
            {
                "query": query,
                "hr_result": hr_result,
            }
        )

        manager_result = ManagerAgent().run(
            {
                "query": query,
                "user": user,
                "hr_result": hr_result,
                "consultant_result": consultant_result,
            }
        )

        return {
            "status": manager_result.get("decision"),
            "response": manager_result.get("response") or "",
            "sources": list(hr_result.get("sources") or [])
            + list(consultant_result.get("sources") or []),
        }

    @staticmethod
    def _needs_hr(query: str) -> bool:
        query_lower = query.lower()
        return (
            any(
                re.search(pattern, query_lower) for pattern in _EMPLOYEE_FACT_PATTERNS
            )
            or detects_leave_submission_intent(query)
        )

    @staticmethod
    def _run_hr(query: str, user: dict) -> dict:
        try:
            result = HRAgent().run(
                {
                    "query": query,
                    "user": user,
                    "employee_id": user.get("employee_id"),
                }
            )
        except NotImplementedError:
            # HR stub until Member 2 merges. Policy half can still run.
            return dict(_EMPTY_HR)

        if not isinstance(result, dict):
            return dict(_EMPTY_HR)
        return result
