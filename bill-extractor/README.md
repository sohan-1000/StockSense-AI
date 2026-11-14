# Bill Extractor (POC)

Upload a bill photo (JPG, PNG, PDF) in the browser. FastAPI sends it to Sarvam
Document AI Extract, cleans the values (amounts, dates, GSTIN), checks the totals
add up, and saves one row in `transactions` plus its `line_items` in PostgreSQL.
Bills are `verified` or `pending_review`; you can Approve or Reject them in the UI.

One process, one Postgres database, no queue, no login.

## Setup

Create an empty database once (pgAdmin: right-click Databases -> Create -> Database, name it
`billextractor`). The tables are created automatically on first start.

```
pip install -r requirements.txt
copy .env.example .env        # then put your Sarvam key and DATABASE_URL in .env
python -m unittest discover -s tests
python app.py                 # open http://localhost:8000
```

`.env` is git-ignored. Never commit the key.

## Files

| File | Purpose |
|---|---|
| `schema.py` | Bill schema sent to Sarvam. Reword field descriptions here to fix accuracy. |
| `sarvam_client.py` | Submit job, poll status, read `result` from `/results`. |
| `cleaning.py` | Amount, date (day-first), GSTIN, payment-mode cleanup. |
| `checks.py` | Missing fields, GSTIN format, totals and line items add up. |
| `db.py` | PostgreSQL (psycopg): `transactions`, `line_items`, status updates. |
| `pipeline.py` | extract -> clean -> check -> save for one bill; duplicate detection by file hash. |
| `app.py` | JSON API + web UI. Endpoints are plain `def` because the Sarvam call blocks. |
| `ingest.py` | `python ingest.py ./bills [language]` loads a folder. |

## JSON API

- `POST /bills` (multipart `file`, optional `language`) -> 201 bill JSON, 409 duplicate, 400 bad file, 502 Sarvam error
- `GET /bills`, `GET /bills/{id}`
- `POST /bills/{id}/status` with `{"status": "verified" | "pending_review" | "rejected"}`

## Web UI

- `/` upload form (with language dropdown) and a table of all bills, newest first; `pending_review` rows are highlighted
- `/view/{id}` bill image next to the extracted fields, line items, issues, Approve / Reject

## Accuracy loop

Put 10-20 real bills in `samples/`, run `python ingest.py ./samples`, compare the
output with the printed bills, and reword `schema.py` descriptions for any field that
comes back wrong. `transactions.raw_json` keeps Sarvam's untouched response for debugging.
