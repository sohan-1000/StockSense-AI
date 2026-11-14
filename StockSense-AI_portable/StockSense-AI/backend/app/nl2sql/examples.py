"""Few-shot example retrieval: embed the example questions once, return the k most similar to the user question.
Falls back to the first k examples if the embedding model is unavailable (never blocks the pipeline)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)
PATH = Path(__file__).parent / "examples.yaml"


@lru_cache(maxsize=1)
def _load() -> list[dict]:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _vectors():
    from app.embeddings import embed_passages

    return embed_passages([e["q"] for e in _load()])


def top_k(question: str, k: int = 5) -> list[dict]:
    examples = _load()
    try:
        from app.embeddings import cosine, embed_query

        qv = embed_query(question)
        scored = sorted(zip(examples, _vectors()), key=lambda p: cosine(qv, p[1]), reverse=True)
        return [e for e, _ in scored[:k]]
    except Exception as e:  # noqa: BLE001
        log.warning("example retrieval failed (%s); using first %d examples", e, k)
        return examples[:k]
