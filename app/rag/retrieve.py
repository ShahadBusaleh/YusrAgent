"""Vector (+ keyword) retrieval. Returns chunks with source ids for citations."""

from __future__ import annotations

from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.rag.client import get_qdrant_client
from app.rag.embeddings import embed_text


def _hit_to_chunk(hit) -> dict:
    payload = hit.payload or {}
    return {
        "id": payload.get("id"),
        "source_table": payload.get("source_table"),
        "filename": payload.get("filename"),
        "text": payload.get("text"),
        "score": float(hit.score or 0.0),
    }


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Hybrid-ish search: dense vectors plus a payload text match, fused by RRF."""
    settings = get_settings()
    client = get_qdrant_client()
    vector = embed_text(query)

    dense = client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )
    try:
        keyword = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="text",
                        match=qmodels.MatchText(text=query),
                    )
                ]
            ),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )[0]
    except Exception:
        keyword = []

    ranked: dict[str, dict] = {}
    k = 60
    for rank, hit in enumerate(dense.points, start=1):
        chunk = _hit_to_chunk(hit)
        key = f"{chunk.get('source_table')}:{chunk.get('id')}"
        ranked[key] = {**chunk, "score": ranked.get(key, {}).get("score", 0.0) + 1.0 / (k + rank)}
    for rank, hit in enumerate(keyword, start=1):
        chunk = _hit_to_chunk(hit)
        key = f"{chunk.get('source_table')}:{chunk.get('id')}"
        ranked[key] = {**chunk, "score": ranked.get(key, {}).get("score", 0.0) + 1.0 / (k + rank)}

    ordered = sorted(ranked.values(), key=lambda c: c["score"], reverse=True)
    return ordered[:top_k]
