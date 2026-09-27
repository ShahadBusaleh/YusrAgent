"""Add policy vocabulary to user questions before retrieval.

Employees ask in everyday words the policy texts don't use: "carry forward"
unused leave (Article 110 says "postpone ... to the following year"), or
"what do I do when I get sick" (AAM-POL-015 says "notify ... on the first
day of sickness ... medical certificate"). Those articles ranked 4th-7th and
were cut by the Consultant's top-3, so the answer missed them. Each rule adds
the policy's own terms when the question uses the everyday phrasing.
"""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"carry(?:ing)?[\s-]*(?:it|them|this|these|those|my\s+\w+)?[\s-]*(?:forward|over)"
            r"|roll(?:ing)?[\s-]*(?:it|them)?[\s-]*over|rollover"
            r"|unused\s+(?:annual\s+)?leave.*\bnext\s+year"
            r"|accumulat\w*\s+(?:annual\s+)?leave"
            r"|defer\w*\s+(?:my\s+|annual\s+)*leave",
            re.IGNORECASE,
        ),
        "postpone postponement annual leave following year employer approval",
    ),
    (
        # Only the "what do I have to do" side of sick leave; entitlement
        # questions ("how many paid sick days") already match LAW060-062.
        re.compile(
            r"\b(?:sick|ill|illness|unwell)\b.*\b(?:must|should|have\s+to|need\s+to|report|notify|tell|inform|miss\w*|absent|procedure|steps?)\b"
            r"|\b(?:must|should|have\s+to|need\s+to|report|notify|tell|inform|miss\w*|absent|procedure|steps?)\b.*\b(?:sick|ill|illness|unwell)\b",
            re.IGNORECASE,
        ),
        "notify department head first day of sickness medical certificate",
    ),
    (
        # WPS003 names the platform ("handled through Mudad"); employees
        # ask how to "access" or "log in to" the Wage Protection System.
        re.compile(
            r"\b(?:access\w*|log\s*in|login|sign\s*in|use|reach)\b.*\b(?:wage\s+protection|wps)\b"
            r"|\b(?:wage\s+protection|wps)\b.*\b(?:access\w*|log\s*in|login|sign\s*in)\b",
            re.IGNORECASE,
        ),
        "Mudad platform",
    ),
    (
        # The WPS rows call the per-employee fields "wage record" items
        # (WPS022-WPS025); "payment details" alone matched the file-header
        # rows (WPS011, WPS015) instead.
        re.compile(
            r"\b(?:employee|payment|wage)\s+(?:payment\s+)?(?:details|information|data|fields)\b",
            re.IGNORECASE,
        ),
        "wage record identifies employee name bank account bank identifier payment information",
    ),
    (
        # "wage components" is the question's word; the rows name each one
        # (WPS026-WPS029).
        re.compile(
            r"\b(?:wage|salary|pay)\s+(?:components?|elements?|breakdown)\b"
            r"|\b(?:types?|kinds?|parts?)\s+of\s+(?:wages?|salary|pay)\b",
            re.IGNORECASE,
        ),
        "wage record basic wage housing allowance other payments total entitlements",
    ),
]


def expand_query(query: str) -> str:
    """The query plus policy terms for any everyday phrasing it uses."""
    extra = [terms for pattern, terms in _RULES if pattern.search(query or "")]
    return f"{query} {' '.join(extra)}" if extra else query
