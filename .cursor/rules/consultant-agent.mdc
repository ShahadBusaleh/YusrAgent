
from __future__ import annotations

import re

from app.agents.base import BaseAgent
from app.rag.retrieve import retrieve
from app.security.governance import detect_prompt_injection, mask_pii, sanitize_input


def _parse_chunk_fields(text: str) -> dict:
    """Split a policy_texts chunk (Rule/Conditions/Exceptions lines) into fields."""
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    return fields


def _looks_related(query: str, fields: dict) -> bool:
    """True if the query shares at least one token (len>=4) with Rule/Conditions."""
    q_tokens = set(re.findall(r"[a-zA-Z\u0600-\u06FF]{4,}", (query or "").lower()))
    blob = f"{fields.get('rule', '')} {fields.get('conditions', '')}".lower()
    blob_tokens = set(re.findall(r"[a-zA-Z\u0600-\u06FF]{4,}", blob))
    return bool(q_tokens & blob_tokens)


class ConsultantAgent(BaseAgent):
    def run(self, input: dict) -> dict:
        """Consultant / Policy Agent — RAG over company policy and labor law.

        Expected input:
            query (str): original request
            hr_result (dict): facts/proposed_action from HRAgent

        Expected output:
            recommendation (str): policy-grounded advice
            conflicts (list): missing conditions, exceptions, mismatches
            sources (list): company_policies.id and/or saudi_labor_law.id
        """
        query = sanitize_input(input.get("query", ""))

        if detect_prompt_injection(query):
            return {
                "recommendation": "Request could not be processed for security reasons.",
                "conflicts": ["prompt_injection_detected"],
                "sources": [],
            }

        chunks = retrieve(query, top_k=5)
        if not chunks:
            return {
                "recommendation": "No matching policy or law article was found for this request.",
                "conflicts": ["no_matching_policy"],
                "sources": [],
            }

        safe_chunks = [c for c in chunks if not detect_prompt_injection(c.get("text", ""))]

        conflicts: list[str] = []
        sources: list[dict] = []
        matched_rules: list[str] = []
        hr_facts = (input.get("hr_result") or {}).get("facts", {})

        for chunk in safe_chunks:
            fields = _parse_chunk_fields(chunk.get("text", ""))
            if not fields:
                continue

            sources.append({
                "id": chunk.get("id"),
                "source_table": chunk.get("source_table"),
                "filename": chunk.get("filename"),
                "score": chunk.get("score"),
            })

            if not _looks_related(query, fields):
                continue

            matched_rules.append(mask_pii(fields.get("rule", "")))

            conditions = fields.get("conditions", "")
            if conditions and hr_facts:
                for k, v in hr_facts.items():
                    if str(v).lower() in conditions.lower():
                        break
                else:
                    conflicts.append(
                        f"The stated condition ({conditions}) could not be verified against employee data."
                    )

        recommendation = (
            " | ".join(matched_rules)
            if matched_rules
            else "No clear rule directly applicable to this request was found."
        )

        return {
            "recommendation": recommendation,
            "conflicts": conflicts,
            "sources": sources,
        }
