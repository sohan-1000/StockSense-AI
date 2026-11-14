# StockSense AI – NL2SQL + RAG chatbot

Merchants ask questions in plain English about their retail data (sales, stock, expiry, suppliers) or about uploaded
shopkeeper documents. See `docs/DATASET.md` for the data.

```
question → [LLM call 1: route + schema linking] ─ sql ─→ date rules → schema builder (PK/FK/joins) → [LLM call 2: SQL]
                                                  │         → SQLGlot validation → read-only PostgreSQL → [answer text]
                                                  ├ rag ─→ pgvector search → relevance gate → [LLM: answer from chunks only] + citations
                                                  └ unsupported → polite refusal
```

## Languages
English, Hindi (हिन्दी) and Telugu (తెలుగు). The reply is written in the language of the question, for both SQL and
document answers. How: the router call also returns an English version of the question; dates, SQL generation and
document search run on that English text; the final explanation and all fixed messages are produced in the user's
language. The language is detected from the script (Latin-script "Hinglish" is answered in English). Product names stay
as in the database. UI labels (badges/buttons) are English.

## Run

```bash
# 1. backend (Python 3.12). API key goes in .env as  llm_api_key=...   (Sarvam; the file is git-ignored)
cd backend
pip install -r requirements.txt
python -m app.db.ingest              # creates embedded PostgreSQL + pgvector, loads CSVs (idempotent)
python scripts/verify_ingest.py      # checks DB == CSVs
uvicorn app.api.main:app --port 8000

# 2. frontend
cd frontend && npm install && npm run dev      # http://localhost:5173  (proxies /api to :8000)
```

Set `DATABASE_URL` / `READONLY_DATABASE_URL` to use your own PostgreSQL (needs the `vector` extension) instead of the embedded one.

## API
| Endpoint | Purpose |
|---|---|
| `POST /api/chat` `{conversation_id?, question}` | answer; returns `route`, `answer`, `sql`, `columns`, `data`, `chart`, `sources`, `status`, `message_id` |
| `POST /api/chat/{message_id}/feedback` `{rating: 1\|-1, comment?}` | thumbs up/down |
| `POST /api/documents` (multipart `file`: PDF/DOCX/TXT) | upload; processed in the background |
| `GET /api/documents`, `GET /api/documents/{id}/status` | ingestion status |
| `GET /api/suggestions`, `GET /api/health` | helpers |

Conversational memory is not implemented yet (`conversation_id` is accepted and logged for later).

## Tests
```bash
cd backend
python -m pytest tests -q                       # security, schema, dates, few-shot examples (no LLM)
python tests/run_eval.py                        # NL2SQL accuracy vs hand-written reference SQL (calls the LLM)
python tests/run_eval.py --file eval_holdout.yaml   # held-out questions not used for tuning
```

## Safety
LLM SQL is parsed by SQLGlot (SELECT-only, table/column allow-list, no unknown functions, forced LIMIT) and then run
by a database role that can only `SELECT` the retail tables, with a 5 s timeout. Numbers in answers must appear in the
query result or the text falls back to a template. RAG answers come only from retrieved chunks.
