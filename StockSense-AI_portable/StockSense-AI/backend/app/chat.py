"""Chat orchestrator: one LLM call routes (and translates) the question, then the SQL or RAG pipeline answers it.

Languages: English, Hindi, Telugu. The reply is written in the language of the question.
(Conversational memory is intentionally NOT implemented yet; `conversation_id` is accepted and logged so it can be
added later without an API change.)
"""
from __future__ import annotations

import logging

from app import chart
from app.db.connection import owner_conn
from app.i18n import detect_language, t
from app.llm.sarvam import LLMError
from app.nl2sql import linker, pipeline as sql_pipeline
from app.rag import pipeline as rag_pipeline

log = logging.getLogger(__name__)


def _base(route: str, status: str, answer: str) -> dict:
    return {"route": route, "status": status, "answer": answer, "sql": None, "columns": [], "data": [],
            "row_count": 0, "truncated": False, "sources": [], "chart": None, "meta": {}}


def answer_question(question: str) -> dict:
    question = (question or "").strip()
    lang = detect_language(question)
    if not question:
        return _base("none", "error", t("empty_q", lang))
    if len(question) > 1000:
        return _base("none", "error", t("too_long", lang))
    try:
        link = linker.link(question)
        lang = link.language
        if link.route == "sql":
            res = sql_pipeline.run_sql(question, link)
            res["chart"] = chart.suggest(res["columns"], res["data"])
        elif link.route == "rag":
            res = rag_pipeline.run_rag(link.question_en, lang, question)
            res["chart"] = None
        else:
            # Safety net: the router may be too strict. Search the uploaded documents before refusing.
            res = rag_pipeline.run_rag(link.question_en, lang, question)
            if res["status"] == "success":
                res["chart"] = None
                res["meta"]["fallback_from"] = "unsupported"
            else:
                reason = f" {link.unsupported_reason}" if (lang == "en" and link.unsupported_reason) else ""  # reason is English-only
                res = _base("unsupported", "unsupported", f"{t('cannot_answer', lang)}{reason} {t('scope', lang)}")
        res.setdefault("meta", {})["linking"] = {"reasoning": link.reasoning, "tables": link.tables}
        res["language"] = lang
        return res
    except LLMError as e:
        log.error("LLM failure: %s", e)
        return _base("none", "error", t("llm_down", lang))
    except Exception:  # noqa: BLE001
        log.exception("chat pipeline failed")
        return _base("none", "error", t("generic_fail", lang))


def log_exchange(conversation_id: str | None, question: str, res: dict) -> int | None:
    try:
        with owner_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO chat_log (conversation_id, question, route, status, answer, sql_text) VALUES (%s,%s,%s,%s,%s,%s) RETURNING message_id",
                        (conversation_id, question, res.get("route"), res.get("status"), res.get("answer"), res.get("sql")))
            return cur.fetchone()[0]
    except Exception:  # noqa: BLE001
        log.exception("could not log chat exchange")
        return None
