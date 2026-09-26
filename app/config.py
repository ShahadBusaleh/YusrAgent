"""Environment-backed settings. Keep secrets in .env, never in code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else int(raw)


@dataclass(frozen=True)
class Settings:
    sqlite_path: Path
    jwt_secret: str
    jwt_access_minutes: int
    jwt_refresh_days: int
    api_base_url: str
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str
    embedding_model: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    # Every configured key (LLM_API_KEYS, else LLM_API_KEY); app/llm.py
    # rotates across them. llm_api_key stays the first one, so existing
    # "is a key configured?" checks keep working.
    llm_api_keys: tuple[str, ...] = ()


def _llm_keys() -> tuple[str, ...]:
    """LLM_API_KEYS="k1,k2,k3" (commas or newlines), falling back to the
    single LLM_API_KEY. Duplicates and blanks are dropped, order kept."""
    raw = os.getenv("LLM_API_KEYS") or os.getenv("LLM_API_KEY") or ""
    keys = [k.strip() for k in raw.replace("\n", ",").split(",")]
    return tuple(dict.fromkeys(k for k in keys if k))


def get_settings() -> Settings:
    sqlite = os.getenv("SQLITE_PATH", "agentic_hr.db")
    sqlite_path = Path(sqlite)
    if not sqlite_path.is_absolute():
        sqlite_path = ROOT / sqlite_path
    return Settings(
        sqlite_path=sqlite_path,
        jwt_secret=os.getenv("JWT_SECRET", "dev-only-change-me"),
        jwt_access_minutes=_int("JWT_ACCESS_MINUTES", 15),
        jwt_refresh_days=_int("JWT_REFRESH_DAYS", 7),
        api_base_url=os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        qdrant_url=os.getenv("QDRANT_URL", ""),
        qdrant_api_key=os.getenv("QDRANT_API_KEY", ""),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "yusor_policies"),
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        llm_api_key=(_llm_keys() or ("",))[0],
        llm_base_url=os.getenv(
            "LLM_BASE_URL", "https://api.openai.com/v1"
        ),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_api_keys=_llm_keys(),
    )


_REASONING_MODEL_MARKERS = ("gpt-oss", "o1", "o3", "o4", "gpt-5")


def fast_llm_options(max_completion_tokens: int) -> dict:
    """Extra chat.completions kwargs for short utility calls (intent
    classification, translation, field extraction).

    Why: on a tokens-per-minute budget (Groq on-demand: 8,000 TPM) the
    provider reserves prompt + max output per request, and reasoning
    models spend most of their output on hidden reasoning. A sized output
    cap plus low reasoning effort cut a translation from 328 to 117
    completion tokens with identical text. `reasoning_effort` is only sent
    to reasoning models (others reject it); LLM_FAST_REASONING_EFFORT=""
    disables it.
    """
    options: dict = {"max_completion_tokens": max_completion_tokens}
    effort = os.getenv("LLM_FAST_REASONING_EFFORT", "low").strip()
    model = get_settings().llm_model.lower()
    if effort and any(marker in model for marker in _REASONING_MODEL_MARKERS):
        options["reasoning_effort"] = effort
    return options
