from __future__ import annotations

import re
import time
from typing import Any

from openai import OpenAI

from app.agents.base import BaseAgent
from app.config import get_settings
from app.rag.retrieve import retrieve
from app.security.governance import (
    detect_prompt_injection,
    mask_pii,
    sanitize_input,
    validate_output,
)


# =========================================================
# CONSTANTS
# =========================================================

SATISFIED = "SATISFIED"
NOT_SATISFIED = "NOT_SATISFIED"
UNKNOWN = "UNKNOWN"

APPLICABLE = "APPLICABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"
POSSIBLY_APPLICABLE = "POSSIBLY_APPLICABLE"
GENERAL_EVIDENCE = "GENERAL_EVIDENCE"

SUPPORTED = "SUPPORTED"
BLOCKED = "BLOCKED"
NEEDS_INFORMATION = "NEEDS_INFORMATION"
INFORMATIONAL = "INFORMATIONAL"

REQUEST_BLOCKER = "REQUEST_BLOCKER"
SOURCE_APPLICABILITY = "SOURCE_APPLICABILITY"

SUCCESS = "SUCCESS"
FAILED = "FAILED"
PASSED = "PASSED"


STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were",
    "how", "what", "when", "where", "why", "who",
    "can", "could", "should", "would",
    "do", "does", "did",
    "of", "to", "for", "in", "on", "at", "by",
    "with", "and", "or", "from",
    "this", "that", "these", "those",
    "employee", "employees",
    "paid", "payment",
}


# =========================================================
# OBSERVABILITY
# =========================================================

def _add_trace(
    trace: list[dict],
    phase: str,
    stage: str,
    status: str,
    **details: Any,
) -> None:
    """
    Add a safe observability event.

    Records workflow state and execution results only.
    It does not store hidden reasoning or chain-of-thought.
    """

    event = {
        "phase": phase,
        "stage": stage,
        "status": status,
    }

    for key, value in details.items():
        if value is not None:
            event[key] = value

    trace.append(event)


def _build_error(
    error_type: str,
    stage: str,
    message: str,
    retryable: bool = False,
) -> dict:
    """Return a standardized Consultant error."""

    return {
        "type": error_type,
        "stage": stage,
        "message": message,
        "retryable": retryable,
    }


def _error_response(
    recommendation: str,
    error: dict,
    trace: list[dict],
    *,
    conflicts: list[str] | None = None,
    sources: list[dict] | None = None,
    policy_analysis: dict | None = None,
    condition_analysis: dict | None = None,
    applicability: list[dict] | None = None,
    request_assessment: dict | None = None,
) -> dict:
    """Return one consistent output structure for failures."""

    return {
        "recommendation": recommendation,
        "conflicts": conflicts or [],
        "sources": sources or [],
        "policy_analysis": policy_analysis or {},
        "condition_analysis": condition_analysis or {},
        "applicability": applicability or [],
        "request_assessment": request_assessment or {
            "status": NEEDS_INFORMATION,
            "blockers": [],
            "missing_information": [],
            "notes": [],
        },
        "success": False,
        "error": error,
        "trace": trace,
    }


# =========================================================
# BASIC HELPERS
# =========================================================

def _unique_strings(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for value in values
            if value
        )
    )


def _safe_text(value: Any) -> str:
    if value is None:
        return ""

    return mask_pii(str(value))


def _extract_tokens(text: str) -> set[str]:
    """
    Tokenization used only for retrieval reranking.

    Token overlap is never treated as proof that
    a policy condition is satisfied.
    """

    return {
        token
        for token in re.findall(
            r"[a-zA-Z0-9\u0600-\u06FF]{3,}",
            (text or "").lower(),
        )
        if token not in STOP_WORDS
    }


def _parse_chunk_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}

    for line in (text or "").splitlines():
        if ":" not in line:
            continue

        key, _, value = line.partition(":")

        key = key.strip().lower()
        value = value.strip()

        if key and value:
            fields[key] = value

    return fields


def _normalize_hr_facts(hr_facts: dict) -> dict[str, Any]:
    """
    Normalize facts received from HR Agent.

    Consultant does not access the employee database directly.
    """

    if not isinstance(hr_facts, dict):
        return {}

    normalized: dict[str, Any] = {}

    for key, value in hr_facts.items():
        if value is None:
            continue

        normalized[str(key).strip().lower()] = value

    return normalized


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        match = re.search(
            r"-?\d+(?:\.\d+)?",
            value.replace(",", ""),
        )

        if match:
            try:
                return float(match.group())
            except ValueError:
                return None

    return None


# =========================================================
# RAG RELEVANCE RERANKING
# =========================================================

def _calculate_relevance_score(
    query: str,
    chunk: dict,
) -> float:
    query_tokens = _extract_tokens(query)

    if not query_tokens:
        return 1.0

    chunk_text = chunk.get("text", "") or ""
    fields = _parse_chunk_fields(chunk_text)

    text_tokens = _extract_tokens(chunk_text)
    rule_tokens = _extract_tokens(fields.get("rule", ""))
    condition_tokens = _extract_tokens(
        fields.get("conditions", "")
    )

    text_overlap = len(query_tokens & text_tokens)
    rule_overlap = len(query_tokens & rule_tokens)
    condition_overlap = len(query_tokens & condition_tokens)

    return (
        text_overlap * 1.0
        + rule_overlap * 2.0
        + condition_overlap * 1.0
    )


def _filter_relevant_chunks(
    query: str,
    chunks: list[dict],
    min_score: float = 2.0,
    max_chunks: int = 4,
) -> list[dict]:
    if not chunks:
        return []

    ranked_chunks: list[dict] = []

    for chunk in chunks:
        relevance_score = _calculate_relevance_score(
            query=query,
            chunk=chunk,
        )

        enriched_chunk = dict(chunk)
        enriched_chunk[
            "consultant_relevance_score"
        ] = relevance_score

        ranked_chunks.append(enriched_chunk)

    ranked_chunks.sort(
        key=lambda item: item[
            "consultant_relevance_score"
        ],
        reverse=True,
    )

    relevant_chunks = [
        chunk
        for chunk in ranked_chunks
        if chunk[
            "consultant_relevance_score"
        ] >= min_score
    ]

    # Preserve the strongest semantic result if the
    # lexical reranker removes everything.
    if not relevant_chunks:
        return ranked_chunks[:1]

    return relevant_chunks[:max_chunks]


# =========================================================
# RETRIEVED CONTENT SECURITY
# =========================================================

def _filter_safe_chunks(
    chunks: list[dict],
) -> list[dict]:
    safe_chunks: list[dict] = []

    for chunk in chunks:
        text = chunk.get("text", "") or ""

        if detect_prompt_injection(text):
            continue

        safe_chunks.append(chunk)

    return safe_chunks


# =========================================================
# POLICY ANALYSIS
# =========================================================

def _analyze_policy_chunks(
    chunks: list[dict],
) -> dict:
    rules: list[dict] = []
    conditions: list[dict] = []
    exceptions: list[dict] = []

    for chunk in chunks:
        source_id = chunk.get("id")

        fields = _parse_chunk_fields(
            chunk.get("text", "")
        )

        rule = fields.get("rule")
        condition = fields.get("conditions")
        exception = fields.get("exceptions")

        if rule:
            rules.append({
                "source_id": source_id,
                "text": _safe_text(rule),
            })

        if condition:
            conditions.append({
                "source_id": source_id,
                "text": _safe_text(condition),
            })

        if exception:
            exceptions.append({
                "source_id": source_id,
                "text": _safe_text(exception),
            })

    return {
        "rules": rules,
        "conditions": conditions,
        "exceptions": exceptions,
    }


# =========================================================
# CONDITION EVALUATORS
# =========================================================

def _extract_required_years(
    condition: str,
) -> float | None:
    lowered = condition.lower()

    digit_match = re.search(
        r"(\d+(?:\.\d+)?)\s+"
        r"(?:consecutive\s+)?years?",
        lowered,
    )

    if digit_match:
        return float(digit_match.group(1))

    word_numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }

    for word, number in word_numbers.items():
        pattern = (
            rf"\b{word}\b\s+"
            rf"(?:consecutive\s+)?years?"
        )

        if re.search(pattern, lowered):
            return float(number)

    return None


def _evaluate_years_of_service_condition(
    condition: str,
    hr_facts: dict,
) -> dict | None:
    lowered = condition.lower()

    if (
        "year" not in lowered
        or "service" not in lowered
    ):
        return None

    required_years = _extract_required_years(condition)

    if required_years is None:
        return {
            "status": UNKNOWN,
            "effect": SOURCE_APPLICABILITY,
            "reason": (
                "The condition references years of "
                "service, but the required threshold "
                "could not be determined safely."
            ),
        }

    employee_years = _as_number(
        hr_facts.get("years_of_service")
    )

    if employee_years is None:
        return {
            "status": UNKNOWN,
            "effect": SOURCE_APPLICABILITY,
            "reason": (
                "Employee years of service were not "
                "provided by the HR Agent."
            ),
        }

    if employee_years >= required_years:
        return {
            "status": SATISFIED,
            "effect": SOURCE_APPLICABILITY,
            "reason": (
                f"Employee has {employee_years:g} "
                f"years of service; the condition "
                f"requires {required_years:g} years."
            ),
        }

    return {
        "status": NOT_SATISFIED,
        "effect": SOURCE_APPLICABILITY,
        "reason": (
            f"Employee has {employee_years:g} "
            f"years of service; the condition "
            f"requires {required_years:g} years."
        ),
    }


def _evaluate_condition(
    condition: dict,
    hr_facts: dict,
) -> dict:
    source_id = condition.get("source_id")
    condition_text = (
        condition.get("text", "") or ""
    )

    evaluators = (
        _evaluate_years_of_service_condition,
    )

    for evaluator in evaluators:
        result = evaluator(
            condition_text,
            hr_facts,
        )

        if result is not None:
            return {
                "source_id": source_id,
                "condition": condition_text,
                "status": result["status"],
                "effect": result["effect"],
                "reason": result["reason"],
            }

    # Conservative fallback.
    return {
        "source_id": source_id,
        "condition": condition_text,
        "status": UNKNOWN,
        "effect": SOURCE_APPLICABILITY,
        "reason": (
            "The available HR facts do not provide "
            "a deterministic basis to verify "
            "this condition."
        ),
    }


# =========================================================
# REQUEST FACT CHECKS
# =========================================================

def _evaluate_leave_balance(
    query: str,
    hr_facts: dict,
) -> dict | None:
    requested_days = _as_number(
        hr_facts.get("requested_days")
    )

    remaining_balance = _as_number(
        hr_facts.get("remaining_balance")
    )

    if (
        requested_days is None
        or remaining_balance is None
    ):
        return None

    query_tokens = _extract_tokens(query)

    leave_related = bool(
        {
            "leave",
            "annual",
            "vacation",
        }
        & query_tokens
    )

    if not leave_related:
        return None

    if requested_days <= remaining_balance:
        return {
            "check": "leave_balance",
            "status": SATISFIED,
            "effect": REQUEST_BLOCKER,
            "reason": (
                f"Requested leave is "
                f"{requested_days:g} days and "
                f"the recorded remaining balance "
                f"is {remaining_balance:g} days."
            ),
        }

    return {
        "check": "leave_balance",
        "status": NOT_SATISFIED,
        "effect": REQUEST_BLOCKER,
        "reason": (
            f"Requested leave is "
            f"{requested_days:g} days but "
            f"the recorded remaining balance "
            f"is only {remaining_balance:g} days."
        ),
    }


# =========================================================
# CONDITION ANALYSIS
# =========================================================

def _analyze_conditions(
    policy_analysis: dict,
    hr_facts: dict,
    query: str,
) -> dict:
    normalized_facts = _normalize_hr_facts(
        hr_facts
    )

    assessments = [
        _evaluate_condition(
            condition=condition,
            hr_facts=normalized_facts,
        )
        for condition in policy_analysis.get(
            "conditions",
            [],
        )
    ]

    fact_checks: list[dict] = []

    balance_check = _evaluate_leave_balance(
        query=query,
        hr_facts=normalized_facts,
    )

    if balance_check:
        fact_checks.append(balance_check)

    return {
        "satisfied": [
            item
            for item in assessments
            if item["status"] == SATISFIED
        ],
        "not_satisfied": [
            item
            for item in assessments
            if item["status"] == NOT_SATISFIED
        ],
        "unknown": [
            item
            for item in assessments
            if item["status"] == UNKNOWN
        ],
        "fact_checks": fact_checks,
    }


# =========================================================
# SOURCE APPLICABILITY
# =========================================================

def _analyze_source_applicability(
    chunks: list[dict],
    condition_analysis: dict,
) -> list[dict]:
    source_ids = _unique_strings(
        [
            str(chunk.get("id"))
            for chunk in chunks
            if chunk.get("id")
        ]
    )

    assessments = (
        condition_analysis.get(
            "satisfied",
            [],
        )
        + condition_analysis.get(
            "not_satisfied",
            [],
        )
        + condition_analysis.get(
            "unknown",
            [],
        )
    )

    results: list[dict] = []

    for source_id in source_ids:
        source_conditions = [
            item
            for item in assessments
            if str(
                item.get("source_id")
            ) == source_id
        ]

        if not source_conditions:
            status = GENERAL_EVIDENCE
            reason = (
                "No structured policy condition "
                "was available for this source."
            )

        elif any(
            item.get("status") == NOT_SATISFIED
            for item in source_conditions
        ):
            status = NOT_APPLICABLE
            reason = (
                "At least one explicit condition "
                "for this source is not satisfied."
            )

        elif all(
            item.get("status") == SATISFIED
            for item in source_conditions
        ):
            status = APPLICABLE
            reason = (
                "All evaluated conditions for "
                "this source are satisfied."
            )

        else:
            status = POSSIBLY_APPLICABLE
            reason = (
                "No condition is disproven, "
                "but one or more conditions "
                "remain unverified."
            )

        results.append({
            "source_id": source_id,
            "status": status,
            "affects_request": False,
            "reason": reason,
        })

    return results


# =========================================================
# REQUEST ASSESSMENT
# =========================================================

def _is_employee_case(
    hr_facts: dict,
) -> bool:
    return bool(
        _normalize_hr_facts(hr_facts)
    )


def _build_request_assessment(
    hr_facts: dict,
    condition_analysis: dict,
    applicability: list[dict],
) -> dict:
    if not _is_employee_case(hr_facts):
        return {
            "status": INFORMATIONAL,
            "blockers": [],
            "missing_information": [],
            "notes": [
                (
                    "No employee-specific HR facts "
                    "were supplied; this is treated "
                    "as an informational policy query."
                )
            ],
        }

    blockers: list[dict] = []

    for fact_check in condition_analysis.get(
        "fact_checks",
        [],
    ):
        if (
            fact_check.get("effect")
            == REQUEST_BLOCKER
            and fact_check.get("status")
            == NOT_SATISFIED
        ):
            blockers.append({
                "type": fact_check.get(
                    "check",
                    "request_check",
                ),
                "reason": fact_check.get(
                    "reason",
                    "",
                ),
            })

    missing_information = [
        {
            "source_id": item.get("source_id"),
            "condition": item.get("condition"),
            "reason": item.get("reason"),
        }
        for item in condition_analysis.get(
            "unknown",
            [],
        )
    ]

    not_applicable_sources = [
        item.get("source_id")
        for item in applicability
        if item.get("status") == NOT_APPLICABLE
    ]

    notes: list[str] = []

    if not_applicable_sources:
        notes.append(
            (
                "Some retrieved rules do not apply "
                "to this employee case, but that "
                "alone does not block the request."
            )
        )

    if blockers:
        status = BLOCKED
    elif missing_information:
        status = NEEDS_INFORMATION
    else:
        status = SUPPORTED

    return {
        "status": status,
        "blockers": blockers,
        "missing_information": missing_information,
        "notes": notes,
    }


# =========================================================
# CONFLICTS
# =========================================================

def _build_conflicts(
    request_assessment: dict,
) -> list[str]:
    conflicts: list[str] = []

    # Only real request blockers become conflicts.
    for blocker in request_assessment.get(
        "blockers",
        [],
    ):
        reason = blocker.get("reason")

        if reason:
            conflicts.append(reason)

    return _unique_strings(conflicts)


# =========================================================
# SOURCE METADATA
# =========================================================

def _build_sources(
    chunks: list[dict],
) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    for chunk in chunks:
        source_id = chunk.get("id")

        if not source_id:
            continue

        source_key = str(source_id)

        if source_key in seen:
            continue

        seen.add(source_key)

        sources.append({
            "id": source_id,
            "text": chunk.get("text"),
            "source_table": chunk.get(
                "source_table"
            ),
            "filename": chunk.get(
                "filename"
            ),
            "score": chunk.get("score"),
            "relevance_score": chunk.get(
                "consultant_relevance_score"
            ),
        })

    return sources


def _format_context(
    chunks: list[dict],
) -> str:
    parts: list[str] = []

    for chunk in chunks:
        source_id = (
            chunk.get("id")
            or "UNKNOWN"
        )

        source_table = (
            chunk.get("source_table")
            or "unknown"
        )

        text = _safe_text(
            chunk.get("text", "")
        )

        parts.append(
            f"[Source: {source_id}]\n"
            f"Source type: {source_table}\n"
            f"{text}"
        )

    return "\n\n".join(parts)


# =========================================================
# CONSULTANT LLM
# =========================================================

def _generate_consultant_recommendation(
    query: str,
    chunks: list[dict],
    hr_facts: dict,
    policy_analysis: dict,
    condition_analysis: dict,
    applicability: list[dict],
    request_assessment: dict,
) -> str:
    settings = get_settings()

    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is not configured "
            "in the local .env file."
        )

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    normalized_facts = _normalize_hr_facts(
        hr_facts
    )

    safe_hr_facts = {
        key: _safe_text(value)
        for key, value
        in normalized_facts.items()
    }

    context = _format_context(chunks)

    system_prompt = """
You are the Consultant Agent in the Yusr Agentic HR System.

Your role is HR policy and compliance advisory only.

A deterministic Python layer has already analyzed:
- employee facts,
- policy conditions,
- source applicability,
- request blockers,
- missing information,
- request assessment status.

You MUST follow those deterministic results.

STRICT RULES:

1. Use only the evidence and facts supplied.

2. Never invent employee facts, policy rules,
   legal rules, eligibility, conditions,
   exceptions, or citations.

3. REQUEST ASSESSMENT is authoritative.
   Do not change its status.

4. If the status is BLOCKED, explain only the
   blockers listed in request_assessment.blockers.

5. A NOT_APPLICABLE source is not automatically
   a reason to reject the employee request.

6. If a rule provides enhanced benefits after a
   service threshold and that threshold is not met,
   explain that the enhanced rule does not apply.
   Do not treat that alone as a request blocker.

7. If status is NEEDS_INFORMATION, explain only
   the information listed in
   request_assessment.missing_information.

8. If status is SUPPORTED, explain what supplied
   evidence supports the request. Do not claim
   final approval.

9. If status is INFORMATIONAL:
   - Answer the general policy question directly
     using retrieved evidence only.
   - Do not invent employee-specific missing information.
   - Do not discuss employee eligibility, request
     blockers, or missing HR facts unless the user
     explicitly asked about a specific employee case.
   - If request_assessment.missing_information is empty,
     do not create a Missing Information section.
   - Focus only on policy rules that directly answer
     the user's question.

10. Distinguish leave balance, policy entitlement,
    source applicability, and final approval.

11. Never convert UNKNOWN into SATISFIED.

12. Never convert NOT_APPLICABLE source evidence
    into REQUEST_BLOCKER unless it is explicitly
    listed as a blocker.

13. Cite policy/legal claims as:
    [Source: SOURCE_ID]

14. Do not expose unnecessary personal information.

15. Never access or modify the HR database,
    submit transactions, approve requests,
    make RBAC decisions, or override the
    Security Layer or Manager Agent.

16. The response structure must agree with the
    deterministic data:
    - If blockers is empty, do not invent blockers.
    - If missing_information is empty, do not invent
      missing information.
    - If there are no relevant non-applicable rules,
      do not create a Non-Applicable Rules section.

17. Keep the recommendation concise and professional.

18. The output is advisory. The Manager Agent
    makes the system-level decision.
""".strip()

    # IMPORTANT:
    # This prompt must exist before the OpenAI call.
    # Its accidental removal caused the previous NameError.
    user_prompt = f"""
EMPLOYEE REQUEST:
{query}

HR FACTS PROVIDED BY HR AGENT:
{safe_hr_facts if safe_hr_facts else "No employee-specific facts provided."}

POLICY ANALYSIS:
{policy_analysis}

CONDITION ANALYSIS:
{condition_analysis}

SOURCE APPLICABILITY:
{applicability}

REQUEST ASSESSMENT:
{request_assessment}

RETRIEVED EVIDENCE:
{context}

Write the Consultant recommendation.

Follow REQUEST ASSESSMENT exactly as provided.

If REQUEST ASSESSMENT status is INFORMATIONAL:
- Answer the general policy question directly.
- Do not invent employee-specific missing information.
- Do not discuss missing HR facts unless the user
  asked about a specific employee case.
- If missing_information is empty, do not create
  a Missing Information section.
- Do not create empty Blockers or
  Non-Applicable Rules sections.
- Focus on retrieved policy evidence that directly
  answers the question.

For employee-specific cases, clearly distinguish:
- supported facts,
- actual blockers,
- missing information,
- non-applicable rules.
""".strip()

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0,
    )

    recommendation = (
        response.choices[0]
        .message.content
        or ""
    ).strip()

    if not recommendation:
        raise RuntimeError(
            "The Consultant LLM returned "
            "an empty response."
        )

    return _safe_text(recommendation)


# =========================================================
# CONSULTANT AGENT
# =========================================================

class ConsultantAgent(BaseAgent):

    def run(
        self,
        input: dict,
    ) -> dict:
        trace: list[dict] = []

        # -------------------------------------------------
        # REASON — determine whether input is processable
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="REASON",
            stage="INPUT_VALIDATION",
            status="STARTED",
        )

        if not isinstance(input, dict):
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="INPUT_VALIDATION",
                status=FAILED,
                error_type="invalid_input",
            )

            return _error_response(
                recommendation="Invalid Consultant input.",
                error=_build_error(
                    error_type="invalid_input",
                    stage="INPUT_VALIDATION",
                    message=(
                        "Consultant input must "
                        "be a dictionary."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=["invalid_input"],
            )

        query = sanitize_input(
            input.get("query", "")
        )

        if not query:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="INPUT_VALIDATION",
                status=FAILED,
                error_type="missing_query",
            )

            return _error_response(
                recommendation=(
                    "No policy question was provided."
                ),
                error=_build_error(
                    error_type="missing_query",
                    stage="INPUT_VALIDATION",
                    message=(
                        "A Consultant query is required."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=["missing_query"],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="INPUT_VALIDATION",
            status=PASSED,
        )

        # -------------------------------------------------
        # ACT — input security
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="ACT",
            stage="INPUT_SECURITY",
            status="STARTED",
        )

        if detect_prompt_injection(query):
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="INPUT_SECURITY",
                status=FAILED,
                error_type="prompt_injection_detected",
            )

            return _error_response(
                recommendation=(
                    "Request could not be processed "
                    "for security reasons."
                ),
                error=_build_error(
                    error_type="prompt_injection_detected",
                    stage="INPUT_SECURITY",
                    message=(
                        "Potential prompt injection "
                        "was detected."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=[
                    "prompt_injection_detected"
                ],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="INPUT_SECURITY",
            status=PASSED,
        )

        # -------------------------------------------------
        # ACT — retrieve policy evidence
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="ACT",
            stage="RETRIEVAL",
            status="STARTED",
        )

        retrieval_start = time.perf_counter()

        try:
            retrieved_chunks = retrieve(
                query=query,
                top_k=8,
            )

        except Exception as exc:
            duration_ms = round(
                (
                    time.perf_counter()
                    - retrieval_start
                )
                * 1000,
                2,
            )

            _add_trace(
                trace,
                phase="OBSERVE",
                stage="RETRIEVAL",
                status=FAILED,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )

            return _error_response(
                recommendation=(
                    "Policy evidence could not "
                    "be retrieved."
                ),
                error=_build_error(
                    error_type="retrieval_error",
                    stage="RETRIEVAL",
                    message=(
                        "The policy retrieval "
                        "operation failed."
                    ),
                    retryable=True,
                ),
                trace=trace,
                conflicts=["retrieval_error"],
            )

        retrieval_duration = round(
            (
                time.perf_counter()
                - retrieval_start
            )
            * 1000,
            2,
        )

        if not retrieved_chunks:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="RETRIEVAL",
                status=FAILED,
                duration_ms=retrieval_duration,
                result_count=0,
            )

            return _error_response(
                recommendation=(
                    "No matching company policy "
                    "or Saudi labor-law evidence "
                    "was found."
                ),
                error=_build_error(
                    error_type="no_matching_policy",
                    stage="RETRIEVAL",
                    message=(
                        "Retrieval completed but "
                        "returned no evidence."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=["no_matching_policy"],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="RETRIEVAL",
            status=SUCCESS,
            duration_ms=retrieval_duration,
            result_count=len(retrieved_chunks),
        )

        # -------------------------------------------------
        # ACT — secure retrieved content
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="ACT",
            stage="EVIDENCE_SECURITY",
            status="STARTED",
        )

        safe_chunks = _filter_safe_chunks(
            retrieved_chunks
        )

        removed_chunks = (
            len(retrieved_chunks)
            - len(safe_chunks)
        )

        if not safe_chunks:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="EVIDENCE_SECURITY",
                status=FAILED,
                removed_count=removed_chunks,
            )

            return _error_response(
                recommendation=(
                    "Retrieved policy evidence "
                    "could not be processed safely."
                ),
                error=_build_error(
                    error_type="unsafe_retrieved_content",
                    stage="EVIDENCE_SECURITY",
                    message=(
                        "All retrieved evidence "
                        "failed security checks."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=[
                    "unsafe_retrieved_content"
                ],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="EVIDENCE_SECURITY",
            status=PASSED,
            safe_count=len(safe_chunks),
            removed_count=removed_chunks,
        )

        # -------------------------------------------------
        # ACT — rerank relevant evidence
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="ACT",
            stage="RERANKING",
            status="STARTED",
        )

        relevant_chunks = _filter_relevant_chunks(
            query=query,
            chunks=safe_chunks,
            min_score=2.0,
            max_chunks=4,
        )

        if not relevant_chunks:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="RERANKING",
                status=FAILED,
                result_count=0,
            )

            return _error_response(
                recommendation=(
                    "No sufficiently relevant "
                    "policy evidence was found."
                ),
                error=_build_error(
                    error_type="no_relevant_policy",
                    stage="RERANKING",
                    message=(
                        "Retrieved evidence did "
                        "not pass relevance filtering."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=["no_relevant_policy"],
            )

        source_ids = [
            chunk.get("id")
            for chunk in relevant_chunks
            if chunk.get("id")
        ]

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="RERANKING",
            status=SUCCESS,
            result_count=len(relevant_chunks),
            source_ids=source_ids,
        )

        # -------------------------------------------------
        # OBSERVE — collect HR facts
        # -------------------------------------------------

        hr_result = input.get("hr_result") or {}

        if not isinstance(hr_result, dict):
            hr_result = {}

        hr_facts = hr_result.get("facts") or {}

        if not isinstance(hr_facts, dict):
            hr_facts = {}

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="HR_FACTS",
            status=SUCCESS,
            facts_available=bool(hr_facts),
        )

        # -------------------------------------------------
        # REASON — policy analysis
        # -------------------------------------------------

        policy_analysis = _analyze_policy_chunks(
            relevant_chunks
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="POLICY_ANALYSIS",
            status=SUCCESS,
            rule_count=len(
                policy_analysis.get("rules", [])
            ),
            condition_count=len(
                policy_analysis.get(
                    "conditions",
                    [],
                )
            ),
        )

        # -------------------------------------------------
        # ACT — deterministic condition checks
        # -------------------------------------------------

        condition_analysis = _analyze_conditions(
            policy_analysis=policy_analysis,
            hr_facts=hr_facts,
            query=query,
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="CONDITION_ANALYSIS",
            status=SUCCESS,
            satisfied_count=len(
                condition_analysis.get(
                    "satisfied",
                    [],
                )
            ),
            not_satisfied_count=len(
                condition_analysis.get(
                    "not_satisfied",
                    [],
                )
            ),
            unknown_count=len(
                condition_analysis.get(
                    "unknown",
                    [],
                )
            ),
        )

        # -------------------------------------------------
        # REASON — source applicability
        # -------------------------------------------------

        applicability = (
            _analyze_source_applicability(
                chunks=relevant_chunks,
                condition_analysis=condition_analysis,
            )
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="SOURCE_APPLICABILITY",
            status=SUCCESS,
            evaluated_sources=len(applicability),
        )

        # -------------------------------------------------
        # ACT — deterministic request assessment
        # -------------------------------------------------

        request_assessment = (
            _build_request_assessment(
                hr_facts=hr_facts,
                condition_analysis=condition_analysis,
                applicability=applicability,
            )
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="REQUEST_ASSESSMENT",
            status=SUCCESS,
            assessment=request_assessment.get(
                "status"
            ),
            blocker_count=len(
                request_assessment.get(
                    "blockers",
                    [],
                )
            ),
            missing_information_count=len(
                request_assessment.get(
                    "missing_information",
                    [],
                )
            ),
        )

        conflicts = _build_conflicts(
            request_assessment
        )

        sources = _build_sources(
            relevant_chunks
        )

        # -------------------------------------------------
        # ACT — grounded recommendation generation
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="ACT",
            stage="GENERATION",
            status="STARTED",
        )

        generation_start = time.perf_counter()

        try:
            recommendation = (
                _generate_consultant_recommendation(
                    query=query,
                    chunks=relevant_chunks,
                    hr_facts=hr_facts,
                    policy_analysis=policy_analysis,
                    condition_analysis=condition_analysis,
                    applicability=applicability,
                    request_assessment=request_assessment,
                )
            )

        except Exception as exc:
            duration_ms = round(
                (
                    time.perf_counter()
                    - generation_start
                )
                * 1000,
                2,
            )

            _add_trace(
                trace,
                phase="OBSERVE",
                stage="GENERATION",
                status=FAILED,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )

            return _error_response(
                recommendation=(
                    "Relevant policy evidence "
                    "was retrieved and analyzed, "
                    "but the Consultant "
                    "recommendation could not "
                    "be generated."
                ),
                error=_build_error(
                    error_type="generation_error",
                    stage="GENERATION",
                    message=(
                        "The Consultant LLM "
                        "generation operation failed."
                    ),
                    retryable=True,
                ),
                trace=trace,
                conflicts=_unique_strings(
                    conflicts
                    + ["generation_error"]
                ),
                sources=sources,
                policy_analysis=policy_analysis,
                condition_analysis=condition_analysis,
                applicability=applicability,
                request_assessment=request_assessment,
            )

        generation_duration = round(
            (
                time.perf_counter()
                - generation_start
            )
            * 1000,
            2,
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="GENERATION",
            status=SUCCESS,
            duration_ms=generation_duration,
        )

        # -------------------------------------------------
        # OBSERVE — validate recommendation
        # -------------------------------------------------

        validation_sources = [
            {
                "id": chunk.get("id"),
                "text": chunk.get("text"),
            }
            for chunk in relevant_chunks
        ]

        try:
            output_is_valid = validate_output(
                recommendation,
                validation_sources,
            )

        except Exception as exc:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="OUTPUT_VALIDATION",
                status=FAILED,
                error_type=type(exc).__name__,
            )

            return _error_response(
                recommendation=recommendation,
                error=_build_error(
                    error_type="validation_error",
                    stage="OUTPUT_VALIDATION",
                    message=(
                        "The generated Consultant "
                        "output could not be validated."
                    ),
                    retryable=False,
                ),
                trace=trace,
                conflicts=_unique_strings(
                    conflicts
                    + [
                        "consultant_output_validation_failed"
                    ]
                ),
                sources=sources,
                policy_analysis=policy_analysis,
                condition_analysis=condition_analysis,
                applicability=applicability,
                request_assessment=request_assessment,
            )

        if not output_is_valid:
            conflicts.append(
                "consultant_output_validation_failed"
            )

            _add_trace(
                trace,
                phase="OBSERVE",
                stage="OUTPUT_VALIDATION",
                status=FAILED,
            )

        else:
            _add_trace(
                trace,
                phase="OBSERVE",
                stage="OUTPUT_VALIDATION",
                status=PASSED,
            )

        # -------------------------------------------------
        # OBSERVE — workflow completed
        # -------------------------------------------------

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="WORKFLOW",
            status=SUCCESS,
            final_assessment=(
                request_assessment.get("status")
            ),
        )

        return {
            "recommendation": recommendation,
            "conflicts": _unique_strings(
                conflicts
            ),
            "sources": sources,
            "policy_analysis": policy_analysis,
            "condition_analysis": condition_analysis,
            "applicability": applicability,
            "request_assessment": request_assessment,
            "success": True,
            "error": None,
            "trace": trace,
        }
