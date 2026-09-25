"""Leave-submission intent detection and field extraction.

Shared by HRAgent (builds the proposed_action) and OrchestratorAgent
(decides whether a query needs HR at all). Pure functions, no DB access.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from app.config import get_settings

_LEAVE_INTENT_PATTERNS = (
    r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?:request|apply\s+for|submit|book)\s+(?:my\s+|a\s+)?(?:(?:annual|sick|emergency)\s+)?(?:leave|time\s+off)\b",
    r"\bi\s+(?:want|need|would\s+like)\s+to\s+(?:take|request|apply\s+for|submit|book)\s+(?:my\s+|a\s+)?(?:(?:annual|sick|emergency)\s+)?(?:leave|time\s+off)\b",
    r"\bi\s+am\s+(?:requesting|applying\s+for)\s+(?:(?:annual|sick|emergency)\s+)?leave\b",
)

_LEAVE_TYPES = {"annual", "sick", "emergency"}


def detects_leave_submission_intent(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if re.search(r"\b(?:how|policy|rules|eligible|eligibility|cancel|withdraw)\b|\b(?:do not|don't|not to)\b", lowered):
        return False
    return any(re.search(pattern, lowered) for pattern in _LEAVE_INTENT_PATTERNS)


def _coerce_leave_type(value: Any) -> str | None:
    if not value:
        return None
    lowered = str(value).strip().lower()
    return lowered if lowered in _LEAVE_TYPES else None


def _coerce_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed.isoformat()


def extract_leave_fields(query: str) -> dict:
    """Extract leave_type/start_date/end_date/days/reason from free text.

    Returns {} on any failure (missing key, LLM error, bad JSON) so the
    caller can uniformly treat that as "missing information" rather than
    fabricating a leave request.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return {}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

        system_prompt = (
            "Extract leave request details from the employee's message. "
            f"Today's date is {date.today().isoformat()}. "
            "Respond with strict JSON only, no prose, matching exactly this shape: "
            '{"leave_type": "annual"|"sick"|"emergency"|null, '
            '"start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null, '
            '"days": number|null, "reason": string|null}. '
            "Use null for anything not clearly stated. Never guess a date."
        )

        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        content = (response.choices[0].message.content or "").strip()
        parsed = json.loads(content)
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}

    leave_type = _coerce_leave_type(parsed.get("leave_type"))
    start_date = _coerce_date(parsed.get("start_date"))
    end_date = _coerce_date(parsed.get("end_date"))
    days = None
    reason = parsed.get("reason")
    reason = str(reason).strip() if reason else None

    today = date.today()
    if any(date.fromisoformat(value) < today for value in (start_date, end_date) if value):
        return {}
    if start_date and end_date:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < start:
            return {}
        # Inclusive calendar days, matching the existing leave convention.
        days = float((end - start).days + 1)

    result: dict[str, Any] = {}
    if leave_type:
        result["leave_type"] = leave_type
    if start_date:
        result["start_date"] = start_date
    if end_date:
        result["end_date"] = end_date
    if days is not None:
        result["days"] = days
    if reason:
        result["reason"] = reason
    return result
