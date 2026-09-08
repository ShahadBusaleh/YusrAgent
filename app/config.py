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
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv(
            "LLM_BASE_URL", "https://api.openai.com/v1"
        ),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )
