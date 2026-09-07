"""Qdrant Cloud client."""

from __future__ import annotations

from qdrant_client import QdrantClient

from app.config import get_settings


def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    if not settings.qdrant_url:
        raise RuntimeError("QDRANT_URL is not set")
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
