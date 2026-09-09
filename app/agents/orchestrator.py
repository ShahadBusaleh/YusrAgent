from app.agents.hr_agent import HRAgent
from app.agents.consultant_agent import ConsultantAgent
from app.agents.manager_agent import ManagerAgent
class OrchestratorAgent:
    def run(self, input: dict) -> dict:
        query = str(input["query"])
        user = input["user"]


        # 1. Decide which agents are needed
        policy_keywords = [
            "policy",
            "law",
            "overtime",
            "annual leave",
            "leave policy",
            "how many days",
            "notice period",
            "working hours",
        ]

        employee_keywords = [
            "my",
            "me",
            "i have",
            "my balance",
            "my leave",
            "remaining",
        ]

        query_lower = query.lower()

        is_policy_question = any(
            keyword in query_lower for keyword in policy_keywords
        )

        is_employee_question = any(
            keyword in query_lower for keyword in employee_keywords
        )

        # 2. Employee-specific request
        if is_employee_question:
            hr_result = HRAgent().run({
                "query": query,
                "user": user,
                "employee_id": user["employee_id"]
            })

            consultant_result = ConsultantAgent().run({
                "query": query,
                "hr_result": hr_result
            })

        # 3. General policy question
        elif is_policy_question:
            hr_result = {
                "facts": {},
                "proposed_action": None,
                "sources": []
            }

            consultant_result = ConsultantAgent().run({
                "query": query,
                "hr_result": hr_result
            })

        # 4. Default: use both HR and Consultant
        else:
            hr_result = HRAgent().run({
                "query": query,
                "user": user,
                "employee_id": user["employee_id"]
            })

            consultant_result = ConsultantAgent().run({
                "query": query,
                "hr_result": hr_result
            })

        # 5. Manager validates the final result
        manager_result = ManagerAgent().run({
            "query": query,
            "user": user,
            "hr_result": hr_result,
            "consultant_result": consultant_result
        })

        return {
            "status": manager_result["decision"],
            "response": manager_result["response"],
            "sources": (
                hr_result.get("sources", [])
                + consultant_result.get("sources", [])
            )
        }
