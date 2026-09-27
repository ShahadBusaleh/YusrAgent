"""Offline checks for app/rag/query_expansion.py (no LLM, no Qdrant)."""

import pytest

from app.rag.query_expansion import expand_query


@pytest.mark.parametrize("query", [
    "Can an employee carry forward unused annual leave to the next year?",
    "How many annual leave days do I have remaining, and can I carry them forward?",
    "Can I carry over my leave?",
])
def test_carry_forward_adds_postponement_terms(query):
    assert "postpone" in expand_query(query)


@pytest.mark.parametrize("query", [
    "What must an employee do when they get sick and miss work?",
    "I'm sick, who should I notify?",
])
def test_sick_procedure_adds_notification_terms(query):
    assert "medical certificate" in expand_query(query)


@pytest.mark.parametrize("query", [
    "How many paid sick leave days do I get?",
    "What is the sick leave pay after 30 days?",
    "What happens to unused leave at termination?",
    "What is the duration of annual leave?",
])
def test_other_questions_unchanged(query):
    assert expand_query(query) == query


@pytest.mark.parametrize("query, term", [
    ("How can an establishment access the Wage Protection System?", "Mudad"),
    ("What information is included in the employee payment details in the Wage Protection file?", "bank identifier"),
    ("What are the different types of wage components that can be included in a Wage Protection file?", "housing allowance"),
])
def test_wps_questions_add_row_vocabulary(query, term):
    assert term in expand_query(query)


def _chunk(cid, table, category, text="unpaid leave approval"):
    return {"id": cid, "source_table": table, "text": f"Category: {category}\nRule: {text}"}


def test_company_policy_takes_last_slot():
    from app.agents.consultant_agent import _filter_relevant_chunks

    chunks = [_chunk(f"LAW05{i}", "saudi_labor_law", "Unpaid Leave") for i in range(4)]
    chunks.append(_chunk("AAM-POL-020", "company_policies", "Leave"))
    kept = _filter_relevant_chunks("Who must approve unpaid leave?", chunks)
    assert [c["id"] for c in kept] == ["LAW050", "LAW051", "AAM-POL-020"]


def test_list_question_keeps_same_category_rows():
    from app.agents.consultant_agent import _filter_relevant_chunks

    chunks = [_chunk(f"WPS02{i}", "wps", "Wage data", "wage components basic housing") for i in range(6)]
    chunks.insert(3, _chunk("WPS001", "wps", "General", "wage components program"))
    kept = _filter_relevant_chunks("What are the wage components?", chunks)
    assert [c["id"] for c in kept] == ["WPS020", "WPS021", "WPS022", "WPS023", "WPS024"]
