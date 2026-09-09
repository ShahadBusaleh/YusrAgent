class OrchestratorAgent:
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

        query = input["query"]
        user = input["user"]

        # 1. HR Agent: get employee facts
        hr_result = HRAgent().run({
            "query": query,
            "user": user,
            "employee_id": user["employee_id"]
        })

        # 2. Consultant Agent: analyze policy using HR facts
        consultant_result = ConsultantAgent().run({
            "query": query,
            "hr_result": hr_result
        })

        # 3. Manager Agent: validate and make the final decision
        manager_result = ManagerAgent().run({
            "query": query,
            "user": user,
            "hr_result": hr_result,
            "consultant_result": consultant_result
        })

        # 4. Return the final orchestrator result
        return {
            "status": manager_result["decision"],
            "response": manager_result["response"],
            "sources": (
                hr_result.get("sources", [])
                + consultant_result.get("sources", [])
            )
        }
