"""Load each policy_texts file as one chunk and upsert into Qdrant."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client.http import models as qmodels

from app.config import ROOT, get_settings
from app.rag.client import get_qdrant_client
from app.rag.embeddings import embed_texts, embedding_dim

POLICY_ROOT = ROOT / "policy_texts"
SOURCE_DIRS = {
    "company_policies": POLICY_ROOT / "company_policies",
    "saudi_labor_law": POLICY_ROOT / "saudi_labor_law",
    "wps": POLICY_ROOT / "WPS",
}
SOURCE_NAMES = {
    "company_policies": "Company Policies",
    "saudi_labor_law": "Saudi Labor Law",
    "wps": "Wage Protection System (WPS) / Mudad",
}

def _point_id(source_table: str, source_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{source_table}:{source_id}"))


def _source_id_from_filename(path: Path) -> str:
    return path.stem


def load_chunks() -> list[dict]:
    chunks: list[dict] = []
    for source_table, folder in SOURCE_DIRS.items():
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.txt")):
            text = path.read_text(encoding="utf-8").strip()
            source_id = _source_id_from_filename(path)
            chunks.append(
                {
                    "id": source_id,
                    "source_table": source_table,
                    "filename": path.name,
                    "text": text,
                }
            )
    return chunks


def ensure_collection(client, collection: str, dim: int) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="text",
        field_schema=qmodels.TextIndexParams(
            type="text",
            tokenizer=qmodels.TokenizerType.WORD,
            min_token_len=2,
            max_token_len=40,
            lowercase=True,
        ),
    )


def ingest() -> int:
    settings = get_settings()
    chunks = load_chunks()
    if not chunks:
        raise RuntimeError(f"No policy files found under {POLICY_ROOT}")
    client = get_qdrant_client()
    dim = embedding_dim()
    ensure_collection(client, settings.qdrant_collection, dim)
    vectors = embed_texts([c["text"] for c in chunks])
    points = []
    for chunk, vector in zip(chunks, vectors):
        points.append(
            qmodels.PointStruct(
                id=_point_id(chunk["source_table"], chunk["id"]),
                vector=vector,
                payload={
                    "id": chunk["id"],
                    "source_table": chunk["source_table"],
                    "source_name": SOURCE_NAMES.get(
                        chunk["source_table"],
                        chunk["source_table"],
                    ),
                    "filename": chunk["filename"],
                    "text": chunk["text"],
                },
            )
        )
    client.upsert(collection_name=settings.qdrant_collection, points=points)
    return len(points)


if __name__ == "__main__":
    count = ingest()
    print(f"Upserted {count} policy chunks into Qdrant")
