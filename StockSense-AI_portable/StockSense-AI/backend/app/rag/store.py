"""Document registry + vector store on PostgreSQL/pgvector (same database as the structured data)."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from psycopg2.extras import execute_values

from app import config
from app.db.connection import owner_conn
from app.embeddings import embed_passages, embed_query
from app.rag.chunk import chunk_pages
from app.rag.extract import SUPPORTED, extract

log = logging.getLogger(__name__)


@dataclass
class Hit:
    document: str
    page: int | None
    chunk_index: int
    text: str
    score: float


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def register_document(path: Path, filename: str) -> tuple[int, bool]:
    """Create (or find, by content hash) the document row. Returns (document_id, is_new). Idempotent."""
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"Unsupported file type '{path.suffix}'. Supported: PDF, TXT, DOCX")
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT document_id, status FROM documents WHERE file_hash=%s", (h,))
        row = cur.fetchone()
        if row and row[1] in ("ready", "processing", "queued"):
            return row[0], False
        if row:  # previously failed -> retry
            cur.execute("UPDATE documents SET status='queued', error=NULL, filename=%s WHERE document_id=%s", (filename, row[0]))
            return row[0], True
        cur.execute("INSERT INTO documents (filename, file_hash) VALUES (%s, %s) RETURNING document_id", (filename, h))
        return cur.fetchone()[0], True


def process_document(document_id: int, path: Path) -> None:
    """extract -> clean -> chunk -> embed -> store. Updates status; never raises (errors are recorded)."""
    def set_status(status, error=None, pages=None, chunks=None):
        with owner_conn() as conn:
            conn.cursor().execute(
                "UPDATE documents SET status=%s, error=%s, page_count=COALESCE(%s,page_count), chunk_count=COALESCE(%s,chunk_count) WHERE document_id=%s",
                (status, error, pages, chunks, document_id))

    try:
        set_status("processing")
        pages = extract(path)
        chunks = chunk_pages(pages)
        if not chunks:
            raise ValueError("No extractable text found (scanned/image-only PDFs need OCR, which is not supported yet).")
        vectors = embed_passages([c.text for c in chunks])
        with owner_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM doc_chunks WHERE document_id=%s", (document_id,))  # re-processing replaces old chunks
            execute_values(cur, "INSERT INTO doc_chunks (document_id, chunk_index, page, content, embedding) VALUES %s",
                           [(document_id, c.index, c.page, c.text, _vec(v)) for c, v in zip(chunks, vectors)])
        set_status("ready", pages=len(pages), chunks=len(chunks))
    except Exception as e:  # noqa: BLE001
        log.exception("document processing failed")
        set_status("failed", error=str(e)[:500])


def ingest_file(path: Path, filename: str | None = None) -> int:
    """Synchronous convenience wrapper (CLI / tests)."""
    doc_id, is_new = register_document(path, filename or path.name)
    if is_new:
        process_document(doc_id, path)
    return doc_id


def get_document(document_id: int) -> dict | None:
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT document_id, filename, status, error, page_count, chunk_count, created_at FROM documents WHERE document_id=%s", (document_id,))
        r = cur.fetchone()
    return None if not r else dict(zip(["document_id", "filename", "status", "error", "page_count", "chunk_count", "created_at"], r))


def list_documents() -> list[dict]:
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT document_id, filename, status, error, page_count, chunk_count, created_at FROM documents ORDER BY document_id")
        rows = cur.fetchall()
    return [dict(zip(["document_id", "filename", "status", "error", "page_count", "chunk_count", "created_at"], r)) for r in rows]


def ready_document_count() -> int:
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM documents WHERE status='ready'")
        return cur.fetchone()[0]


def search(question: str, k: int = 20) -> list[Hit]:
    """Top-k chunks by cosine similarity from documents that are ready."""
    qv = _vec(embed_query(question))
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT d.filename, c.page, c.chunk_index, c.content, 1 - (c.embedding <=> %s::vector) AS score
               FROM doc_chunks c JOIN documents d USING (document_id)
               WHERE d.status = 'ready'
               ORDER BY c.embedding <=> %s::vector LIMIT %s""", (qv, qv, k))
        return [Hit(*r) for r in cur.fetchall()]
