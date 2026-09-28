
from __future__ import annotations

import logging
import re
import time
from typing import Any

from openai import OpenAI

from app.agents.arabic_text import glossary_instruction
from app.agents.base import BaseAgent
from app.config import get_settings
from app.rag.retrieve import chunk_article, cited_articles, retrieve

from app.llm import llm_client
from app.security.governance import (
    detect_prompt_injection,
    mask_pii,
    sanitize_input,
    validate_output,
    verify_citations,
)
# =========================================================
# CONSTANTS
# =========================================================

logger = logging.getLogger(__name__)

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
    # Worded after the annual-leave texts: "submitting ... a request"
    # matched the exam-leave article (LAW057, "submit an examination
    # leave request") ahead of the annual-leave rules.
    "leave_request": (
        "What is the company policy on planning annual leave with the "
        "line manager, and what does Saudi labor law say about annual "
        "leave entitlement and taking it in the year it becomes due?"
    ),
    "certificate_request": (
        "What is the HR policy on issuing an employment or salary "
        "certificate letter for an employee?"
    ),
    "new_hire": (
        "What does Saudi labor law say about the probation period for a "
        "newly hired employee, and what is the company medical "
        "insurance policy for new employees?"
    ),
    "termination": (
        "Under Saudi labor law, what notice period is required to "
        "terminate an employment contract, and how is the end-of-service "
        "award calculated?"
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


_HYPHEN_VARIANTS = "‐‑‒–—−"

_POLICY_ID_ODD_HYPHENS_RE = re.compile(
    rf"\bAAM[{_HYPHEN_VARIANTS}]POL[{_HYPHEN_VARIANTS}](\d{{3}})\b"
)


def _normalize_citations(text: str) -> str:
    """
    The model sometimes writes 【Source: X】 and non-breaking hyphens
    (AAM‑POL‑015). Normalize to the [Source: ID] / AAM-POL-015 form the
    UI, the Arabic translator, and the retrieved source ids all use.
    """

    text = text.replace("【", "[").replace("】", "]")

    return _POLICY_ID_ODD_HYPHENS_RE.sub(r"AAM-POL-\1", text)


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


_ENUMERATION_RE = re.compile(
    r"\b(?:what|which)\s+(?:information|details|fields|data|items)\b"
    r"|\b(?:types?|kinds?|components?|elements?|list)\s+of\b"
    r"|\bcomponents?\b",
    re.IGNORECASE,
)
_MAX_LIST_CHUNKS = 5


def _filter_relevant_chunks(
    query: str,
    chunks: list[dict],
    min_score: float = 2.0,
    max_chunks: int = 3,
) -> list[dict]:
    """
    Keep the first retrieved chunks that pass a lexical relevance filter.
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

    # Keep the retriever's (hybrid dense + keyword) order and use the
    # lexical score only as a filter. Sorting by it let generic words
    # ("policy", "labor", "law") outrank the right article — e.g. the
    # annual-leave policy beat LAW006 for an IBAN question.
    relevant_chunks = [
        chunk
        for chunk in ranked_chunks
        if chunk[
            "consultant_relevance_score"
        ] >= min_score
    ]

    # A question about a named article gets every row of that article,
    # otherwise the cap dropped e.g. the 21/30-day rules of Article 109.
    articles = cited_articles(query)
    if articles:
        article_chunks = [
            chunk
            for chunk in ranked_chunks
            if chunk_article(chunk) in articles
        ]
        if article_chunks:
            return article_chunks

    # Keep at least the strongest retrieved document — but only if it
    # shares any vocabulary with the question at all. A zero-overlap
    # chunk is noise; returning [] lets the caller say the evidence is
    # insufficient instead of stretching an unrelated rule.

    if not relevant_chunks:
        return [
            chunk
            for chunk in ranked_chunks[:1]
            if chunk["consultant_relevance_score"] > 0
        ]

    selected = relevant_chunks[:max_chunks]

    # Company policy is the Consultant's first source. When three law rows
    # fill the cap, the matching company rule (AAM-POL-020 for "who approves
    # unpaid leave?", ranked 5th) was dropped; give it the last slot.
    if not any(
        chunk.get("source_table") == "company_policies"
        for chunk in selected
    ):
        company = next(
            (
                chunk
                for chunk in relevant_chunks[max_chunks:]
                if chunk.get("source_table") == "company_policies"
            ),
            None,
        )
        if company is not None and len(selected) == max_chunks:
            selected = selected[:-1] + [company]

    # "What are the wage components?" is answered by several one-line WPS
    # rows of one category (basic wage, housing allowance, ...). Three
    # could never cover the list, so keep same-category siblings too.
    if _ENUMERATION_RE.search(query or ""):
        categories = {
            _parse_chunk_fields(chunk.get("text", "")).get("category")
            for chunk in selected
        }
        for chunk in relevant_chunks[max_chunks:]:
            if len(selected) >= _MAX_LIST_CHUNKS:
                break
            if chunk in selected:
                continue
            if _parse_chunk_fields(chunk.get("text", "")).get("category") in categories:
                selected.append(chunk)

    return selected


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
        source_table = chunk.get("source_table")

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
                    "source_table": source_table,
                    "text": _safe_text(rule),
                }
            )

        if condition:

            conditions.append(
                {
                    "source_id": source_id,
                    "source_table": source_table,
                    "text": _safe_text(condition),
                }
            )

        if exception:

            exceptions.append(
                {
                    "source_id": source_id,
                    "source_table": source_table,
                    "text": _safe_text(exception),
                }
            )

    return {
        "rules": rules,
        "conditions": conditions,
        "exceptions": exceptions,
    }

def _detect_policy_conflicts(
    policy_analysis: dict,
) -> list[str]:
    """Detect only meaningful numeric law-vs-policy conflicts."""

    conflicts: list[str] = []

    entries = []
    for field_name in (
        "rules",
        "conditions",
        "exceptions",
    ):
        for entry in policy_analysis.get(field_name, []):
            entries.append(
                {
                    "field": field_name,
                    **entry,
                }
            )

    law_entries = [
        entry
        for entry in entries
        if entry.get("source_table") == "saudi_labor_law"
    ]

    policy_entries = [
        entry
        for entry in entries
        if entry.get("source_table") == "company_policies"
    ]

    def clean_text(text: str) -> str:
        text = _safe_text(text)

        # Remove source metadata / identifiers.
        text = re.sub(
            r"\bLAW\d+\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\bAAM-POL-\d+\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(?:Article|Section)\s*:?\s*\d+\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )

        return text.strip()

    def meaningful_numbers(text: str) -> set[str]:
        cleaned = clean_text(text)
        return set(
            re.findall(
                r"\b\d+(?:\.\d+)?\b",
                cleaned,
            )
        )

    def meaningful_tokens(text: str) -> set[str]:
        return {
            token
            for token in _extract_tokens(clean_text(text))
            if len(token) >= 4
        }

    for law in law_entries:
        law_text = clean_text(law.get("text", ""))

        if not law_text:
            continue

        law_tokens = meaningful_tokens(law_text)
        law_numbers = meaningful_numbers(law_text)

        for policy in policy_entries:
            policy_text = clean_text(policy.get("text", ""))

            if not policy_text:
                continue

            shared_tokens = (
                law_tokens
                & meaningful_tokens(policy_text)
            )

            if len(shared_tokens) < 3:
                continue

            policy_numbers = meaningful_numbers(
                policy_text
            )

            # No meaningful numeric rule values -> no numeric conflict.
            if not law_numbers or not policy_numbers:
                continue

            if law_numbers != policy_numbers:
                conflicts.append(
                    "Potential conflict between "
                    f"{law.get('source_id')} and "
                    f"{policy.get('source_id')}: "
                    "meaningful numeric rule values differ."
                )

    return list(dict.fromkeys(conflicts))
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
            r"Article:\s*([^\n]+)\s*"
            r"Title:\s*([^\n]+)",
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
        # Internal company policy
        if not display_name:
            policy_match = re.search(
                r"Policy Name:\s*([^\n]+)",
                source_text,
                flags=re.IGNORECASE,
            )

            if policy_match:
                policy_name = policy_match.group(1).strip()

                display_name = (
                    f"Company Policies — "
                    f"{policy_name}"
                )

        # Generic fallback
        # Generic source name from RAG metadata
        if not display_name:
            display_name = (
                chunk.get("source_name")
                or chunk.get("filename")
                or "Policy source"
            )

        
        sources.append(
            {
                "id": source_id,
                "source_ids": chunk.get("source_ids") or [source_id],
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

        source_name = (
        chunk.get("source_name")
        or chunk.get("source_table")
        or "Policy source"
    )

        text = _safe_text(
            chunk.get(
                "text",
                "",
            )
        )

        parts.append(
            f"Source: {source_name}\n"
            f"{text}"
        )

    return "\n\n".join(parts)


# =========================================================
# LLM GENERATION
# =========================================================

def _generate_consultant_recommendation(
    query: str,
    chunks: list[dict],
    is_grievance: bool = False,
    lang: str = "en",
) -> str:
    """
    Generate a policy-only Consultant response.

    HR data is intentionally NOT passed to this function.
    lang="ar" writes the answer in Arabic directly (the evidence and the
    question stay English) instead of translating it afterwards.
    """

    settings = get_settings()

    if not settings.llm_api_key:

        raise RuntimeError(
            "LLM_API_KEY is not configured "
            "in the local .env file."
        )

    client = llm_client(settings, openai_cls=OpenAI)

    context = _format_context(
        chunks
    )

    # -----------------------------------------------------
    # SYSTEM PROMPT
    # -----------------------------------------------------
    if is_grievance:
       system_prompt = """
        You are the Consultant Agent in the Yusr Agentic HR System.

        You are handling an employee grievance or complaint.

        STRICT RULES:

        1. Use ONLY retrieved Saudi Labor Law and Company HR policy evidence.
        2. Explain the relevant legal or policy rules and supporting evidence.
        3. Mention the relevant Article or policy when available.
        4. Do not access employee-specific data or the HR database.
        5. Do not decide employee eligibility, approve, reject, or resolve the grievance.
        6. Do not invent policies, legal rules, conditions, exceptions, or citations.
        7. For every relevant policy rule, include a citation in the exact format [Source: ID]. Use only IDs that appear in the retrieved policy evidence.        8. State clearly if the retrieved evidence is insufficient.
        9. Human HR review is ALWAYS required.
        10. If employee-specific information is required, indicate that it is required.
        11. Only include rules, conditions, or exceptions that the evidence states. Leave out anything it does not cover.
        """.strip()
    else:
        system_prompt = """
    You are the Consultant Agent in the Yusr Agentic HR System.

    Your role is HR policy retrieval and policy interpretation only.

    STRICT RULES:

    1. Answer only from the retrieved policy evidence.
    2. Explain the policy rules that directly answer the user's question.
    3. Identify the rules, conditions, requirements, and exceptions that the evidence actually states. Leave out any of these the evidence does not mention; do not add a heading or a sentence to fill it.
    4. Do NOT access employee-specific data or the HR database.
    5. Do NOT evaluate employee eligibility or employee-specific conditions.
    6. Do NOT calculate employee leave balances.
    7. Do NOT approve, reject, or authorize employee actions.
    8. Never invent policies, legal rules, conditions, exceptions, or citations.
    9. If the retrieved evidence is insufficient, clearly say so.
    10. Use human-readable source names only.
    11. Keep the response concise and professional.
    12. The Manager Agent is responsible for governance and final decisions.
    13. The Consultant is advisory only and independent from the HR Agent.
    14. Article numbers may be mentioned when relevant.
    15. For every relevant policy rule, include a citation in the exact format [Source: ID]. Use only IDs that appear in the retrieved policy evidence.    """.strip()

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

Explain whichever of these the evidence states:

- Policy rules
- Conditions
- Requirements
- Exceptions

Leave out any part the evidence does not cover: no empty or
placeholder headings, and no general statements (such as
"subject to statutory limits") unless the evidence says them.
Only answer what was asked; do not add a summary that repeats
or extends the points above.

For each relevant policy rule, mention the Article number
when it is relevant and available.

For every relevant policy rule, include a citation in the exact
format [Source: ID].

Use ONLY source IDs that appear in the retrieved policy evidence.
Do not invent or modify source IDs.

You may also mention human-readable source names when available.
Do NOT evaluate any specific employee.

Do NOT use employee-specific information.

Do NOT calculate leave balances.

Do NOT determine eligibility.

Include citations using the exact format [Source: ID].
Citations must refer only to the retrieved policy evidence.
""".strip()

    if lang == "ar":
        system_prompt += (
            "\n\nWrite the whole answer in Modern Standard Arabic. Keep every "
            "[Source: ID] citation exactly as written, in English. Write "
            "numbers as digits exactly as they appear in the evidence."
            + glossary_instruction()
        )

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
        _normalize_citations(recommendation)
    )

def _generate_for_reader(
    query: str,
    chunks: list[dict],
    is_grievance: bool,
    lang: str,
) -> tuple[str, str]:
    """(recommendation, language). An Arabic answer that fails the
    groundedness checks falls back to the English answer, which the
    Orchestrator then translates as before."""

    if lang == "ar":
        validation_sources = [
            {
                "id": chunk.get("id"),
                "source_ids": chunk.get("source_ids"),
                "text": chunk.get("text", ""),
            }
            for chunk in chunks
        ]
        try:
            arabic = _generate_consultant_recommendation(
                query=query, chunks=chunks, is_grievance=is_grievance, lang="ar"
            )
            if validate_output(arabic, validation_sources) and verify_citations(
                arabic, validation_sources
            ):
                return arabic, "ar"
            logger.info("Arabic Consultant answer not grounded; using English")
        except Exception:
            logger.warning("Arabic Consultant answer failed; using English", exc_info=True)

    return (
        _generate_consultant_recommendation(
            query=query, chunks=chunks, is_grievance=is_grievance
        ),
        "en",
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
            )

        raw_query = input.get("query", "")
        intent = str(
            input.get("intent") or ""
        ).strip().upper()

        is_grievance = intent == "GRIEVANCE"
        lang = "ar" if input.get("lang") == "ar" and not is_grievance else "en"

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
                # Over-fetch so the reranker below can actually choose
                # the best 3; with top_k=3 it could only reorder.
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
        conflicts = _detect_policy_conflicts(
            policy_analysis
        )

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

            recommendation, language = _generate_for_reader(
                query=query,
                chunks=relevant_chunks,
                is_grievance=is_grievance,
                lang=lang,
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
                "source_ids": chunk.get("source_ids"),
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
                and verify_citations(
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
                sources=sources,
                policy_analysis=policy_analysis,
            )

        if not output_is_valid:

            _add_trace(
                trace,
                phase="OBSERVE",
                stage="OUTPUT_VALIDATION",
                status=FAILED,
            )

            # An ungrounded answer is an error, not a policy conflict:
            # `conflicts` is reserved for real law-vs-policy conflicts,
            # and Manager reads `success`/`error` to decide whether to
            # use the recommendation at all.
            return _error_response(
                recommendation=recommendation,
                error=_build_error(
                    error_type="ungrounded_output",
                    stage="OUTPUT_VALIDATION",
                    message=(
                        "The generated Consultant output is not "
                        "sufficiently supported by the retrieved evidence."
                    ),
                ),
                trace=trace,
                sources=sources,
                policy_analysis=policy_analysis,
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

            # "ar" when written in Arabic directly for an Arabic reader;
            # Manager then skips translating it.
            "language": language,

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

