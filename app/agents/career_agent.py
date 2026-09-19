"""
Career Development Agent.

Rule-based agent for employee skill growth tracking.
Includes RBAC authorization and development recommendations.
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.db.connection import get_connection
from app.db.career_goals import list_employee_skill_progress
from app.db.employees import get_employee
from app.db.career_recommendations import get_skill_recommendations


_ALLOWED_STAFF_ROLES = {
    "hr_manager",
    "admin",
}


class CareerAgent(BaseAgent):

    def run(self, input: dict) -> dict:

        employee_id = str(
            input.get("employee_id") or ""
        ).strip()

        user = input.get("user") or {}

        viewer_role = user.get("role")
        viewer_employee_id = user.get("employee_id")


        if not employee_id:
            return {
                "status": "FAILED",
                "error": "employee_id is required",
            }


        if (
            viewer_role == "employee"
            and viewer_employee_id != employee_id
        ):
            return {
                "status": "FAILED",
                "error": (
                    "Employees can only view "
                    "their own development progress."
                ),
            }


        if (
            viewer_employee_id != employee_id
            and viewer_role not in _ALLOWED_STAFF_ROLES
        ):
            return {
                "status": "FAILED",
                "error": (
                    "Not authorized to view "
                    "this employee development data."
                ),
            }


        conn = get_connection()

        try:

            employee = get_employee(
                conn,
                employee_id,
            )

            if not employee:
                return {
                    "status": "FAILED",
                    "error": "Employee not found",
                }


            progress = list_employee_skill_progress(
                conn,
                employee_id,
            )


            for item in progress:

                item["recommendations"] = (
                    get_skill_recommendations(
                        item.get("category", "")
                    )
                )


            return {

                "status": "SUCCESS",

                "employee": {
                    "employee_id": employee_id,
                    "full_name": employee.get(
                        "full_name"
                    ),
                    "job_title": employee.get(
                        "job_title"
                    ),
                    "department": employee.get(
                        "department_name"
                    ),
                },

                "skill_progress": progress,

                "summary": {
                    "total_skills": len(progress),

                    "completed": len(
                        [
                            x for x in progress
                            if x["status"] == "COMPLETED"
                        ]
                    ),

                    "in_progress": len(
                        [
                            x for x in progress
                            if x["status"] == "IN_PROGRESS"
                        ]
                    ),

                    "recommended": len(
                        [
                            x for x in progress
                            if x["status"] == "RECOMMENDED"
                        ]
                    ),
                },

                "sources": [
                    f"employees:{employee_id}",
                    f"employee_skill_progress:{employee_id}",
                ],
            }


        finally:
            conn.close()