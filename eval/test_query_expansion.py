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
