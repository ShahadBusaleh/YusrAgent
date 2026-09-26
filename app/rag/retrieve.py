from __future__ import annotations

import time

from app.config import get_settings
from app.rag.client import get_qdrant_client
from app.rag.embeddings import embed_text
from app.rag.keyword import keyword_search


def _hit_to_chunk(hit) -> dict:
    payload = hit.payload or {}


    return {
    "id": payload.get("id"),
    "source_table": payload.get("source_table"),
    "source_name": payload.get("source_name"),
    "filename": payload.get("filename"),
    "text": payload.get("text"),
    "score": float(getattr(hit, "score", 0.0) or 0.0),
}

def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Hybrid-ish search: dense vectors plus keyword search."""

    total_start = time.perf_counter()

    settings = get_settings()

    # 1. Qdrant client
    start = time.perf_counter()
    client = get_qdrant_client()
    print(f"[RAG] get_qdrant_client: {(time.perf_counter() - start) * 1000:.2f} ms")

    # 2. Embedding
    start = time.perf_counter()
    vector = embed_text(query)
    print(f"[RAG] embed_text: {(time.perf_counter() - start) * 1000:.2f} ms")

    # 3. Dense search
    start = time.perf_counter()

    dense = client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )

    print(
        f"[RAG] dense query: "
        f"{(time.perf_counter() - start) * 1000:.2f} ms"
    )

    # 4. Keyword search
    start = time.perf_counter()

    # BM25 over the policy files (see app/rag/keyword.py): Qdrant's
    # MatchText needed every query word in one chunk, so it matched nothing.
    try:
        keyword = keyword_search(query, top_k=top_k)
    except Exception as e:
        print(f"[RAG] keyword search failed: {e}")
        keyword = []

    print(
        f"[RAG] keyword search: "
        f"{(time.perf_counter() - start) * 1000:.2f} ms"
    )

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

    print(
        f"[RAG] RRF: "
        f"{(time.perf_counter() - start) * 1000:.2f} ms"
    )

    print(
        f"[RAG] TOTAL: "
        f"{(time.perf_counter() - total_start) * 1000:.2f} ms"
    )

    return ordered[:top_k]
