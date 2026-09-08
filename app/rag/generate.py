"""Answer a policy question using retrieved chunks only. Not Consultant Agent reasoning."""

from __future__ import annotations

from app.config import get_settings
from app.rag.retrieve import retrieve

SYSTEM_PROMPT = """You answer HR policy questions using only the retrieved documents.
If the documents do not contain the answer, say so clearly.
Cite source ids (for example LAW037 or AAM-POL-024) in the answer.
Do not invent policy, numbers, or conditions that are not in the documents."""


def _format_chunks(chunks: list[dict]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(
            f"[{chunk.get('id')}] ({chunk.get('source_table')})\n{chunk.get('text') or ''}"
        )
    return "\n\n".join(parts)


def _llm_answer(query: str, chunks: list[dict]) -> str:
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Add it to your local .env file, then retry."
        )
    from openai import OpenAI

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Question:\n{query}\n\n"
                    f"Retrieved documents:\n{_format_chunks(chunks)}"
                ),
            },
        ],
        temperature=0,
    )
    return (response.choices[0].message.content or "").strip()


def answer_with_rag(query: str, top_k: int = 5) -> dict:
    chunks = retrieve(query, top_k=top_k)
    if not chunks:
        return {
            "answer": "No policy documents were retrieved for this question.",
            "sources": [],
            "chunks": [],
        }
    return {
        "answer": _llm_answer(query, chunks),
        "sources": [c.get("id") for c in chunks if c.get("id")],
        "chunks": chunks,
    }


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]).strip() or "What is the minimum annual leave per year?"
    result = answer_with_rag(query)
    print(f"Query: {query}\n")
    print("Answer:")
    print(result["answer"])
    print("\nSources:", ", ".join(result["sources"]) or "(none)")
