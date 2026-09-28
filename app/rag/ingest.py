"""Load policy_texts into chunks and upsert into Qdrant.

Law and company-policy files are one chunk each. WPS files are one short
rule each (a single field such as "Bank account"), so they are merged into
one chunk per category: a question like "what payment details are in the
file?" needs WPS022-WPS025 together, which top-3 retrieval of single rules
could not return.
"""

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


def _fields(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields


def _wps_sections(chunks: list[dict]) -> list[dict]:
    """Merge single-rule WPS chunks into one chunk per category.

    The chunk id is the id range ("WPS021-WPS030"); "source_ids" lists the
    rule ids inside it, which answers may cite individually.
    """
    groups: dict[str, list[dict]] = {}
    for chunk in chunks:
        category = _fields(chunk["text"]).get("category") or chunk["id"]
        groups.setdefault(category, []).append(chunk)

    sections = []
    for category, members in groups.items():
        ids = [m["id"] for m in members]
        section_id = ids[0] if len(ids) == 1 else f"{ids[0]}-{ids[-1]}"
        source = _fields(members[0]["text"]).get("source", "")
        lines = [
            f"Section ID: {section_id}",
            f"Category: {category}",
            f"Source: {source}",
        ]
        for member in members:
            fields = _fields(member["text"])
            lines.append("")
            lines.append(f"{member['id']} - {fields.get('topic', '')}")
            lines.append(f"Rule: {fields.get('rule', '')}")
            if fields.get("conditions") not in (None, "", "—"):
                lines.append(f"Conditions: {fields['conditions']}")
            if fields.get("exceptions") not in (None, "", "—"):
                lines.append(f"Exceptions: {fields['exceptions']}")
        sections.append(
            {
                "id": section_id,
                "source_ids": ids,
                "source_table": "wps",
                "filename": ", ".join(m["filename"] for m in members),
                "text": "\n".join(lines),
            }
        )
    return sections


def load_chunks(root: Path = POLICY_ROOT) -> list[dict]:
    chunks: list[dict] = []
    for source_table, folder in SOURCE_DIRS.items():
        folder = root / folder.relative_to(POLICY_ROOT)
        if not folder.exists():
            continue
        table_chunks = []
        for path in sorted(folder.glob("*.txt")):
            text = path.read_text(encoding="utf-8").strip()
            source_id = _source_id_from_filename(path)
            table_chunks.append(
                {
                    "id": source_id,
                    "source_ids": [source_id],
                    "source_table": source_table,
                    "filename": path.name,
                    "text": text,
                }
            )
        if source_table == "wps":
            table_chunks = _wps_sections(table_chunks)
        chunks.extend(table_chunks)
    return chunks


def delete_single_wps_points(client, collection: str, root: Path = POLICY_ROOT) -> None:
    """Remove the per-file WPS points left over from before sections."""
    folder = root / SOURCE_DIRS["wps"].relative_to(POLICY_ROOT)
    ids = [_point_id("wps", path.stem) for path in folder.glob("*.txt")]
    if ids:
        client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=ids),
        )


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
    delete_single_wps_points(client, settings.qdrant_collection)
    vectors = embed_texts([c["text"] for c in chunks])
    points = []
    for chunk, vector in zip(chunks, vectors):
        points.append(
            qmodels.PointStruct(
                id=_point_id(chunk["source_table"], chunk["id"]),
                vector=vector,
                payload={
                    "id": chunk["id"],
                    "source_ids": chunk["source_ids"],
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
