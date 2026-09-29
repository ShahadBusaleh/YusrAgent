from __future__ import annotations

import logging
import re
import time

from app.config import get_settings
from app.rag.client import get_qdrant_client
from app.rag.embeddings import embed_text
from app.rag.ingest import SOURCE_NAMES, load_chunks
from app.rag.keyword import keyword_search
from app.rag.query_expansion import expand_query

logger = logging.getLogger(__name__)


def _hit_to_chunk(hit) -> dict:
    payload = hit.payload or {}
    return {
        "id": payload.get("id"),
        "source_ids": payload.get("source_ids") or [payload.get("id")],
        "source_table": payload.get("source_table"),
        "source_name": payload.get("source_name"),
        "filename": payload.get("filename"),
        "text": payload.get("text"),
        "score": float(getattr(hit, "score", 0.0) or 0.0),
    }


_ARTICLE_RE = re.compile(r"(?:\barticle|\bart\.|المادة)\s*(\d{1,3})\b", re.IGNORECASE)


def cited_articles(query: str) -> set[str]:
    """Article numbers the question names ("Article 109", "المادة 109")."""
    return set(_ARTICLE_RE.findall(query or ""))


def chunk_article(chunk: dict) -> str | None:
    match = re.search(r"^Article:\s*(\d+)\s*$", chunk.get("text") or "", re.MULTILINE)
    return match.group(1) if match else None


def _article_chunks(articles: set[str]) -> list[dict]:
    # A named article is split across several LAW rows (Article 109 is
    # LAW037-LAW043). Similarity search only surfaced some of them, so
    # "What does Article 109 say?" missed the 21/30-day entitlement.
    return [
        {**chunk, "source_name": SOURCE_NAMES.get(chunk["source_table"]), "score": 1.0}
        for chunk in load_chunks()
        if chunk.get("source_table") == "saudi_labor_law"
        and chunk_article(chunk) in articles
    ]


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Hybrid-ish search: dense vectors plus keyword search."""

    total_start = time.perf_counter()

    settings = get_settings()
    articles = cited_articles(query)
    query = expand_query(query)

    # 1. Qdrant client
    start = time.perf_counter()
    client = get_qdrant_client()
    logger.debug("get_qdrant_client: %.2f ms", (time.perf_counter() - start) * 1000)

    # 2. Embedding
    start = time.perf_counter()
    vector = embed_text(query)
    logger.debug("embed_text: %.2f ms", (time.perf_counter() - start) * 1000)

    # 3. Dense search
    start = time.perf_counter()

    dense = client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )

    logger.debug("dense query: %.2f ms", (time.perf_counter() - start) * 1000)

    # 4. Keyword search
    start = time.perf_counter()

    # BM25 over the policy files (see app/rag/keyword.py): Qdrant's
    # MatchText needed every query word in one chunk, so it matched nothing.
    try:
        keyword = keyword_search(query, top_k=top_k)
    except Exception as e:
        logger.warning("keyword search failed: %s", e)
        keyword = []

    logger.debug("keyword search: %.2f ms", (time.perf_counter() - start) * 1000)

    # 5. RRF
    start = time.perf_counter()

    ranked: dict[str, dict] = {}
    k = 60

    for rank, hit in enumerate(dense.points, start=1):
        chunk = _hit_to_chunk(hit)
        key = f"{chunk.get('source_table')}:{chunk.get('id')}"

        ranked[key] = {
            **chunk,
            "score": ranked.get(key, {}).get("score", 0.0)
            + 1.0 / (k + rank),
        }

    for rank, chunk in enumerate(keyword, start=1):
        key = f"{chunk.get('source_table')}:{chunk.get('id')}"

        ranked[key] = {
            **chunk,
            "score": ranked.get(key, {}).get("score", 0.0)
            + 1.0 / (k + rank),
        }

    ordered = sorted(
        ranked.values(),
        key=lambda c: c["score"],
        reverse=True,
    )

    logger.debug("RRF: %.2f ms", (time.perf_counter() - start) * 1000)

    logger.debug("TOTAL: %.2f ms", (time.perf_counter() - total_start) * 1000)

    if articles:
        pinned = _article_chunks(articles)
        pinned_keys = {f"{c['source_table']}:{c['id']}" for c in pinned}
        ordered = pinned + [
            c for c in ordered
            if f"{c.get('source_table')}:{c.get('id')}" not in pinned_keys
        ]
        top_k = max(top_k, len(pinned))

    return ordered[:top_k]
