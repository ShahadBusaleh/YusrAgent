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
    r"\btake\b.{0,20}\bleave\b",
    r"\brequest(ing)?\b.{0,20}\bleave\b",
    r"\bapply(ing)?\s+for\s+leave\b",
    r"\bsubmit\b.{0,20}\bleave\b",
    r"\bleave\s+from\b",
    r"\bcover\s+(for\s+)?me\b",
    r"\bcover\s+my\b",
    r"\btime\s+off\b",
)

_LEAVE_TYPES = {"annual", "sick", "emergency"}


def detects_leave_submission_intent(query: str) -> bool:
    lowered = (query or "").lower()
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
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _coerce_days(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    days = _coerce_days(parsed.get("days"))
    reason = parsed.get("reason")
    reason = str(reason).strip() if reason else None

    if days is None and start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            if end >= start:
                days = float((end - start).days + 1)
        except ValueError:
            days = None

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
