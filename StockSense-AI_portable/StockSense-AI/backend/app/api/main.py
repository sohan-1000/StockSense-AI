"""FastAPI app.   Run from backend/:  uvicorn app.api.main:app --reload --port 8000"""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import chat, config
from app.db.connection import owner_conn
from app.expiry import get_expiry
from app.rag import store
from app.rag.extract import SUPPORTED
from app.warmup import warm

logging.basicConfig(level=logging.INFO)
MAX_UPLOAD_MB = 20


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(warm)  # build catalog / DB connection / embedding model once, before requests
    yield


app = FastAPI(title="StockSense AI Chat API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=100)
    question: str = Field(min_length=1, max_length=1000)


class FeedbackRequest(BaseModel):
    rating: int = Field(description="1 = helpful, -1 = not helpful")
    comment: str | None = Field(default=None, max_length=1000)


@app.get("/api/health")
def health():
    return {"status": "ok", "documents_ready": store.ready_document_count()}


@app.post("/api/chat")
async def post_chat(req: ChatRequest):
    conv = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    res = await run_in_threadpool(chat.answer_question, req.question)
    message_id = await run_in_threadpool(chat.log_exchange, conv, req.question, res)
    return {"conversation_id": conv, "message_id": message_id, **res}


@app.post("/api/chat/{message_id}/feedback")
def post_feedback(message_id: int, fb: FeedbackRequest):
    if fb.rating not in (1, -1):
        raise HTTPException(422, "rating must be 1 or -1")
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM chat_log WHERE message_id=%s", (message_id,))
        if not cur.fetchone():
            raise HTTPException(404, "message not found")
        cur.execute("INSERT INTO chat_feedback (message_id, rating, comment) VALUES (%s,%s,%s)", (message_id, fb.rating, fb.comment))
    return {"status": "saved"}


@app.post("/api/documents", status_code=202)
async def upload_document(background: BackgroundTasks, file: UploadFile = File(...)):
    name = Path(file.filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(415, f"Unsupported file type '{ext}'. Supported: PDF, TXT, DOCX")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
    if not data:
        raise HTTPException(400, "Empty file")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe}"
    dest.write_bytes(data)
    doc_id, is_new = await run_in_threadpool(store.register_document, dest, name)
    if is_new:
        background.add_task(store.process_document, doc_id, dest)  # extraction + embedding run in the background
    else:
        dest.unlink(missing_ok=True)  # identical content already uploaded
    return {"document_id": doc_id, "status": "queued" if is_new else "already_uploaded"}


@app.get("/api/documents")
def documents():
    return store.list_documents()


@app.get("/api/documents/{document_id}/status")
def document_status(document_id: int):
    d = store.get_document(document_id)
    if not d:
        raise HTTPException(404, "document not found")
    return d


@app.get("/api/expiry")
async def expiry(days: int | None = Query(default=None, ge=0, le=3650, description="only batches expiring within this many days"),
                 category: str | None = Query(default=None, max_length=60),
                 search: str | None = Query(default=None, max_length=60)):
    """Expiry tab data: batches sorted by soonest expiry, plus summary counts. No LLM involved."""
    return await run_in_threadpool(get_expiry, days, category, search)


@app.get("/api/suggestions")
def suggestions():
    return [
        "Show me the top 5 products by revenue last month",
        "Which products are below their reorder point?",
        "Which batches expire within 7 days?",
        "Compare monthly revenue for 2026",
        "Which supplier generated the most revenue?",
    ]
