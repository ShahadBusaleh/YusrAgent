"""Password hashing isolated here so bcrypt/argon2 can replace SHA-256 later.

Seeded accounts in agentic_hr.db use unsalted SHA-256 of the shared dev
password. Do not use this scheme outside local development.
"""

from __future__ import annotations

import hashlib
import hmac


def hash_password(password: str) -> str:
    """Hash a password. Swap the body of this function to change schemes."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against users.password_hash."""
    if not password_hash:
        return False
    candidate = hash_password(password)
    return hmac.compare_digest(candidate, password_hash)
