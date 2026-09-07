"""Shared embedding model loader."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=1)
def get_embedder():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    return SentenceTransformer(settings.embedding_model)


def embed_text(text: str) -> list[float]:
    vector = get_embedder().encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = get_embedder().encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embedding_dim() -> int:
    return int(get_embedder().get_sentence_embedding_dimension())
