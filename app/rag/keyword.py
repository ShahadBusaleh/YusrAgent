"""In-memory BM25 keyword search over the policy files.

The keyword half of hybrid retrieval. Qdrant's MatchText filter needs every
query word to appear in one chunk, so natural questions matched nothing and
it was switched off, leaving dense-only search — which ranked "annual leave
after five years" (LAW038) above the basic 21-day rule (LAW037) for "what is
the duration of annual leave?". BM25 scores partial matches, and the corpus
(the same chunks as ingested: one per law/policy file, one per WPS
category) fits in memory.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from app.rag.ingest import SOURCE_NAMES, load_chunks

_K1 = 1.5
_B = 0.75

_STOP_WORDS = {
    "the", "and", "for", "are", "what", "how", "many", "much", "does", "can",
    "under", "according", "with", "from", "this", "that", "have", "has", "who",
    "which", "when", "will", "would", "should", "about", "into", "per", "any",
    "you", "your", "get", "their", "its", "law", "saudi", "labor", "policy",
    "company", "rule", "rules", "article", "applies", "apply", "applicable",
}


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) >= 3 and token not in _STOP_WORDS
    ]


@lru_cache(maxsize=1)
def _index() -> tuple[list[dict], list[Counter], dict[str, float], float]:
    chunks = load_chunks()
    counts = [Counter(_tokens(chunk["text"])) for chunk in chunks]
    doc_freq = Counter(token for count in counts for token in count)
    total = len(chunks)
    idf = {
        token: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
        for token, freq in doc_freq.items()
    }
    avg_len = sum(sum(c.values()) for c in counts) / max(total, 1)
    return chunks, counts, idf, avg_len


def keyword_search(query: str, top_k: int = 5) -> list[dict]:
    """Top chunks by BM25, in the same shape as retrieve()'s chunks."""
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return []
    chunks, counts, idf, avg_len = _index()

    scored = []
    for chunk, count in zip(chunks, counts):
        length = sum(count.values())
        score = 0.0
        for token in query_tokens:
            freq = count.get(token)
            if freq:
                score += idf[token] * freq * (_K1 + 1) / (
                    freq + _K1 * (1 - _B + _B * length / avg_len)
                )
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            **chunk,
            "source_name": SOURCE_NAMES.get(chunk["source_table"], chunk["source_table"]),
            "score": score,
        }
        for score, chunk in scored[:top_k]
    ]
