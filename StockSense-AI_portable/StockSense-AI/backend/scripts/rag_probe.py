"""Calibrate the RAG relevance threshold: print the top retrieval score for relevant vs irrelevant questions.
   python scripts/rag_probe.py <path-to-document> [--keep]      (the document is removed afterwards unless --keep)"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.connection import owner_conn  # noqa: E402
from app.rag import store  # noqa: E402

path = Path(sys.argv[1])
doc_id = store.ingest_file(path)
print("document:", store.get_document(doc_id))
probes = sys.argv[2:] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else [
    "What is StockSense AI?", "What problem does the product solve for shopkeepers?", "How does demand forecasting work?",
    "What is the capital of India?", "Write a poem about cats", "How do I bake a chocolate cake?", "Who won the cricket world cup?"]
for q in probes:
    hits = store.search(q, 3)
    print(f"{hits[0].score:.3f}  p{hits[0].page}  {q}" if hits else f"----  {q}")
if "--keep" not in sys.argv:
    with owner_conn() as conn:
        conn.cursor().execute("DELETE FROM documents WHERE document_id=%s", (doc_id,))
    print("(test document removed)")
