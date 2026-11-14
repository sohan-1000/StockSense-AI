"""Local embeddings (Sarvam offers no embeddings API). fastembed runs ONNX on CPU, no torch needed.
The model (~130 MB) is downloaded once on first use."""
from __future__ import annotations

import math
from functools import lru_cache

from app import config

_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # recommended for bge retrieval queries


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=config.EMBED_MODEL)


def _norm(v) -> list[float]:
    v = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [_norm(v) for v in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    return _norm(next(iter(_model().embed([_QUERY_PREFIX + text]))))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
