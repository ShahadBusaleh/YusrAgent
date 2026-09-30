"""Database helpers for Experience Gap Insight."""

from __future__ import annotations

import sqlite3


def ensure_experience_gap_tables(
    conn: sqlite3.Connection,
) -> None:
    """Create only the three additive Experience Gap tables."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            category TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS department_requirements (
            department_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            minimum_headcount INTEGER NOT NULL,
            is_critical INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (department_id, skill_id),
            FOREIGN KEY (department_id)
                REFERENCES departments(department_id),
            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_job_titles (
            skill_id TEXT NOT NULL,
            job_title TEXT NOT NULL,
            PRIMARY KEY (skill_id, job_title),
            FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id)
        )
        """
    )

    conn.commit()


def _candidate_job_titles(
    conn: sqlite3.Connection,
    department_id: str,
    skill_id: str,
) -> list[dict]:
    """
    Job titles in the department that are the "closest experience" to
    the given (missing) skill — titles that already carry some other
    skill in the same category, ranked by how many employees hold
    that title. Used only to build a suggestion, not to change status.

    Plain "not already covering this exact skill" isn't a real
    closeness signal on its own: when a skill has zero job-title
    mappings anywhere (nobody, in any department, has ever been
    tagged with it), that test matches every title in the department
    and falsely claims the whole department is "close" to it — e.g.
    CRM Management (Technology) would list every Sales title as a
    natural fit, which it isn't. Requiring the candidate title to
    already carry a sibling skill in the same category grounds
    "closest" in real adjacency instead: Sales titles that already
    handle another Sales-category skill are a real fit for a new
    Sales-category gap, but they're never proposed for a Technology
    gap they have no connection to.

    Same-category alone misses obvious cross-category fits, though
    (Systems Administrators for Cyber Security, Supply Planning staff
    for Supply Chain Risk), which made Team Insights recommend external
    hiring for gaps the department can clearly grow into. A title also
    counts when it carries one of the explicitly related skills in
    `_RELATED_SKILLS`.

    Returns [{"job_title": ..., "headcount": ...}, ...] for every such
    title, or an empty list when nothing in the department has any
    adjacency — the caller then falls back to a hiring recommendation
    instead of naming an unrelated position.
    """

    skill_row = conn.execute(
        "SELECT skill_name FROM skills WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    related = _RELATED_SKILLS.get(skill_row[0], ()) if skill_row else ()
    related_placeholders = ",".join("?" for _ in related) or "NULL"

    rows = conn.execute(
        f"""
        SELECT e.job_title, COUNT(*) AS headcount
        FROM employees e
        WHERE e.department_id = ?
          AND e.job_title IN (
              SELECT DISTINCT sjt.job_title
              FROM skill_job_titles sjt
              JOIN skills s2 ON s2.skill_id = sjt.skill_id
              WHERE sjt.skill_id != ?
                AND (
                    s2.category = (
                        SELECT category FROM skills WHERE skill_id = ?
                    )
                    OR s2.skill_name IN ({related_placeholders})
                )
          )
        GROUP BY e.job_title
        ORDER BY headcount DESC, e.job_title ASC
        """,
        (department_id, skill_id, skill_id, *related),
    ).fetchall()

    return [dict(row) for row in rows]


# Cross-category adjacency: gap skill -> existing skills whose holders
# are a realistic internal pool to grow into it. Keyed by skill_name
# (stable across reseeds, unlike generated ids).
_RELATED_SKILLS: dict[str, tuple[str, ...]] = {
    "Cyber Security": ("Systems Administration", "Cloud Infrastructure"),
    "CRM Management": ("Sales Forecasting", "Customer Analytics"),
    "Marketing Automation": ("Digital Marketing", "Marketing Analytics"),
    "Process Automation": ("Process Improvement",),
    "Supply Chain Risk": ("Supply Planning", "Demand Forecasting"),
}


def summarize_candidate_titles(candidates: list[dict]) -> list[dict]:
    """[{"job_title", "headcount"}] in the candidates' ranked order."""

    counts: dict[str, int] = {}
    for c in candidates:
        counts[c["job_title"]] = counts.get(c["job_title"], 0) + 1
    return [{"job_title": t, "headcount": n} for t, n in counts.items()]


_CATEGORY_TRAINING_HINTS: dict[str, str] = {
    "Analytics": "a data-analysis/BI certification (e.g. SQL, Power BI) or an internal data bootcamp",
    "Commercial": "commercial/contract-management training or a mentorship rotation with the commercial team",
    "Communication": "a communication and presentation-skills workshop",
    "Compliance": "a compliance certification course and shadowing the compliance team",
    "Customer Experience": "customer-experience/service-excellence training",
    "Engineering": "an engineering certification or a hands-on rotation with senior engineers",
    "Finance": "a finance/accounting certification (e.g. cost accounting, CFA foundations)",
    "General": "an internal cross-training rotation",
    "Human Resources": "an HR-practices certification (e.g. CIPD, SHRM) or shadowing the HR team",
    "Leadership": "a leadership/management development program",
    "Legal": "a legal and contracts fundamentals course",
    "Marketing": "a digital-marketing certification or a rotation with the marketing team",
    "Operations": "an operations-management training program",
    "Procurement": "a procurement/supply-chain certification (e.g. CIPS)",
    "Risk": "a risk-management certification course",
    "Safety": "a health & safety certification (e.g. NEBOSH, OSHA)",
    "Sales": "a sales-methodology training program",
    "Security": "a security certification (e.g. CompTIA Security+, CISSP foundations)",
    "Supply Chain": "a supply-chain management certification",
    "Technology": "a technical certification or bootcamp (e.g. cloud, software development)",
}

_DEFAULT_TRAINING_HINT = "a relevant internal or external training course"


def _build_recommendation(
    item: dict,
    candidates: list[dict],
) -> str | None:
    """
    Deterministic, template-based development suggestion for a gap.

    Frames every gap as an internal-growth question first — which
    current employees are positioned to develop into this experience
    title, and how. Only falls back to a hiring recommendation when
    no employee in the department is in an adjacent role at all. Not
    LLM-generated: the inputs are just headcount numbers and job
    titles already in hand, so a template avoids adding an LLM
    dependency to what is otherwise read-only SQL analytics.

    `candidates` is the employee-level list from
    `get_gap_candidate_employees` (already ranked so each employee's
    job title appears in the same "biggest pool first" order the old
    aggregated headcount ranking used).
    """

    if item["status"] == "OK":
        return None

    skill_name = item["skill_name"]
    training_hint = _CATEGORY_TRAINING_HINTS.get(
        item.get("category"), _DEFAULT_TRAINING_HINT
    )

    lead = (
        f"{skill_name} is an experience gap — no employee in "
        f"this department currently holds a role built around it."
    )

    pool_size = len(candidates)

    if candidates:
        titles_seen: list[str] = []
        for c in candidates:
            if c["job_title"] not in titles_seen:
                titles_seen.append(c["job_title"])

        if len(titles_seen) == 1:
            titles = titles_seen[0]
        else:
            titles = f"{', '.join(titles_seen[:-1])}, and {titles_seen[-1]}"

        develop = (
            f" {pool_size} employees across the existing "
            f"{titles} position{'s' if len(titles_seen) > 1 else ''} "
            f"in this department are the most natural fit to grow "
            f"into it — supporting them through {training_hint} "
            f"builds a path to promotion into a {skill_name}-focused "
            f"role."
        )
    else:
        develop = (
            " No employee in this department is in an adjacent role "
            "today, so there is no realistic internal candidate to "
            "develop — recommend hiring externally for this "
            "experience."
        )

    return (lead + develop).strip()


def get_gap_candidate_employees(
    conn: sqlite3.Connection,
    department_id: str,
    skill_id: str,
) -> list[dict]:
    """
    Actual employees behind every "closest experience" job title for a
    gap skill — the same ranking `_candidate_job_titles` already uses
    to build the HR recommendation sentence, resolved to real
    employee rows instead of an aggregated headcount.

    Returns [{"employee_id", "full_name", "job_title"}, ...], ordered
    by the same "biggest pool first" title ranking `_candidate_job_titles`
    uses (then by name within a title) so callers that need the
    ranked title order (e.g. the recommendation sentence) don't have
    to re-rank it themselves.
    """

    candidate_titles = [
        c["job_title"]
        for c in _candidate_job_titles(
            conn,
            department_id,
            skill_id,
        )
    ]

    if not candidate_titles:
        return []

    placeholders = ",".join("?" for _ in candidate_titles)

    rows = conn.execute(
        f"""
        SELECT employee_id, full_name, job_title
        FROM employees
        WHERE department_id = ?
          AND job_title IN ({placeholders})
        ORDER BY full_name
        """,
        (department_id, *candidate_titles),
    ).fetchall()

    title_rank = {title: i for i, title in enumerate(candidate_titles)}

    employees = [dict(row) for row in rows]
    employees.sort(key=lambda e: title_rank[e["job_title"]])

    return employees


def get_department_experience_gap(
    conn: sqlite3.Connection,
    department_id: str,
) -> list[dict]:
    """
    Return required skills and current headcount per department.

    No per-employee proficiency score is used. An employee counts
    toward a skill's current_headcount when their job_title is one
    of the titles mapped to that skill in skill_job_titles.

    Status is derived purely from real employee data (no assumed
    per-skill headcount target):
        MISSING -> current_headcount == 0
        OK      -> current_headcount > 0

    Each MISSING item also carries a "recommendation" string (hire
    vs. upskill-from-role suggestion). OK items get None.
    """

    rows = conn.execute(
        """
        SELECT
            dr.department_id,
            dr.skill_id,
            s.skill_name,
            s.category,
            dr.is_critical,
            COUNT(DISTINCT e.employee_id) AS current_headcount
        FROM department_requirements dr
        JOIN skills s
            ON s.skill_id = dr.skill_id
        LEFT JOIN skill_job_titles sjt
            ON sjt.skill_id = dr.skill_id
        LEFT JOIN employees e
            ON e.department_id = dr.department_id
            AND e.job_title = sjt.job_title
        WHERE dr.department_id = ?
        GROUP BY
            dr.department_id,
            dr.skill_id,
            s.skill_name,
            s.category,
            dr.is_critical
        """,
        (department_id,),
    ).fetchall()

    results = []

    for row in rows:
        item = dict(row)

        current = int(item["current_headcount"] or 0)
        status = "MISSING" if current == 0 else "OK"

        item["current_headcount"] = current
        item["status"] = status

        if status == "OK":
            item["recommendation"] = None
            item["hire_recommended"] = False
            item["_candidate_employees"] = []
        else:
            candidates = get_gap_candidate_employees(
                conn,
                department_id,
                item["skill_id"],
            )
            item["recommendation"] = _build_recommendation(
                item,
                candidates,
            )
            item["hire_recommended"] = not bool(candidates)
            # Internal only - stripped before this leaves the API
            # boundary (see experience_gap router). Lets callers like
            # Growth Opportunities reuse the same candidate lookup
            # instead of re-querying it per skill.
            item["_candidate_employees"] = candidates

        results.append(item)

    status_order = {
        "MISSING": 0,
        "OK": 1,
    }

    results.sort(
        key=lambda item: (
            -int(item["is_critical"]),
            status_order[item["status"]],
            item["skill_name"],
        )
    )

    return results


def list_departments(conn: sqlite3.Connection) -> list[dict]:
    """Return all departments as {department_id, department_name}, name-sorted."""

    rows = conn.execute(
        """
        SELECT department_id, department_name
        FROM departments
        ORDER BY department_name
        """
    ).fetchall()

    return [dict(row) for row in rows]
