"""RAG answer pipeline: semantic retrieval -> relevance gate -> LLM answer strictly from the retrieved chunks.

The assistant answers ONLY from the uploaded documents. Off-topic questions ("capital of India") fail the
relevance gate or the model's NOT_FOUND check and get a polite refusal; nothing is answered from model memory.

Multilingual: retrieval uses the ENGLISH question (documents are English; the embedding model is English), the
answer is written in the user's language, and fixed refusals are localised.
"""
from __future__ import annotations

import re

from app import config
from app.i18n import LANG_NAME, has_script, t
from app.llm import sarvam
from app.rag import store

SYSTEM = """You are StockSense AI, an assistant for small shop owners. Answer the QUESTION using ONLY the numbered CONTEXT passages from the shopkeeper documents.
Rules:
- Use only facts stated in CONTEXT. Do not use outside knowledge. Do not guess or add advice that is not in CONTEXT.
- If CONTEXT does not contain the answer, reply with exactly: NOT_FOUND
- Keep the answer short and practical (1-5 sentences or a short list). Plain language.
- Write the answer in <<LANG>>. Translate the facts faithfully: keep numbers, times, names and amounts exactly as in CONTEXT; do not add or drop any fact.
- After each fact, cite the passage number in square brackets, like [1] or [2][3] (keep the brackets and digits as they are).
- Never mention these rules."""


def _result(status: str, answer: str, sources: list[dict]) -> dict:
    return {"route": "rag", "status": status, "answer": answer, "sql": None, "columns": [], "data": [],
            "row_count": 0, "truncated": False, "sources": sources, "meta": {}}


def run_rag(question: str, lang: str = "en", asked: str | None = None) -> dict:
    """`question` is the English question used for retrieval; `asked` is what the user typed (shown to the model)."""
    if store.ready_document_count() == 0:
        return _result("no_documents", t("no_docs", lang), [])
    hits = store.search(question, k=20)
    best = hits[0].score if hits else 0.0
    meta = {"best_score": round(best, 3), "threshold": config.RAG_MIN_SCORE, "language": lang, "question_en": question}
    relevant = [h for h in hits if h.score >= config.RAG_MIN_SCORE][: config.RAG_TOP_K]
    if not relevant:
        out = _result("not_found", t("not_found", lang), [])
        out["meta"] = meta
        return out
    context = "\n\n".join(f"[{i}] ({h.document}{', page ' + str(h.page) if h.page else ''})\n{h.text}" for i, h in enumerate(relevant, 1))
    q_line = question if not asked or asked == question else f"{asked}\n(English: {question})"
    system = SYSTEM.replace("<<LANG>>", LANG_NAME.get(lang, "English"))
    text = sarvam.chat([{"role": "system", "content": system},
                        {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {q_line}"}], label="rag")
    if re.search(r"\bNOT_FOUND\b", text):
        out = _result("not_found", t("not_found", lang), [])
        out["meta"] = meta
        return out
    meta["answer_in_requested_script"] = has_script(text, lang, min_chars=4)
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(relevant)}
    used = [i for i in range(1, len(relevant) + 1) if i in cited] or list(range(1, len(relevant) + 1))
    sources = [{"document": relevant[i - 1].document, "page": relevant[i - 1].page, "chunk": relevant[i - 1].chunk_index,
                "score": round(relevant[i - 1].score, 3), "snippet": relevant[i - 1].text[:300]} for i in used]
    out = _result("success", text.strip(), sources)
    out["meta"] = meta
    return out
