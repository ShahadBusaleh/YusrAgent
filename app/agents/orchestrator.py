from app.agents.base import BaseAgent


class OrchestratorAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """Orchestrator: understand, plan, delegate, monitor, synthesize.

        Expected input:
            query (str): free-text HR request
            user (dict): authenticated user_id, employee_id, role

        Expected output:
            status: PASS | FAIL | REPLAN
            response (str): user-facing answer
            sources (list): cited employee/policy identifiers
        """
        raise NotImplementedError("Manual implementation pending")
