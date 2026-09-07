from app.agents.base import BaseAgent


class ConsultantAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """Consultant / Policy Agent — RAG over company policy and labor law.

        Expected input:
            query (str): original request
            hr_result (dict): facts/proposed_action from HRAgent

        Expected output:
            recommendation (str): policy-grounded advice
            conflicts (list): missing conditions, exceptions, mismatches
            sources (list): company_policies.id and/or saudi_labor_law.id
        """
        raise NotImplementedError("Manual implementation pending")
