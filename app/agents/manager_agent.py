from app.agents.base import BaseAgent


class ManagerAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """Manager / Validation Agent — final PASS/FAIL supervisor.

        Expected input:
            query (str)
            user (dict)
            hr_result (dict)
            consultant_result (dict)

        Expected output:
            decision: PASS | FAIL
            reasons (list): which governance checks failed, if any
            response (str): final user-facing text when PASS
        """
        raise NotImplementedError("Manual implementation pending")
