"""End-to-end RAG test. Uses a clearly-labelled TEST fixture that is deleted from the database afterwards.
(Calls the LLM for the answer step.)   python -m pytest tests/test_rag.py -q -s"""
from pathlib import Path

import pytest

from app.db.connection import owner_conn
from app.rag import pipeline, store
from app.rag.chunk import chunk_pages
from app.rag.extract import Page, extract

FIXTURE = Path(__file__).parent / "fixtures" / "TEST_FIXTURE_shop_guide.txt"


@pytest.fixture(scope="module")
def doc_id():
    with owner_conn() as conn:  # make sure no earlier run left the fixture behind
        conn.cursor().execute("DELETE FROM documents WHERE filename = %s", (FIXTURE.name,))
    did = store.ingest_file(FIXTURE)
    yield did
    with owner_conn() as conn:
        conn.cursor().execute("DELETE FROM documents WHERE document_id = %s", (did,))


def test_chunking_keeps_pages_and_size():
    pages = [Page(1, "Para one. " * 300), Page(2, "Short page text.")]
    chunks = chunk_pages(pages)
    assert all(len(c.text) <= 2100 for c in chunks)
    assert {c.page for c in chunks} == {1, 2}
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_extract_txt_and_unsupported_type(tmp_path):
    assert "Return policy" in extract(FIXTURE)[0].text
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError):
        store.register_document(bad, "x.exe")


def test_ingestion_ready_and_idempotent(doc_id):
    d = store.get_document(doc_id)
    assert d["status"] == "ready" and d["chunk_count"] >= 1
    assert store.ingest_file(FIXTURE) == doc_id  # same content -> same document, no duplicate chunks
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM doc_chunks WHERE document_id=%s", (doc_id,))
        assert cur.fetchone()[0] == d["chunk_count"]


def test_semantic_retrieval_finds_right_chunk(doc_id):
    hits = store.search("how long can I return perishable items", 3)
    assert "24 hours" in hits[0].text and hits[0].score > 0.5


def test_in_document_question_answers_with_sources(doc_id):
    res = pipeline.run_rag("What are the supplier payment terms?")
    print("\nANSWER:", res["answer"], "\nSOURCES:", [(s["document"], s["page"], s["score"]) for s in res["sources"]])
    assert res["status"] == "success" and res["sources"]
    assert "15" in res["answer"]  # the fixture states 15 days
    assert res["sources"][0]["document"] == FIXTURE.name


@pytest.mark.parametrize("q", ["What is the capital of India?", "Write a poem about cats", "Who won the cricket world cup?"])
def test_off_topic_is_refused_not_answered(doc_id, q):
    res = pipeline.run_rag(q)
    assert res["status"] == "not_found" and not res["sources"], res["answer"]


def test_fact_not_in_document_is_not_invented(doc_id):
    res = pipeline.run_rag("What is the refund policy for electronics?")
    print("\nANSWER:", res["answer"], "| status:", res["status"])
    assert res["status"] == "not_found"
