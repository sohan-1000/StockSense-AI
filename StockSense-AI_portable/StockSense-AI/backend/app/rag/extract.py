"""Text extraction (PDF / DOCX / TXT) and cleaning. Page numbers are kept for citations where the format has them."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Page:
    page: int | None
    text: str


SUPPORTED = {".pdf", ".txt", ".docx"}


def extract(path: Path) -> list[Page]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [Page(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    elif ext == ".docx":
        from docx import Document

        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:  # tables often hold terms/prices
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        pages = [Page(None, "\n\n".join(parts))]
    elif ext == ".txt":
        pages = [Page(None, path.read_text(encoding="utf-8", errors="replace"))]
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: PDF, TXT, DOCX")
    return clean(pages)


def clean(pages: list[Page]) -> list[Page]:
    # drop lines repeated on most pages (running headers / footers / page numbers)
    repeated: set[str] = set()
    if len(pages) >= 4:
        counts = Counter(l.strip() for p in pages for l in set(p.text.splitlines()) if l.strip())
        repeated = {l for l, c in counts.items() if c >= max(3, int(0.6 * len(pages))) and len(l) < 120}
    out = []
    for p in pages:
        lines = [l for l in p.text.splitlines() if l.strip() not in repeated and not re.fullmatch(r"\s*(page\s*)?\d+\s*(of\s*\d+)?\s*", l, re.I)]
        t = "\n".join(lines)
        t = re.sub(r"-\n(?=[a-z])", "", t)          # re-join words hyphenated across lines
        t = re.sub(r"[ \t ]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        if t:
            out.append(Page(p.page, t))
    return out
