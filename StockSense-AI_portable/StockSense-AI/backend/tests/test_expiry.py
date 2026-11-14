"""Expiry endpoint tests (no LLM). Numbers are checked against independent SQL on the raw `batches` table."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db.connection import owner_conn


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def raw(sql, *args):
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, args)
        return cur.fetchall()


def test_summary_matches_raw_batches(client):
    j = client.get("/api/expiry").json()
    s = j["summary"]
    assert j["as_of"] == "2026-10-31"
    assert s["total_batches"] == 51 and j["count"] == 51  # no filter -> every batch
    cnt = lambda d: raw("SELECT count(*) FROM batches WHERE expiry_date <= DATE '2026-10-31' + %s", d)[0][0]
    assert s["within_3_days"] == cnt(3) and s["within_7_days"] == cnt(7) and s["within_14_days"] == cnt(14) and s["within_30_days"] == cnt(30)
    assert s["expired"] == 0
    risk = raw("SELECT sum(b.quantity_remaining*p.unit_cost) FROM batches b JOIN products p USING(product_id) WHERE b.expiry_date <= DATE '2026-10-31' + 7")[0][0]
    assert s["value_at_risk_7_days"] == float(risk) or s["value_at_risk_7_days"] == int(risk)


def test_all_batches_sorted_by_soonest_expiry(client):
    j = client.get("/api/expiry").json()
    assert j["count"] == 51
    days = [i["days_to_expiry"] for i in j["items"]]
    assert days == sorted(days)
    first = j["items"][0]
    assert set(first) >= {"batch_id", "product_name", "category", "quantity_remaining", "expiry_date", "days_to_expiry", "value_at_risk", "status"}


def test_days_filter_and_status_buckets(client):
    j = client.get("/api/expiry", params={"days": 7}).json()
    assert j["count"] == j["summary"]["within_7_days"] == 10
    assert all(i["days_to_expiry"] <= 7 for i in j["items"])
    for i in j["items"]:
        assert i["status"] == ("critical" if i["days_to_expiry"] <= 3 else "warning")
    assert client.get("/api/expiry", params={"days": 0}).json()["count"] == 0


def test_category_and_search_filters(client):
    j = client.get("/api/expiry", params={"category": "Dairy"}).json()
    assert j["count"] > 0 and {i["category"] for i in j["items"]} == {"Dairy"}
    assert "Dairy" in j["categories"]
    j = client.get("/api/expiry", params={"search": "bread"}).json()
    assert j["count"] > 0 and all("bread" in i["product_name"].lower() for i in j["items"])
    j = client.get("/api/expiry", params={"search": "nonexistent-product-xyz"}).json()
    assert j["count"] == 0 and j["summary"]["total_batches"] == 51  # summary does not depend on the filters


def test_validation_and_injection_safety(client):
    assert client.get("/api/expiry", params={"days": -1}).status_code == 422
    assert client.get("/api/expiry", params={"days": "abc"}).status_code == 422
    assert client.get("/api/expiry", params={"search": "x" * 61}).status_code == 422
    r = client.get("/api/expiry", params={"search": "'; DROP TABLE batches; --", "category": "Dairy' OR '1'='1"})
    assert r.status_code == 200 and r.json()["count"] == 0
    assert raw("SELECT count(*) FROM batches")[0][0] == 51  # table intact
