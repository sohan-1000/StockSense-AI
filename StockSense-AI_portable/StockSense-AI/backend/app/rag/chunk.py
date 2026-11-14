"""Chunking: ~1,800 characters (~450 tokens) with ~200 characters overlap, split on paragraph/sentence boundaries.
Chunks never cross a page boundary, so every chunk has an exact page reference for citations."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extract import Page

TARGET = 1800
OVERLAP = 200
MIN_CHUNK = 150


@dataclass
class Chunk:
    index: int
    page: int | None
    text: str


def _units(text: str) -> list[str]:
    """Paragraphs, with over-long paragraphs split into sentences."""
    units = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= TARGET:
            units.append(para)
        else:
            units.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip())
    return units


def _split_page(text: str) -> list[str]:
    chunks, cur = [], ""
    for u in _units(text):
        while len(u) > TARGET:  # a single huge sentence: hard split
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(u[:TARGET])
            u = u[TARGET - OVERLAP :]
        if cur and len(cur) + len(u) + 2 > TARGET:
            chunks.append(cur)
            tail = cur[-OVERLAP:]
            cur = (tail[tail.find(" ") + 1 :] + "\n" + u) if OVERLAP else u  # overlap starts at a word boundary
        else:
            cur = f"{cur}\n\n{u}" if cur else u
    if cur:
        if chunks and len(cur) < MIN_CHUNK:
            chunks[-1] += "\n\n" + cur
        else:
            chunks.append(cur)
    return chunks


def chunk_pages(pages: list[Page]) -> list[Chunk]:
    out: list[Chunk] = []
    for p in pages:
        for t in _split_page(p.text):
            out.append(Chunk(len(out), p.page, t))
    return out
