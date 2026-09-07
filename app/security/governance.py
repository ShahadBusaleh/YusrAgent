"""Rule-based governance checks used later by the Manager Agent.

Six architecture checks: authorization, PII, policy/risk, prompt-injection,
output validation, rate-limit/safety. Human approval is derived from risk.
"""

from __future__ import annotations

import re
import time
from typing import Literal

RiskLevel = Literal["low", "medium", "high"]

HIGH_ACTIONS = {
    "bank_change",
    "iban_change",
    "salary_change",
    "compensation_change",
    "employment_status_change",
    "termination",
    "disciplinary",
    "sensitive_change",
    "payroll_change",
    "sensitive_change_request",
}
MEDIUM_ACTIONS = {
    "leave_request",
    "leave_cancel",
    "benefits_claim",
    "job_title_change",
}
LOW_ACTIONS = {
    "personal_info_update",
    "policy_query",
    "leave_balance_query",
    "profile_view",
    "mobile_update",
    "address_update",
    "email_update",
}
SENSITIVE_PAYLOAD_KEYS = {
    "iban",
    "new_iban",
    "bank_code",
    "new_bank_code",
    "bank_name",
    "basic_salary",
    "salary",
    "national_id",
}

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any|previous|prior) instructions", re.I),
    re.compile(r"you are now", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"disregard (the|your) (rules|policy|instructions)", re.I),
    re.compile(r"<\|?(system|assistant)\|?>", re.I),
    re.compile(r"override (safety|policy|guardrail)", re.I),
]

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+966|05)\d{8,9}\b")
_NATIONAL_ID_RE = re.compile(r"\b[12]\d{9}\b")

_RATE_HITS: dict[str, list[float]] = {}


def classify_risk(action_type: str, payload: dict) -> RiskLevel:
    """If/else risk rules. Not an LLM call."""
    payload = payload or {}
    key = (action_type or "").strip().lower()
    fields = {str(k).lower() for k in payload}
    if fields & SENSITIVE_PAYLOAD_KEYS:
        return "high"
    if key in HIGH_ACTIONS:
        return "high"
    if any(token in key for token in ("bank", "iban", "salary", "terminat", "disciplin")):
        return "high"
    if key in MEDIUM_ACTIONS or "leave" in key:
        return "medium"
    if key in LOW_ACTIONS or "personal" in key or key.endswith("_query"):
        return "low"
    return "medium"


def requires_human_approval(risk: RiskLevel) -> bool:
    """Low-risk may auto-execute; medium and high go to pending_approvals."""
    return risk in ("medium", "high")


def check_authorization(user: dict, resource: dict) -> bool:
    """Least-privilege check against the requesting user's role."""
    role = (user or {}).get("role")
    user_eid = (user or {}).get("employee_id")
    rtype = (resource or {}).get("type")
    target_eid = (resource or {}).get("employee_id")

    if not role:
        return False
    if role == "admin":
        return True
    if rtype in {"users", "roles", "audit_log"}:
        return False
    if rtype == "pending_approvals":
        return role == "hr_manager"
    if rtype in {"employee_profile", "employee_update"}:
        if role in {"hr_specialist", "hr_manager"}:
            return True
        return bool(target_eid) and target_eid == user_eid
    if rtype in {"leave_balance", "leave_request", "proposed_action"}:
        if role in {"hr_specialist", "hr_manager"}:
            return True
        return bool(target_eid) and target_eid == user_eid
    if rtype == "agent_query":
        return role in {"employee", "hr_specialist", "hr_manager", "admin"}
    return False


def mask_pii(text: str) -> str:
    """Mask common HR PII so agent output cannot leak identifiers."""
    if not text:
        return text
    masked = _IBAN_RE.sub("[IBAN]", text)
    masked = _EMAIL_RE.sub("[EMAIL]", masked)
    masked = _PHONE_RE.sub("[PHONE]", masked)
    masked = _NATIONAL_ID_RE.sub("[NATIONAL_ID]", masked)
    return masked


def detect_prompt_injection(text: str) -> bool:
    """Heuristic prompt-injection detector for inputs and retrieved docs."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def sanitize_input(text: str) -> str:
    cleaned = (text or "").replace("\x00", "").strip()
    return cleaned[:4000]


def validate_output(response: str, sources: list) -> bool:
    """Groundedness check: non-empty sources and lexical overlap with the answer."""
    if not response or not str(response).strip():
        return False
    if not sources:
        return False
    blob_parts: list[str] = []
    for src in sources:
        if isinstance(src, dict):
            blob_parts.append(str(src.get("text") or src.get("id") or ""))
        else:
            blob_parts.append(str(src))
    blob = " ".join(blob_parts).lower()
    if not blob.strip():
        return False
    tokens = [t for t in re.findall(r"[a-zA-Z0-9_]{4,}", str(response).lower())]
    if not tokens:
        return True
    overlap = sum(1 for t in tokens if t in blob)
    return overlap / max(len(tokens), 1) >= 0.08


def check_rate_limit(user_id: str, limit: int = 30, window_seconds: int = 60) -> bool:
    """Return True if the caller is within the window budget."""
    now = time.time()
    hits = [t for t in _RATE_HITS.get(user_id, []) if now - t < window_seconds]
    if len(hits) >= limit:
        _RATE_HITS[user_id] = hits
        return False
    hits.append(now)
    _RATE_HITS[user_id] = hits
    return True
