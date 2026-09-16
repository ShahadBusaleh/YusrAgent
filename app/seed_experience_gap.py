"""
Synthetic seed data for the Experience Gap Insight feature.

IMPORTANT:
- This data is synthetic / placeholder data for demonstration only.
- Coverage is derived from job_title, not an individual employee
  assessment or proficiency score.
- It only writes to the three new Experience Gap tables:
    skills
    department_requirements
    skill_job_titles
- Existing HR tables and rows are not modified.
"""

from __future__ import annotations

from app.db.connection import get_connection
from app.db.skills import ensure_experience_gap_tables


# ---------------------------------------------------------
# Skill catalogue
# ---------------------------------------------------------

SKILLS = {
    # Operations
    "SK-OPS-01": ("Operations Planning", "Operations"),
    "SK-OPS-02": ("Process Improvement", "Operations"),
    "SK-OPS-03": ("Team Leadership", "Leadership"),
    "SK-OPS-04": ("Scheduling", "Operations"),
    "SK-OPS-05": ("Customer Communication", "Communication"),
    "SK-OPS-06": ("Process Automation", "Technology"),

    # Sales
    "SK-SAL-01": ("Sales Strategy", "Sales"),
    "SK-SAL-02": ("Account Management", "Sales"),
    "SK-SAL-03": ("Negotiation", "Commercial"),
    "SK-SAL-04": ("CRM Management", "Technology"),
    "SK-SAL-05": ("Sales Forecasting", "Analytics"),
    "SK-SAL-06": ("Enterprise Selling", "Sales"),

    # Customer Service
    "SK-CS-01": ("Customer Service", "Customer Experience"),
    "SK-CS-02": ("Complaint Resolution", "Customer Experience"),
    "SK-CS-03": ("Call Handling", "Customer Experience"),
    "SK-CS-04": ("CRM Management", "Technology"),
    "SK-CS-05": ("Problem Solving", "General"),
    "SK-CS-06": ("Customer Analytics", "Analytics"),

    # Supply Chain
    "SK-SC-01": ("Inventory Management", "Supply Chain"),
    "SK-SC-02": ("Logistics", "Supply Chain"),
    "SK-SC-03": ("Warehouse Operations", "Supply Chain"),
    "SK-SC-04": ("Supply Planning", "Supply Chain"),
    "SK-SC-05": ("Demand Forecasting", "Analytics"),
    "SK-SC-06": ("Supply Chain Risk", "Risk"),

    # Engineering
    "SK-ENG-01": ("Mechanical Engineering", "Engineering"),
    "SK-ENG-02": ("Electrical Engineering", "Engineering"),
    "SK-ENG-03": ("Maintenance", "Engineering"),
    "SK-ENG-04": ("Troubleshooting", "Engineering"),
    "SK-ENG-05": ("Safety Awareness", "Safety"),
    "SK-ENG-06": ("Reliability Engineering", "Engineering"),

    # IT
    "SK-IT-01": ("Cyber Security", "Security"),
    "SK-IT-02": ("Cloud Infrastructure", "Technology"),
    "SK-IT-03": ("Data Analysis", "Analytics"),
    "SK-IT-04": ("Software Development", "Technology"),
    "SK-IT-05": ("IT Support", "Technology"),
    "SK-IT-06": ("Systems Administration", "Technology"),

    # Finance
    "SK-FIN-01": ("Accounting", "Finance"),
    "SK-FIN-02": ("Financial Analysis", "Finance"),
    "SK-FIN-03": ("Budgeting", "Finance"),
    "SK-FIN-04": ("Financial Reporting", "Finance"),
    "SK-FIN-05": ("Audit", "Finance"),
    "SK-FIN-06": ("Treasury Management", "Finance"),

    # HR
    "SK-HR-01": ("Recruitment", "Human Resources"),
    "SK-HR-02": ("HR Operations", "Human Resources"),
    "SK-HR-03": ("Payroll", "Human Resources"),
    "SK-HR-04": ("Employee Relations", "Human Resources"),
    "SK-HR-05": ("HR Analytics", "Analytics"),
    "SK-HR-06": ("Succession Planning", "Human Resources"),

    # Procurement
    "SK-PRO-01": ("Procurement", "Procurement"),
    "SK-PRO-02": ("Contract Management", "Procurement"),
    "SK-PRO-03": ("Supplier Management", "Procurement"),
    "SK-PRO-04": ("Purchasing", "Procurement"),
    "SK-PRO-05": ("Negotiation", "Commercial"),
    "SK-PRO-06": ("Strategic Sourcing", "Procurement"),

    # Marketing
    "SK-MKT-01": ("Digital Marketing", "Marketing"),
    "SK-MKT-02": ("Brand Management", "Marketing"),
    "SK-MKT-03": ("Content Marketing", "Marketing"),
    "SK-MKT-04": ("Campaign Management", "Marketing"),
    "SK-MKT-05": ("Marketing Analytics", "Analytics"),
    "SK-MKT-06": ("Marketing Automation", "Technology"),

    # HSE
    "SK-HSE-01": ("HSE Management", "Safety"),
    "SK-HSE-02": ("Safety", "Safety"),
    "SK-HSE-03": ("Risk Assessment", "Risk"),
    "SK-HSE-04": ("Environmental Compliance", "Compliance"),
    "SK-HSE-05": ("Incident Management", "Safety"),
    "SK-HSE-06": ("Industrial Hygiene", "Safety"),

    # Legal
    "SK-LEG-01": ("Legal Research", "Legal"),
    "SK-LEG-02": ("Contract Law", "Legal"),
    "SK-LEG-03": ("Compliance", "Compliance"),
    "SK-LEG-04": ("Legal Drafting", "Legal"),
    "SK-LEG-05": ("Risk Management", "Risk"),
    "SK-LEG-06": ("Data Privacy Law", "Legal"),
}


# ---------------------------------------------------------
# Department requirements
#
# Format:
# department_id:
#   [
#       (skill_id, required_headcount, is_critical)
#   ]
#
# Each department intentionally contains one MISSING skill.
# ---------------------------------------------------------

DEPARTMENT_REQUIREMENTS = {
    "DEP-01": [
        ("SK-OPS-01", 3, 1),
        ("SK-OPS-02", 2, 1),
        ("SK-OPS-03", 2, 1),
        ("SK-OPS-04", 3, 0),
        ("SK-OPS-05", 2, 0),
        ("SK-OPS-06", 1, 0),  # MISSING
    ],
    "DEP-02": [
        ("SK-SAL-01", 2, 1),
        ("SK-SAL-02", 3, 1),
        ("SK-SAL-03", 2, 1),
        ("SK-SAL-04", 2, 0),
        ("SK-SAL-05", 2, 0),
        ("SK-SAL-06", 1, 0),  # MISSING
    ],
    "DEP-03": [
        ("SK-CS-01", 3, 1),
        ("SK-CS-02", 2, 1),
        ("SK-CS-03", 4, 0),
        ("SK-CS-04", 2, 0),
        ("SK-CS-05", 2, 0),
        ("SK-CS-06", 1, 0),  # MISSING
    ],
    "DEP-04": [
        ("SK-SC-01", 3, 1),
        ("SK-SC-02", 2, 1),
        ("SK-SC-03", 4, 1),
        ("SK-SC-04", 2, 0),
        ("SK-SC-05", 2, 0),
        ("SK-SC-06", 1, 0),  # MISSING
    ],
    "DEP-05": [
        ("SK-ENG-01", 2, 1),
        ("SK-ENG-02", 2, 1),
        ("SK-ENG-03", 4, 1),
        ("SK-ENG-04", 3, 0),
        ("SK-ENG-05", 3, 0),
        ("SK-ENG-06", 1, 0),  # MISSING
    ],
    "DEP-06": [
        ("SK-IT-01", 1, 1),  # MISSING - demo headline
        ("SK-IT-02", 3, 1),
        ("SK-IT-03", 2, 0),
        ("SK-IT-04", 6, 1),  # LOW expected
        ("SK-IT-05", 5, 0),
        ("SK-IT-06", 4, 1),
    ],
    "DEP-07": [
        ("SK-FIN-01", 4, 1),
        ("SK-FIN-02", 2, 1),
        ("SK-FIN-03", 2, 1),
        ("SK-FIN-04", 3, 0),
        ("SK-FIN-05", 2, 0),
        ("SK-FIN-06", 1, 0),  # MISSING
    ],
    "DEP-08": [
        ("SK-HR-01", 2, 1),
        ("SK-HR-02", 3, 1),
        ("SK-HR-03", 2, 1),
        ("SK-HR-04", 2, 0),
        ("SK-HR-05", 2, 0),
        ("SK-HR-06", 1, 0),  # MISSING
    ],
    "DEP-09": [
        ("SK-PRO-01", 3, 1),
        ("SK-PRO-02", 2, 1),
        ("SK-PRO-03", 2, 1),
        ("SK-PRO-04", 4, 0),
        ("SK-PRO-05", 2, 0),
        ("SK-PRO-06", 1, 0),  # MISSING
    ],
    "DEP-10": [
        ("SK-MKT-01", 2, 1),
        ("SK-MKT-02", 2, 1),
        ("SK-MKT-03", 3, 0),
        ("SK-MKT-04", 2, 0),
        ("SK-MKT-05", 2, 0),
        ("SK-MKT-06", 1, 0),  # MISSING
    ],
    "DEP-11": [
        ("SK-HSE-01", 2, 1),
        ("SK-HSE-02", 4, 1),
        ("SK-HSE-03", 2, 1),
        ("SK-HSE-04", 2, 0),
        ("SK-HSE-05", 2, 0),
        ("SK-HSE-06", 1, 0),  # MISSING
    ],
    "DEP-12": [
        ("SK-LEG-01", 2, 1),
        ("SK-LEG-02", 2, 1),
        ("SK-LEG-03", 2, 1),
        ("SK-LEG-04", 2, 0),
        ("SK-LEG-05", 2, 0),
        ("SK-LEG-06", 1, 0),  # MISSING
    ],
}


# ---------------------------------------------------------
# Synthetic mapping from actual job titles to skills
# ---------------------------------------------------------

TITLE_SKILL_RULES = {
    # Operations
    "Operations Assistant": ["SK-OPS-01", "SK-OPS-04", "SK-OPS-05"],
    "Operations Director": ["SK-OPS-01", "SK-OPS-02", "SK-OPS-03"],
    "Operations Manager": ["SK-OPS-01", "SK-OPS-02", "SK-OPS-03"],
    "Operations Officer": ["SK-OPS-01", "SK-OPS-04", "SK-OPS-05"],
    "Shift Supervisor": ["SK-OPS-03", "SK-OPS-04", "SK-OPS-05"],

    # Sales
    "Key Account Manager": ["SK-SAL-02", "SK-SAL-03", "SK-SAL-05"],
    "Sales Director": ["SK-SAL-01", "SK-SAL-02", "SK-SAL-03"],
    "Sales Manager": ["SK-SAL-01", "SK-SAL-02", "SK-SAL-05"],
    "Sales Representative": ["SK-SAL-02", "SK-SAL-03"],
    "Senior Sales Executive": ["SK-SAL-01", "SK-SAL-03", "SK-SAL-05"],

    # Customer Service
    "Call Center Agent": ["SK-CS-01", "SK-CS-03", "SK-CS-05"],
    "Complaints Analyst": ["SK-CS-02", "SK-CS-05", "SK-CS-06"],
    "Customer Service Manager": ["SK-CS-01", "SK-CS-02", "SK-CS-05"],
    "Customer Service Specialist": ["SK-CS-01", "SK-CS-03", "SK-CS-05"],

    # Supply Chain
    "Inventory Analyst": ["SK-SC-01", "SK-SC-05"],
    "Logistics Coordinator": ["SK-SC-02", "SK-SC-04"],
    "Supply Chain Manager": ["SK-SC-01", "SK-SC-02", "SK-SC-04"],
    "Warehouse Clerk": ["SK-SC-01", "SK-SC-03"],

    # Engineering
    "Electrical Engineer": ["SK-ENG-02", "SK-ENG-04"],
    "Engineering Manager": ["SK-ENG-01", "SK-ENG-02", "SK-ENG-03"],
    "Maintenance Supervisor": ["SK-ENG-03", "SK-ENG-04", "SK-ENG-05"],
    "Maintenance Technician": ["SK-ENG-03", "SK-ENG-04", "SK-ENG-05"],
    "Mechanical Engineer": ["SK-ENG-01", "SK-ENG-04"],

    # IT
    "Data Analyst": ["SK-IT-03"],
    "IT Manager": ["SK-IT-02", "SK-IT-05", "SK-IT-06"],
    "IT Support Technician": ["SK-IT-05"],
    "Software Engineer": ["SK-IT-04"],
    "Systems Administrator": ["SK-IT-02", "SK-IT-06"],

    # Finance
    "Accountant": ["SK-FIN-01", "SK-FIN-04"],
    "Chief Accountant": ["SK-FIN-01", "SK-FIN-04", "SK-FIN-05"],
    "Finance Manager": ["SK-FIN-01", "SK-FIN-02", "SK-FIN-03"],
    "Financial Analyst": ["SK-FIN-02", "SK-FIN-03"],
    "Senior Accountant": ["SK-FIN-01", "SK-FIN-04"],

    # HR
    "HR Business Partner": ["SK-HR-02", "SK-HR-04", "SK-HR-05"],
    "HR Coordinator": ["SK-HR-02", "SK-HR-04"],
    "HR Manager": ["SK-HR-02", "SK-HR-04", "SK-HR-05"],
    "Payroll Specialist": ["SK-HR-03", "SK-HR-02"],
    "Recruitment Specialist": ["SK-HR-01", "SK-HR-02"],

    # Procurement
    "Buyer": ["SK-PRO-01", "SK-PRO-04", "SK-PRO-05"],
    "Contracts Specialist": ["SK-PRO-02", "SK-PRO-05"],
    "Procurement Manager": ["SK-PRO-01", "SK-PRO-02", "SK-PRO-03"],
    "Purchasing Officer": ["SK-PRO-01", "SK-PRO-04"],

    # Marketing
    "Brand Specialist": ["SK-MKT-02", "SK-MKT-03"],
    "Digital Marketing Specialist": ["SK-MKT-01", "SK-MKT-05"],
    "Marketing Coordinator": ["SK-MKT-03", "SK-MKT-04"],
    "Marketing Manager": ["SK-MKT-01", "SK-MKT-02", "SK-MKT-04"],

    # HSE
    "HSE Manager": ["SK-HSE-01", "SK-HSE-03", "SK-HSE-05"],
    "HSE Specialist": ["SK-HSE-01", "SK-HSE-03", "SK-HSE-04"],
    "HSE Supervisor": ["SK-HSE-02", "SK-HSE-03", "SK-HSE-05"],
    "Safety Officer": ["SK-HSE-02", "SK-HSE-05"],

    # Legal
    "Compliance Officer": ["SK-LEG-03", "SK-LEG-05"],
    "Legal Assistant": ["SK-LEG-01", "SK-LEG-04"],
    "Legal Counsel": ["SK-LEG-01", "SK-LEG-02", "SK-LEG-04"],
    "Legal Manager": ["SK-LEG-01", "SK-LEG-03", "SK-LEG-05"],
}


def seed_skills(conn) -> None:
    """Insert synthetic skill catalogue."""

    for skill_id, (skill_name, category) in SKILLS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO skills (
                skill_id,
                skill_name,
                category
            )
            VALUES (?, ?, ?)
            """,
            (
                skill_id,
                skill_name,
                category,
            ),
        )


def seed_department_requirements(conn) -> None:
    """Insert department skill requirements."""

    for department_id, requirements in DEPARTMENT_REQUIREMENTS.items():
        for skill_id, minimum_headcount, is_critical in requirements:
            conn.execute(
                """
                INSERT OR IGNORE INTO department_requirements (
                    department_id,
                    skill_id,
                    minimum_headcount,
                    is_critical
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    department_id,
                    skill_id,
                    minimum_headcount,
                    is_critical,
                ),
            )


def seed_skill_job_titles(conn) -> None:
    """
    Map each skill to the job titles that plausibly carry it.

    No per-employee assessment or proficiency score is stored.
    Department headcount for a skill is derived at query time by
    counting employees whose job_title appears here.
    """

    for job_title, skill_ids in TITLE_SKILL_RULES.items():
        for skill_id in skill_ids:
            conn.execute(
                """
                INSERT OR IGNORE INTO skill_job_titles (
                    skill_id,
                    job_title
                )
                VALUES (?, ?)
                """,
                (
                    skill_id,
                    job_title,
                ),
            )


def main() -> None:
    conn = get_connection()

    try:
        ensure_experience_gap_tables(conn)

        seed_skills(conn)
        seed_department_requirements(conn)
        seed_skill_job_titles(conn)

        conn.commit()

        print("Experience Gap synthetic seed completed.")
        print("Tables populated:")
        print(" - skills")
        print(" - department_requirements")
        print(" - skill_job_titles")
        print()
        print("NOTE: Skill coverage is derived from job_title, not an")
        print("individual employee assessment or proficiency score.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()