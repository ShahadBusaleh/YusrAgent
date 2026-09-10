from app.agents.base import BaseAgent
from app.db.connection import get_connection
from app.db.employees import get_employee
from app.db.leave import get_leave_balance, list_leave_requests

_STAFF_ROLES = {"hr_specialist", "hr_manager", "admin"}
_PROFILE_FIELDS = (
    "employee_id",
    "full_name",
    "job_title",
    "department_id",
    "department_name",
    "hire_date",
    "employment_status",
    "manager_id",
    "salary",
    "basic_salary",
)


def _own_record_only(user: dict, employee_id: str) -> bool:
    """Employees may only read their own employee_id. HR/admin may read any."""
    role = (user or {}).get("role")
    user_eid = (user or {}).get("employee_id")
    if not employee_id or not role:
        return False
    if role in _STAFF_ROLES:
        return True
    if role == "employee":
        return bool(user_eid) and user_eid == employee_id
    return False


def _safe_profile(employee: dict) -> dict:
    return {key: employee[key] for key in _PROFILE_FIELDS if key in employee}


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
        user = input.get("user") or {}
        employee_id = str(
            input.get("employee_id") or user.get("employee_id") or ""
        ).strip()

        empty = {"facts": {}, "proposed_action": None, "sources": []}
        if not _own_record_only(user, employee_id):
            return empty

        conn = get_connection()
        try:
            facts: dict = {"employee_id": employee_id}
            sources: list[str] = []

            balance = get_leave_balance(conn, employee_id)
            if balance:
                facts["leave_balance"] = balance
                annual_remaining = balance.get("annual_remaining")
                if annual_remaining is not None:
                    facts["annual_remaining"] = annual_remaining
                    facts["remaining_balance"] = annual_remaining
                sources.append(f"leave_balances:{employee_id}")

            employee = get_employee(conn, employee_id)
            if employee:
                facts["profile"] = _safe_profile(employee)
                sources.append(f"employees:{employee_id}")

            requests = list_leave_requests(conn, employee_id)
            if requests:
                facts["leave_requests"] = requests
                sources.append(f"leave_requests:{employee_id}")
        finally:
            conn.close()

        return {
            "facts": facts,
            "proposed_action": None,
            "sources": sources,
        }
