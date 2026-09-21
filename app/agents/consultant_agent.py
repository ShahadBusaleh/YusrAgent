
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

SUCCESS = "SUCCESS"
FAILED = "FAILED"
PASSED = "PASSED"
INFORMATIONAL = "INFORMATIONAL"


STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "how",
    "what",
    "when",
    "where",
    "why",
    "who",
    "can",
    "could",
    "should",
    "would",
    "do",
    "does",
    "did",
    "of",
    "to",
    "for",
    "in",
    "on",
    "at",
    "by",
    "with",
    "and",
    "or",
    "from",
    "this",
    "that",
    "these",
    "those",
    "employee",
    "employees",
}


# =========================================================
# TRACE / OBSERVABILITY
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

    This stores execution metadata only.
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


# =========================================================
# ERROR HELPERS
# =========================================================

def _build_error(
    error_type: str,
    stage: str,
    message: str,
    retryable: bool = False,
) -> dict:
    """
    Build a standardized Consultant error.
    """

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
) -> dict:
    """
    Return a consistent Consultant response structure.
    """

    return {
        "recommendation": recommendation,
        "conflicts": conflicts or [],
        "sources": sources or [],
        "policy_analysis": policy_analysis or {},

        # Kept for compatibility with the existing project.
        #
        # IMPORTANT:
        # Consultant no longer performs employee-specific
        # condition analysis or request assessment.
        "condition_analysis": {},
        "applicability": [],
        "request_assessment": {
            "status": INFORMATIONAL,
            "blockers": [],
            "missing_information": [],
            "notes": [],
        },

        "success": False,
        "error": error,
        "trace": trace,
    }


# =========================================================
# ACTION-TYPE CITATION LOOKUP
# =========================================================

_ACTION_TYPE_QUERIES: dict[str, str] = {
    "bank_update": (
        "What is the HR policy or Saudi labor law on changing an "
        "employee's bank account or IBAN details?"
    ),
    "personal_info_update": (
        "What is the HR policy on updating an employee's personal "
        "information, such as mobile number, address, or email?"
    ),
    "leave_request": (
        "What is the HR policy or Saudi labor law on submitting and "
        "approving an employee leave request?"
    ),
    "certificate_request": (
        "What is the HR policy on issuing an employment or salary "
        "certificate letter for an employee?"
    ),
}


def _query_for_action_type(action_type: str) -> str:
    """Map a proposed-action type to the policy question Consultant
    should answer for it.

    This lets an approval-context lookup (input: {"action_type": ...})
    reuse the exact same retrieval + generation pipeline as a normal
    free-text query, instead of a separate code path.
    """

    key = str(action_type or "").strip().lower()

    return _ACTION_TYPE_QUERIES.get(
        key,
        f"What HR policy or Saudi labor law applies to the action "
        f"type '{action_type}'?",
    )


# =========================================================
# TEXT HELPERS
# =========================================================

def _safe_text(value: Any) -> str:
    """
    Convert a value to safe text and mask PII.
    """

    if value is None:
        return ""

    return mask_pii(str(value))


def _extract_tokens(text: str) -> set[str]:
    """
    Tokenization used only for lexical relevance reranking.
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
    """
    Parse simple key:value fields from retrieved
    policy chunks.

    Example:

        Rule: Employees are entitled to annual leave.
        Conditions: Five years of service.
        Exceptions: Employer may postpone leave.

    """

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


# =========================================================
# RERANKING
# =========================================================

def _calculate_relevance_score(
    query: str,
    chunk: dict,
) -> float:
    """
    Calculate a lexical relevance score.

    This function only ranks policy evidence.

    It does NOT:
    - evaluate employees
    - calculate leave
    - determine eligibility
    - make decisions
    """

    query_tokens = _extract_tokens(query)

    if not query_tokens:
        return 1.0

    chunk_text = chunk.get("text", "") or ""

    fields = _parse_chunk_fields(chunk_text)

    text_tokens = _extract_tokens(chunk_text)

    rule_tokens = _extract_tokens(
        fields.get("rule", "")
    )

    condition_tokens = _extract_tokens(
        fields.get("conditions", "")
    )

    exception_tokens = _extract_tokens(
        fields.get("exceptions", "")
    )

    text_overlap = len(
        query_tokens & text_tokens
    )

    rule_overlap = len(
        query_tokens & rule_tokens
    )

    condition_overlap = len(
        query_tokens & condition_tokens
    )

    exception_overlap = len(
        query_tokens & exception_tokens
    )

    return (
        text_overlap * 1.0
        + rule_overlap * 2.0
        + condition_overlap * 1.0
        + exception_overlap * 1.0
    )


def _filter_relevant_chunks(
    query: str,
    chunks: list[dict],
    min_score: float = 2.0,
    max_chunks: int = 3,
) -> list[dict]:
    """
    Rank retrieved policy chunks and keep the strongest ones.
    """

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

    # Keep at least the strongest retrieved document.
    if not relevant_chunks:
        return ranked_chunks[:1]

    return relevant_chunks[:max_chunks]


# =========================================================
# RETRIEVED EVIDENCE SECURITY
# =========================================================

def _filter_safe_chunks(
    chunks: list[dict],
) -> list[dict]:
    """
    Remove retrieved chunks containing prompt injection.
    """

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
    """
    Extract policy information from retrieved documents.

    IMPORTANT:

    This function describes the policy itself.

    It does NOT determine whether a particular employee
    satisfies the policy.
    """

    rules: list[dict] = []
    conditions: list[dict] = []
    exceptions: list[dict] = []

    for chunk in chunks:

        source_id = chunk.get("id")

        text = chunk.get(
            "text",
            "",
        )

        fields = _parse_chunk_fields(text)

        rule = fields.get("rule")
        condition = fields.get("conditions")
        exception = fields.get("exceptions")

        if rule:

            rules.append(
                {
                    "source_id": source_id,
                    "text": _safe_text(rule),
                }
            )

        if condition:

            conditions.append(
                {
                    "source_id": source_id,
                    "text": _safe_text(condition),
                }
            )

        if exception:

            exceptions.append(
                {
                    "source_id": source_id,
                    "text": _safe_text(exception),
                }
            )

    return {
        "rules": rules,
        "conditions": conditions,
        "exceptions": exceptions,
    }


# =========================================================
# SOURCES
# =========================================================

def _build_sources(
    chunks: list[dict],
) -> list[dict]:
    """
    Build source metadata.

    Source IDs are kept internally for validation and linking.
    A human-readable display name is also generated for the UI.
    """

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

        source_text = str(
            chunk.get("text") or ""
        ).strip()

        # ---------------------------------
        # Human-readable source name
        # ---------------------------------
        display_name = ""

        # Saudi Labor Law
        article_match = re.search(
            r"Article:\s*([^\s]+).*?"
            r"(?:Title|Name):\s*(.*?)(?=\s+(?:Section|Purpose|Rule|Conditions|$))",
            source_text,
            flags=re.IGNORECASE,
        )

        if article_match:
            article = article_match.group(1).strip()
            title = article_match.group(2).strip()

            display_name = (
                f"Saudi Labor Law — "
                f"Article {article}: {title}"
            )

        # Internal company policy
        if not display_name:
            section_match = re.search(
                r"Section:\s*([^\s]+).*?"
                r"Policy Name:\s*(.*?)(?=\s+(?:Purpose|Rule|Conditions|$))",
                source_text,
                flags=re.IGNORECASE,
            )

            if section_match:
                section = section_match.group(1).strip()
                policy_name = section_match.group(2).strip()

                display_name = (
                    f"Internal Policy — "
                    f"Section {section}: {policy_name}"
                )

        # Generic fallback
        if not display_name:
            display_name = (
                chunk.get("filename")
                or "Policy source"
            )

        
        sources.append(
            {
                "id": source_id,
                "display_name": display_name,
                "text": source_text,
                "source_table": chunk.get("source_table"),
                "filename": chunk.get("filename"),
                "score": chunk.get("score"),
                "relevance_score": chunk.get(
                    "consultant_relevance_score"
                ),
            }
        )

    return sources

def _format_context(
    chunks: list[dict],
) -> str:
    """
    Format retrieved policy evidence for the LLM.
    """

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
            chunk.get(
                "text",
                "",
            )
        )

        parts.append(
            f"[Source: {source_id}]\n"
            f"Source type: {source_table}\n"
            f"{text}"
        )

    return "\n\n".join(parts)


# =========================================================
# LLM GENERATION
# =========================================================

def _generate_consultant_recommendation(
    query: str,
    chunks: list[dict],
) -> str:
    """
    Generate a policy-only Consultant response.

    HR data is intentionally NOT passed to this function.
    """

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

    context = _format_context(
        chunks
    )

    # -----------------------------------------------------
    # SYSTEM PROMPT
    # -----------------------------------------------------

    system_prompt = """
You are the Consultant Agent in the Yusr Agentic HR System.

Your role is HR policy retrieval and policy interpretation only.

STRICT RULES:

1. Answer only from the retrieved policy evidence.

2. Explain the policy rules that directly answer
   the user's question.

3. Clearly identify policy:
   - Rules
   - Conditions
   - Requirements
   - Exceptions

4. Do NOT access employee-specific data.

5. Do NOT access the HR database.

6. Do NOT evaluate whether a specific employee
   satisfies a policy condition.

7. Do NOT calculate employee leave balances.

8. Do NOT determine employee eligibility.

9. Do NOT assess an employee's request.

10. Do NOT create employee-specific blockers.

11. Do NOT request employee information.

12. Do NOT approve or reject employee actions.

13. Do NOT make authorization decisions.

14. Never invent policies, legal rules, conditions,
    exceptions, or citations.

15. If the retrieved evidence is insufficient,
    clearly say that the available policy evidence
    is insufficient.

16. Cite relevant evidence using:

    [Source: SOURCE_ID]

17. Keep the response concise and professional.

18. The Manager Agent is responsible for governance
    and final system-level decisions.

19. The Consultant is advisory only.

20. The Consultant must remain independent from
    the HR Agent.

21. When the retrieved evidence contains a Law ID or Article number,
    explicitly mention it in the answer.

22. Prefer this format when applicable:
    "According to Article X (Law ID: LAWXXX), ..."

23. Connect each policy rule to its corresponding Article or Law ID.

24. Do not invent an Article number or Law ID.
    Only mention identifiers explicitly present in the retrieved evidence.

GRIEVANCE HANDLING:

When the user request is a grievance or complaint:

1. Analyze the complaint using ONLY:
   - Saudi Labor Law
   - Company HR policies
   - Retrieved policy evidence

2. Determine whether the complaint appears:
   - compliant with the regulations/policies
   - or potentially in violation

3. Identify the relevant:
   - Article
   - Law ID
   - Company policy
   when available in the retrieved evidence.

4. Provide:
   - policy/legal analysis
   - supporting evidence
   - suggested resolution

5. Determine whether resolving the grievance requires
   employee-specific information.

6. Return whether employee-specific data is required.

7. NEVER retrieve employee-specific information yourself.

8. If employee-specific information is required,
   the Orchestrator may call the HR Agent only when
   the employee's identity is visible.

9. If the employee chose to hide their identity,
   do not request, reveal, or retrieve identifying information.

10. A grievance must NEVER be considered finally resolved
    by the Consultant.

11. Human HR review is ALWAYS required for grievances.
""".strip()

    # -----------------------------------------------------
    # USER PROMPT
    # -----------------------------------------------------

    user_prompt = f"""
POLICY QUESTION:

{query}

RETRIEVED POLICY EVIDENCE:

{context}

Answer the question using ONLY the retrieved
policy evidence.

Explain the relevant:

- Policy rules
- Conditions
- Requirements
- Exceptions

For each relevant policy rule, include the corresponding
Article number and Law ID when they are available in the evidence.

Use the format:

According to Article X (Law ID: LAWXXX), ...

Do not invent or infer legal identifiers.

Do NOT evaluate any specific employee.

Do NOT use employee-specific information.

Do NOT calculate leave balances.

Do NOT determine eligibility.

Cite relevant sources using:

[Source: SOURCE_ID]
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

    return _safe_text(
        recommendation
    )


# =========================================================
# CONSULTANT AGENT
# =========================================================

class ConsultantAgent(BaseAgent):

    def run(
        self,
        input: dict,
    ) -> dict:

        trace: list[dict] = []

        # =================================================
        # 1. INPUT VALIDATION
        # =================================================

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
                recommendation=(
                    "Invalid Consultant input."
                ),
                error=_build_error(
                    error_type="invalid_input",
                    stage="INPUT_VALIDATION",
                    message=(
                        "Consultant input must "
                        "be a dictionary."
                    ),
                ),
                trace=trace,
                conflicts=[
                    "invalid_input"
                ],
            )

        raw_query = input.get("query", "")

        # Second call shape: {"action_type": "bank_update"} looks up the
        # policy/law citation for that action instead of a free-text
        # question. Falls back to the templated query only when no
        # explicit query was given.
        if not raw_query and input.get("action_type"):
            raw_query = _query_for_action_type(input["action_type"])

        query = sanitize_input(raw_query)

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
                ),
                trace=trace,
                conflicts=[
                    "missing_query"
                ],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="INPUT_VALIDATION",
            status=PASSED,
        )

        # =================================================
        # IMPORTANT ARCHITECTURE NOTE
        # =================================================
        #
        # We intentionally do NOT read:
        #
        # input["hr_result"]
        #
        # The Consultant is independent from HR.
        #
        # =================================================

        # =================================================
        # 2. INPUT SECURITY
        # =================================================

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
                error_type=(
                    "prompt_injection_detected"
                ),
            )

            return _error_response(
                recommendation=(
                    "Request could not be processed "
                    "for security reasons."
                ),
                error=_build_error(
                    error_type=(
                        "prompt_injection_detected"
                    ),
                    stage="INPUT_SECURITY",
                    message=(
                        "Potential prompt injection "
                        "was detected."
                    ),
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

        # =================================================
        # 3. RAG RETRIEVAL
        # =================================================

        _add_trace(
            trace,
            phase="ACT",
            stage="RETRIEVAL",
            status="STARTED",
        )

        retrieval_start = (
            time.perf_counter()
        )

        try:

            retrieved_chunks = retrieve(
                query=query,
                top_k=3,
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
                conflicts=[
                    "retrieval_error"
                ],
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
                    "No matching policy evidence "
                    "was found."
                ),
                error=_build_error(
                    error_type="no_matching_policy",
                    stage="RETRIEVAL",
                    message=(
                        "Retrieval completed but "
                        "returned no evidence."
                    ),
                ),
                trace=trace,
                conflicts=[
                    "no_matching_policy"
                ],
            )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="RETRIEVAL",
            status=SUCCESS,
            duration_ms=retrieval_duration,
            result_count=len(
                retrieved_chunks
            ),
        )

        # =================================================
        # 4. RETRIEVED EVIDENCE SECURITY
        # =================================================

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
                    error_type=(
                        "unsafe_retrieved_content"
                    ),
                    stage="EVIDENCE_SECURITY",
                    message=(
                        "All retrieved evidence "
                        "failed security checks."
                    ),
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
            safe_count=len(
                safe_chunks
            ),
            removed_count=removed_chunks,
        )

        # =================================================
        # 5. RERANKING
        # =================================================

        _add_trace(
            trace,
            phase="ACT",
            stage="RERANKING",
            status="STARTED",
        )

        relevant_chunks = (
            _filter_relevant_chunks(
                query=query,
                chunks=safe_chunks,
                min_score=2.0,
                max_chunks=3,
            )
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
                ),
                trace=trace,
                conflicts=[
                    "no_relevant_policy"
                ],
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
            result_count=len(
                relevant_chunks
            ),
            source_ids=source_ids,
        )

        # =================================================
        # 6. POLICY ANALYSIS
        # =================================================

        policy_analysis = (
            _analyze_policy_chunks(
                relevant_chunks
            )
        )

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="POLICY_ANALYSIS",
            status=SUCCESS,
            rule_count=len(
                policy_analysis.get(
                    "rules",
                    [],
                )
            ),
            condition_count=len(
                policy_analysis.get(
                    "conditions",
                    [],
                )
            ),
            exception_count=len(
                policy_analysis.get(
                    "exceptions",
                    [],
                )
            ),
        )

        # =================================================
        # 7. SOURCES
        # =================================================

        sources = _build_sources(
            relevant_chunks
        )

        # Consultant does not create employee-specific
        # conflicts.
        conflicts: list[str] = []

        # =================================================
        # 8. LLM GENERATION
        # =================================================

        _add_trace(
            trace,
            phase="ACT",
            stage="GENERATION",
            status="STARTED",
        )

        generation_start = (
            time.perf_counter()
        )

        try:

            recommendation = (
                _generate_consultant_recommendation(
                    query=query,
                    chunks=relevant_chunks,
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
                    "was retrieved, but the "
                    "Consultant response "
                    "could not be generated."
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
                conflicts=[
                    "generation_error"
                ],
                sources=sources,
                policy_analysis=policy_analysis,
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

        # =================================================
        # 9. OUTPUT VALIDATION
        # =================================================

        validation_sources = [
            {
                "id": chunk.get("id"),
                "text": chunk.get(
                    "text",
                    "",
                ),
            }
            for chunk in relevant_chunks
        ]

        try:

            output_is_valid = (
                validate_output(
                    recommendation,
                    validation_sources,
                )
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
                ),
                trace=trace,
                conflicts=[
                    "consultant_output_validation_failed"
                ],
                sources=sources,
                policy_analysis=policy_analysis,
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

        # =================================================
        # 10. FINAL RESPONSE
        # =================================================

        _add_trace(
            trace,
            phase="OBSERVE",
            stage="WORKFLOW",
            status=SUCCESS,
            final_assessment=INFORMATIONAL,
        )

        return {
            "recommendation": recommendation,

            # Consultant does not generate
            # employee-specific conflicts.
            "conflicts": list(
                dict.fromkeys(
                    conflicts
                )
            ),

            "sources": sources,

            # Policy-level information only.
            "policy_analysis": policy_analysis,

            # Compatibility fields.
            #
            # These are intentionally empty because
            # Consultant does not analyze employee facts.
            "condition_analysis": {},

            "applicability": [],

            "request_assessment": {
                "status": INFORMATIONAL,
                "blockers": [],
                "missing_information": [],
                "notes": [],
            },

            "success": True,

            "error": None,

            "trace": trace,
        }

