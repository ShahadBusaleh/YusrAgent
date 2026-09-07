from app.agents.base import BaseAgent


class HRAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """HR / Execution Agent — structured HR data and operations.

        Expected input:
            query (str): user request
            user (dict): authenticated caller
            employee_id (str): target employee, already authorized by the API

        Expected output:
            facts (dict): records pulled from SQLite (leave, profile, etc.)
            proposed_action (dict | None): action_type + payload
            sources (list): table/row identifiers used
        """
        raise NotImplementedError("Manual implementation pending")
