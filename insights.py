"""Insights: pick a preset data source, see charts, get AI-written insights.

Safety by design:
  * The browser only ever sends a source id from a fixed allow-list. No SQL, no credentials.
  * Every query below is fixed text and runs in a read-only transaction with a timeout.
  * Only summary numbers (KPIs and chart data) go to the LLM, never raw rows.
  * Every number in an AI insight is checked against the data; unmatched ones are flagged.

Connection settings come from environment variables (RETAIL_DATABASE_URL, DATABASE_URL,
SARVAM_API_KEY) or, as a fallback, from bill-extractor/.env, which is git-ignored.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent
router = APIRouter()

# ----------------------------------------------------------------------------- config
_ALLOWED_ENV = {"DATABASE_URL", "BILLS_DATABASE_URL", "RETAIL_DATABASE_URL", "SARVAM_API_KEY",
                "llm_api_key", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"}


def _load_env() -> None:
    for f in (ROOT / ".env", ROOT / "bill-extractor" / ".env"):
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    if key.strip() in _ALLOWED_ENV:
                        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env()


def _llm_key() -> str:
    return os.environ.get("SARVAM_API_KEY") or os.environ.get("llm_api_key") or os.environ.get("LLM_API_KEY") or ""


def _bills_url() -> str:
    return os.environ.get("BILLS_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def _where(url: str) -> str:
    """host:port/database only: never the user or password."""
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}" if u.hostname else ""


class SourceUnavailable(Exception):
    pass


# ----------------------------------------------------------------------------- db helper
def _f(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _query(url: str, sql: str, params=None) -> list[dict]:
    """Run one fixed read-only query."""
    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(url, connect_timeout=5, row_factory=dict_row,
                             options="-c default_transaction_read_only=on -c statement_timeout=8000") as conn:
            rows = conn.execute(sql, params or {}).fetchall()
    except psycopg.Error as e:
        raise SourceUnavailable(f"Database query failed: {str(e).splitlines()[0][:200]}")
    return [{k: _f(v) for k, v in row.items()} for row in rows]


def _series(rows: list[dict], label: str, value: str = "v") -> list[dict]:
    return [{"label": str(r[label]), "value": round(float(r[value] or 0), 2)} for r in rows]


def _pct(new: float, old: float) -> float | None:
    return round((new - old) / old * 100, 1) if old else None


def _kpi(label: str, value, fmt: str = "int", hint: str = "", tone: str = "") -> dict:
    return {"label": label, "value": value, "format": fmt, "hint": hint, "tone": tone}


# ----------------------------------------------------------------------------- tables
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _table(tid: str, title: str, hint: str, columns: list, rows: list[dict]) -> dict:
    """columns: [(key, label, format)] with format money | int | dec | pct | text."""
    return {"id": tid, "title": title, "hint": hint,
            "columns": [{"key": k, "label": l, "format": f} for k, l, f in columns], "rows": rows}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ----------------------------------------------------------------------------- source: retail
def build_retail() -> dict:
    url = os.environ.get("RETAIL_DATABASE_URL", "")
    if not url:
        raise SourceUnavailable("RETAIL_DATABASE_URL is not set")
    end = _query(url, "SELECT max(date) AS d FROM sales")[0]["d"]
    if not end:
        raise SourceUnavailable("The retail database has no sales rows yet")
    p = {"e": date.fromisoformat(end)}

    s30 = _query(url, """SELECT
            COALESCE(sum(revenue)  FILTER (WHERE date > %(e)s - 30), 0) AS rev,
            COALESCE(sum(profit)   FILTER (WHERE date > %(e)s - 30), 0) AS profit,
            COALESCE(sum(revenue)  FILTER (WHERE date > %(e)s - 60 AND date <= %(e)s - 30), 0) AS prev_rev,
            COALESCE(sum(profit)   FILTER (WHERE date > %(e)s - 60 AND date <= %(e)s - 30), 0) AS prev_profit,
            COALESCE(sum(quantity_sold) FILTER (WHERE date > %(e)s - 30), 0) AS units
        FROM v_sales_enriched""", p)[0]
    inv = _query(url, """SELECT i.product_id, i.product_name, i.supplier_name, i.current_stock, i.snapshot_date,
            i.avg_daily_sales_last_30d AS adr, i.days_of_cover, i.supplier_lead_time_days AS lead,
            i.minimum_order_quantity AS moq, i.unit_cost, i.stock_value, i.below_reorder_point, i.stockout_risk,
            p.reorder_target_days AS target
        FROM v_inventory_status i JOIN products p USING (product_id)""")
    bat = _query(url, """SELECT b.batch_id, b.product_id, p.product_name, b.quantity_remaining AS qty, b.expiry_date,
            i.snapshot_date, i.avg_daily_sales_last_30d AS adr, p.unit_cost
        FROM batches b JOIN products p USING (product_id) JOIN inventory i USING (product_id)
        ORDER BY b.product_id, b.expiry_date""")
    daily = _query(url, """SELECT date, day_of_week, max(CASE WHEN festival IS NOT NULL THEN 1 ELSE 0 END) AS fest,
            sum(quantity_sold) AS u FROM v_sales_enriched WHERE date > %(e)s - 365 GROUP BY date, day_of_week""", p)
    promo = _query(url, """SELECT product_id, promotion_applied AS promo, avg(quantity_sold) AS u, avg(profit) AS pr
        FROM v_sales_enriched WHERE date > %(e)s - 365 GROUP BY 1, 2""", p)
    movers = _query(url, """SELECT product_name AS l,
            COALESCE(sum(quantity_sold) FILTER (WHERE date > %(e)s - 30), 0) AS cur,
            COALESCE(sum(quantity_sold) FILTER (WHERE date > %(e)s - 60 AND date <= %(e)s - 30), 0) AS prev
        FROM v_sales_enriched GROUP BY 1""", p)
    prod90 = _query(url, """SELECT product_name AS l, sum(revenue) AS v, sum(profit) AS pr FROM v_sales_enriched
        WHERE date > %(e)s - 90 GROUP BY 1""", p)
    cat90 = _query(url, """SELECT category AS l, sum(revenue) AS v, sum(profit) AS pr FROM v_sales_enriched
        WHERE date > %(e)s - 90 GROUP BY 1 ORDER BY 2 DESC""", p)
    sup90 = _query(url, """SELECT supplier_name AS l, sum(revenue) AS v FROM v_sales_enriched
        WHERE date > %(e)s - 90 GROUP BY 1 ORDER BY 2 DESC""", p)
    monthly = list(reversed(_query(url, """SELECT date_trunc('month', date)::date AS m, sum(revenue) AS v
        FROM v_sales_enriched GROUP BY 1 ORDER BY 1 DESC LIMIT 12""")))
    raw = _query(url, """SELECT CASE WHEN days_to_expiry <= 3 THEN '0-3 days' WHEN days_to_expiry <= 7 THEN '4-7 days'
            WHEN days_to_expiry <= 14 THEN '8-14 days' WHEN days_to_expiry <= 30 THEN '15-30 days'
            ELSE 'Over 30 days' END AS l, sum(value_at_risk) AS v FROM v_batch_expiry GROUP BY 1""")
    order = ["0-3 days", "4-7 days", "8-14 days", "15-30 days", "Over 30 days"]
    buckets = sorted(raw, key=lambda r: order.index(r["l"]))

    # ---- profit
    margin = round(s30["profit"] / s30["rev"] * 100, 1) if s30["rev"] else None
    prev_margin = round(s30["prev_profit"] / s30["prev_rev"] * 100, 1) if s30["prev_rev"] else None
    margin_move = round(margin - prev_margin, 1) if margin is not None and prev_margin is not None else None
    rev_change = _pct(s30["rev"], s30["prev_rev"])

    # ---- what to order now: enough for the supplier lead time plus the target days of cover
    reorder = []
    for r in inv:
        adr = float(r["adr"] or 0)
        qty = max(0, math.ceil(adr * (r["lead"] + r["target"]) - r["current_stock"]))
        if qty and (r["below_reorder_point"] or r["stockout_risk"]):
            qty = max(qty, r["moq"] or 0)
            reorder.append({"product": r["product_name"], "supplier": r["supplier_name"], "stock": r["current_stock"],
                            "days_left": round(float(r["days_of_cover"]), 1) if r["days_of_cover"] is not None else None,
                            "lead": r["lead"], "qty": qty, "cost": round(qty * float(r["unit_cost"]), 2)})
    reorder.sort(key=lambda x: x["days_left"] if x["days_left"] is not None else 1e9)
    reorder_cash = round(sum(x["cost"] for x in reorder), 2)
    below = sum(1 for r in inv if r["below_reorder_point"])
    stock_value = round(sum(float(r["stock_value"]) for r in inv), 2)
    slow = [r for r in inv if r["days_of_cover"] is not None and float(r["days_of_cover"]) > 30]
    slow_value = round(sum(float(r["stock_value"]) for r in slow), 2)

    # ---- what will go to waste: oldest batch sells first (FEFO) at the recent daily sales rate
    waste = []
    by_product = defaultdict(list)
    for b in bat:
        by_product[b["product_id"]].append(b)
    for batches in by_product.values():
        cum = 0
        for b in batches:
            days = (date.fromisoformat(b["expiry_date"]) - date.fromisoformat(b["snapshot_date"])).days
            cum += b["qty"]
            unsold = int(round(max(0, min(b["qty"], cum - float(b["adr"] or 0) * max(days, 0)))))
            if unsold >= 1:
                waste.append({"batch": b["batch_id"], "product": b["product_name"], "qty": b["qty"], "days": days,
                              "unsold": unsold, "value": round(unsold * float(b["unit_cost"]), 2)})
    waste.sort(key=lambda x: -x["value"])
    waste_units, waste_value = sum(w["unsold"] for w in waste), round(sum(w["value"] for w in waste), 2)

    # ---- busy days and festivals (last 365 days)
    by_day = defaultdict(list)
    for d in daily:
        by_day[d["day_of_week"]].append(float(d["u"]))
    day_avg = {k: _mean(v) for k, v in by_day.items()}
    weekend = _mean([day_avg[k] for k in ("Saturday", "Sunday") if k in day_avg])
    weekday = _mean([day_avg[k] for k in WEEKDAYS[:5] if k in day_avg])
    weekend_lift = _pct(weekend, weekday) if weekend and weekday else None
    fest = [float(d["u"]) for d in daily if d["fest"]]
    normal = [float(d["u"]) for d in daily if not d["fest"]]
    festival_lift = _pct(_mean(fest), _mean(normal)) if len(fest) >= 3 and normal else None
    busiest = max(day_avg, key=day_avg.get) if day_avg else None

    # ---- do promotions pay? (each product compared with itself)
    pairs = defaultdict(dict)
    for r in promo:
        pairs[r["product_id"]][bool(r["promo"])] = (float(r["u"]), float(r["pr"]))
    both = [v for v in pairs.values() if True in v and False in v and v[False][0] > 0]
    units_lift = _pct(_mean([v[True][0] / v[False][0] for v in both]) * 100, 100) if both else None
    profit_norm = _mean([v[False][1] for v in both]) if both else None
    profit_promo = _mean([v[True][1] for v in both]) if both else None
    profit_change = _pct(profit_promo, profit_norm) if both and profit_norm else None

    # ---- who is rising or falling (units, last 30 days vs the 30 before)
    moved = [{"product": m["l"], "cur": int(m["cur"]), "prev": int(m["prev"]),
              "change": _pct(m["cur"], m["prev"])} for m in movers if m["prev"] >= 15]
    moved.sort(key=lambda x: x["change"])
    movers_rows = list(reversed(moved[-5:])) + moved[:5]

    # ---- concentration
    ranked = sorted(prod90, key=lambda r: -r["v"])
    total90 = sum(r["v"] for r in ranked)
    k20 = max(1, round(len(ranked) * 0.2))
    top20_share = round(sum(r["v"] for r in ranked[:k20]) / total90 * 100, 1) if total90 else None
    sup_share = round(sup90[0]["v"] / sum(r["v"] for r in sup90) * 100, 1) if sup90 and sum(r["v"] for r in sup90) else None
    margins = sorted(({"l": r["l"], "v": round(r["pr"] / r["v"] * 100, 1)} for r in prod90 if r["v"]), key=lambda r: r["v"])
    cat_margin = sorted(({"l": r["l"], "v": round(r["pr"] / r["v"] * 100, 1)} for r in cat90 if r["v"]), key=lambda r: -r["v"])

    return {
        "as_of": end,
        "kpis": [
            _kpi("Revenue, last 30 days", round(s30["rev"], 2), "money",
                 f"{rev_change:+.1f}% vs the 30 days before" if rev_change is not None else "", "good" if (rev_change or 0) >= 0 else "bad"),
            _kpi("Gross margin, last 30 days", margin, "pct",
                 f"{margin_move:+.1f} points vs the 30 days before" if margin_move is not None else "", "good" if (margin_move or 0) >= 0 else "bad"),
            _kpi("Units sold, last 30 days", round(s30["units"]), "int"),
            _kpi("Products to reorder now", len(reorder), "int",
                 f"{below} below reorder point · ₹{round(reorder_cash):,} to restock", "bad" if reorder else "good"),
            _kpi("Stock likely to expire unsold", waste_value, "money", f"{waste_units:,} units across {len(waste)} batches", "bad" if waste else "good"),
            _kpi("Stock value at cost", stock_value, "money", f"₹{round(slow_value):,} in slow stock (over 30 days left)" if slow else ""),
        ],
        "tables": [
            _table("reorder", "Order now: stock that will run out before a new delivery arrives",
                   "Suggested quantity = recent daily sales x (supplier lead time + target days of cover) minus stock in hand, at least the minimum order.",
                   [("product", "Product", "text"), ("supplier", "Supplier", "text"), ("stock", "In stock", "int"),
                    ("days_left", "Days left", "dec"), ("lead", "Lead time (days)", "int"), ("qty", "Order qty", "int"), ("cost", "Cost", "money")],
                   reorder[:10]),
            _table("waste", "Likely to expire unsold",
                   "Assumes the oldest batch sells first at the last 30 days' sales rate. Units that will not sell before expiry are counted as waste.",
                   [("batch", "Batch", "text"), ("product", "Product", "text"), ("qty", "Qty", "int"), ("days", "Days to expiry", "int"),
                    ("unsold", "Likely unsold", "int"), ("value", "Cost at risk", "money")],
                   waste[:10]),
            _table("movers", "Biggest changes in units sold",
                   "Last 30 days against the 30 days before, for products that sold at least 15 units before.",
                   [("product", "Product", "text"), ("cur", "Last 30 days", "int"), ("prev", "Previous 30", "int"), ("change", "Change %", "pct")],
                   movers_rows),
        ],
        "charts": [
            {"id": "monthly", "title": "Monthly revenue", "type": "line", "unit": "₹", "data": _series(monthly, "m")},
            {"id": "top", "title": "Top 10 products by revenue (last 90 days)", "type": "hbar", "unit": "₹", "data": _series(sorted(prod90, key=lambda r: -r["v"])[:10], "l")},
            {"id": "catmargin", "title": "Profit margin by category (last 90 days)", "type": "bar", "unit": "%", "data": _series(cat_margin, "l")},
            {"id": "lowmargin", "title": "Lowest-margin products (last 90 days)", "type": "hbar", "unit": "%", "data": _series(margins[:8], "l")},
            {"id": "weekday", "title": "Average units sold per day, by weekday (last year)", "type": "bar", "unit": "units",
             "data": [{"label": d[:3], "value": round(day_avg[d], 1)} for d in WEEKDAYS if d in day_avg]},
            {"id": "promo", "title": "Average profit per product per day: normal vs promotion days", "type": "bar", "unit": "₹",
             "data": ([{"label": "Normal days", "value": round(profit_norm, 2)}, {"label": "Promotion days", "value": round(profit_promo, 2)}] if both else [])},
            {"id": "supplier", "title": "Revenue by supplier (last 90 days)", "type": "hbar", "unit": "₹", "data": _series(sup90, "l")},
            {"id": "expiry", "title": "Stock value by time left before expiry", "type": "bar", "unit": "₹", "data": _series(buckets, "l")},
        ],
        "derived": {
            "revenue_change_vs_previous_30_days_percent": rev_change,
            "gross_margin_percent_last_30_days": margin,
            "gross_margin_change_in_percentage_points": margin_move,
            "units_that_will_probably_expire_unsold": waste_units,
            "cash_needed_to_restock_products_listed": reorder_cash,
            "best_selling_single_product_share_of_90_day_revenue_percent": round(ranked[0]["v"] / total90 * 100, 1) if total90 else None,
            "top_20_percent_of_products_share_of_90_day_revenue_percent": top20_share,
            "largest_supplier_share_of_90_day_revenue_percent": sup_share,
            "weekend_vs_weekday_units_percent": weekend_lift,
            "busiest_weekday": busiest,
            "festival_day_vs_normal_day_units_percent": festival_lift,
            "units_sold_on_promotion_days_vs_normal_days_percent": units_lift,
            "profit_per_product_day_on_promotion_vs_normal_percent": profit_change,
        },
        "notes": ["Sales data ends " + end + ". Figures are from the sample retail dataset, which is synthetic demo data."],
        "ai_notes": ["Prefer practical advice a shop owner can act on today: what to order, what to discount before it expires, "
                     "where margin is lost, which products are rising or falling. Refer to specific products from the tables by name."],
    }


# ----------------------------------------------------------------------------- source: bills
def build_bills() -> dict:
    url = _bills_url()
    if not url:
        raise SourceUnavailable("DATABASE_URL (Bill Intake database) is not set")
    base = _query(url, """SELECT count(*) AS n,
                                 COALESCE(sum(total_amount) FILTER (WHERE status <> 'rejected'), 0) AS spend,
                                 count(*) FILTER (WHERE status <> 'rejected') AS counted,
                                 count(*) FILTER (WHERE status = 'pending_review') AS review,
                                 count(*) FILTER (WHERE status = 'posted') AS posted,
                                 COALESCE(sum(tax_amount) FILTER (WHERE status <> 'rejected'), 0) AS gst,
                                 COALESCE(sum(total_amount) FILTER (WHERE status <> 'rejected' AND payment_mode = 'credit'), 0) AS credit,
                                 max(now()::date - created_at::date) FILTER (WHERE status IN ('verified', 'pending_review')) AS oldest
                          FROM transactions""")[0]
    if not base["n"]:
        raise SourceUnavailable("No bills have been uploaded yet. Upload some in Bill Intake first.")
    by_status = _query(url, "SELECT status AS l, count(*) AS v FROM transactions GROUP BY 1 ORDER BY 2 DESC")
    supplier = _query(url, """SELECT COALESCE(merchant_name, 'Unknown') AS l, sum(total_amount) AS v FROM transactions
                              WHERE status <> 'rejected' GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 8""")
    monthly = _query(url, """SELECT date_trunc('month', bill_date)::date AS m, sum(total_amount) AS v FROM transactions
                             WHERE bill_date IS NOT NULL AND status <> 'rejected' GROUP BY 1 ORDER BY 1""")
    pay = _query(url, "SELECT COALESCE(payment_mode, 'unknown') AS l, count(*) AS v FROM transactions GROUP BY 1 ORDER BY 2 DESC")
    avg = round(base["spend"] / max(base["counted"], 1), 2)
    labels = {"verified": "Ready", "pending_review": "Needs review", "posted": "In inventory", "rejected": "Rejected", "posting": "Adding"}
    for r in by_status:
        r["l"] = labels.get(r["l"], r["l"])
    sup_share = round(supplier[0]["v"] / base["spend"] * 100, 1) if supplier and base["spend"] else None

    # what we paid compared with the cost recorded in the product catalogue
    price_rows, extra_paid, price_note = [], 0.0, ""
    retail_url = os.environ.get("RETAIL_DATABASE_URL", "")
    items = _query(url, """SELECT t.merchant_name AS supplier, li.description, li.product_id, li.units, li.amount
                           FROM line_items li JOIN transactions t ON t.id = li.transaction_id
                           WHERE li.product_id IS NOT NULL AND li.units > 0 AND li.amount > 0 AND t.status <> 'rejected'""")
    if items and retail_url:
        try:
            costs = {r["product_id"]: r for r in _query(retail_url, "SELECT product_id, product_name, unit_cost FROM products WHERE product_id = ANY(%(ids)s)",
                                                         {"ids": sorted({i["product_id"] for i in items})})}
        except SourceUnavailable:
            costs = {}
        for i in items:
            c = costs.get(i["product_id"])
            if not c or not c["unit_cost"]:
                continue
            paid = float(i["amount"]) / float(i["units"])
            diff = (paid - float(c["unit_cost"])) / float(c["unit_cost"]) * 100
            extra = (paid - float(c["unit_cost"])) * float(i["units"])
            extra_paid += max(extra, 0)
            price_rows.append({"supplier": i["supplier"] or "Unknown", "product": c["product_name"], "paid": round(paid, 2),
                               "cost": float(c["unit_cost"]), "diff": round(diff, 1), "extra": round(extra, 2)})
        price_rows.sort(key=lambda r: -r["diff"])
    elif items:
        price_note = "Price check needs the retail database (RETAIL_DATABASE_URL)."

    oldest = base["oldest"]
    kpis = [
        _kpi("Bills uploaded", base["n"], "int"),
        _kpi("Total purchase value", round(base["spend"], 2), "money", "excluding rejected bills"),
        _kpi("Average bill value", avg, "money"),
        _kpi("GST paid on purchases", round(base["gst"], 2), "money", "total tax on non-rejected bills"),
        _kpi("Bought on credit", round(base["credit"], 2), "money", "bills with payment mode credit", "bad" if base["credit"] else "good"),
        _kpi("Bills waiting to be added", base["counted"] - base["posted"], "int",
             f"oldest has waited {oldest} day(s)" if oldest is not None else "", "bad" if (oldest or 0) >= 3 else ""),
    ]
    if price_rows:
        kpis.append(_kpi("Paid above recorded cost", round(extra_paid, 2), "money", "bill price vs product cost, matched items only", "bad" if extra_paid else "good"))
    tables = []
    if price_rows:
        tables.append(_table("price", "Price check: what you paid vs the cost in your catalogue",
                             "Paid per unit = line amount divided by catalogue units. Positive difference means you paid more than the recorded cost.",
                             [("supplier", "Supplier", "text"), ("product", "Product", "text"), ("paid", "Paid per unit", "dec"),
                              ("cost", "Recorded cost", "dec"), ("diff", "Difference %", "pct"), ("extra", "Extra paid", "money")], price_rows[:10]))
    notes = ["Based on the bills uploaded to Bill Intake."] + ([price_note] if price_note else [])
    return {
        "as_of": date.today().isoformat(),
        "kpis": kpis,
        "tables": tables,
        "charts": [
            {"id": "supplier", "title": "Purchase value by supplier", "type": "hbar", "unit": "₹", "data": _series(supplier, "l")},
            {"id": "month", "title": "Purchase value by month", "type": "line", "unit": "₹", "data": _series(monthly, "m")},
            {"id": "status", "title": "Bills by status", "type": "bar", "unit": "bills", "data": _series(by_status, "l")},
            {"id": "pay", "title": "Bills by payment mode", "type": "bar", "unit": "bills", "data": _series(pay, "l")},
        ],
        "derived": {"largest_supplier_share_of_purchase_value_percent": sup_share,
                    "amount_paid_above_recorded_cost": round(extra_paid, 2) if price_rows else None},
        "notes": notes,
        "ai_notes": ["Status meanings: Ready = read and passed the checks, waiting for the user to add it to inventory. "
                     "Needs review = a check failed and a person must fix it. In inventory = stock from this bill has been added. "
                     "Rejected = discarded. All of these bills have already been uploaded and read.",
                     "GST is shown only as a total of tax printed on the bills. Do not give tax advice.",
                     "Prefer practical advice: pay attention to suppliers charging more than the recorded cost, to bills that are "
                     "waiting too long to be added, and to credit that will need to be paid."],
    }


# ----------------------------------------------------------------------------- source: forecast
@lru_cache(maxsize=1)
def _history():
    import pandas as pd
    return pd.read_csv(ROOT / "datasets" / "sales.csv", parse_dates=["date"])


def build_forecast() -> dict:
    if not (ROOT / "datasets" / "sales.csv").is_file():
        raise SourceUnavailable("datasets/sales.csv is missing")
    import pandas as pd
    from forecast_transformer import generate_monthly_forecast

    hist = _history()
    fc = generate_monthly_forecast(6)
    months = sorted(fc["month"].unique())
    by_month = fc.groupby("month")["forecast_quantity"].sum().round(1)
    actual = hist.groupby(hist["date"].dt.strftime("%Y-%m"))["quantity_sold"].sum()
    last_year = [float(actual.get(f"{int(m[:4]) - 1}{m[4:]}", 0)) for m in months]
    total_fc, total_ly = float(by_month.sum()), sum(last_year)
    growth = _pct(total_fc, total_ly)
    # Sanity check, computed live: the first forecast month should be close to what really sold recently.
    end_date = hist["date"].max()
    last30 = float(hist[hist["date"] > end_date - pd.Timedelta(days=30)]["quantity_sold"].sum())
    first_month = float(by_month[months[0]])
    gap = _pct(first_month, last30)
    biased = gap is not None and abs(gap) > 15
    peak = by_month.idxmax()
    top = fc.groupby("product_name")["forecast_quantity"].sum().sort_values(ascending=False).head(10).round(1)
    yearly = hist.groupby(hist["date"].dt.year)["quantity_sold"].sum()
    recent = hist[(hist["date"].dt.year >= 2020) & (hist["date"].dt.year <= 2025)]
    season = (recent.groupby(recent["date"].dt.month)["quantity_sold"].sum() / 6).round(0)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    # share of total demand per product, forecast vs the same months last year (a ranking view that a low bias does not distort)
    ly_months = [f"{int(m[:4]) - 1}{m[4:]}" for m in months]
    ly_prod = hist[hist["date"].dt.strftime("%Y-%m").isin(ly_months)].groupby("product_id")["quantity_sold"].sum()
    fc_prod = fc.groupby(["product_id", "product_name"])["forecast_quantity"].sum()
    mix = []
    for (pid, name), qty in fc_prod.items():
        f_share, l_share = qty / total_fc * 100, float(ly_prod.get(pid, 0)) / total_ly * 100 if total_ly else 0
        mix.append({"product": name, "fshare": round(f_share, 1), "lshare": round(l_share, 1), "shift": round(f_share - l_share, 1)})
    mix.sort(key=lambda r: r["shift"])
    mix_rows = list(reversed(mix[-5:])) + mix[:5]
    return {
        "as_of": str(hist["date"].max().date()),
        "kpis": [
            _kpi("Forecast demand, next 6 months", round(total_fc), "int", "units, all 30 products"),
            _kpi("vs same months last year", growth, "percent", f"{round(total_ly):,} units a year earlier" if total_ly else "", "good" if (growth or 0) >= 0 else "bad"),
            _kpi("Peak month", peak, "text", f"{round(float(by_month[peak])):,} units"),
            _kpi("Average per product per month", round(total_fc / 30 / len(months), 1), "int", "units"),
            _kpi("Model check: month 1 vs last 30 days sold", gap, "percent",
                 f"forecast {round(first_month):,} vs {round(last30):,} actual units", "bad" if biased else "good"),
        ],
        "tables": [_table("mix", "Which products gain or lose share of demand",
                          "Each product's share of total forecast units vs its share in the same months last year. Shares stay meaningful even though the model's totals run low.",
                          [("product", "Product", "text"), ("lshare", "Share last year %", "pct"), ("fshare", "Share forecast %", "pct"), ("shift", "Change (points)", "pct")],
                          mix_rows)],
        "charts": [
            {"id": "fc", "title": "Forecast vs the same months last year", "type": "line", "unit": "units",
             "series": ["Forecast", "Last year"],
             "data": [{"label": m, "values": [round(float(by_month[m]), 1), round(ly, 1)]} for m, ly in zip(months, last_year)]},
            {"id": "top", "title": "Top 10 products by forecast demand", "type": "hbar", "unit": "units",
             "data": [{"label": k, "value": float(v)} for k, v in top.items()]},
            {"id": "season", "title": "Typical month-by-month pattern (average of 2020 to 2025)", "type": "bar", "unit": "units",
             "data": [{"label": names[int(m) - 1], "value": float(v)} for m, v in season.items()]},
            {"id": "yearly", "title": "Total units sold per year (2026 is Jan to Oct only)", "type": "bar", "unit": "units",
             "data": [{"label": str(int(y)), "value": float(v)} for y, v in yearly.items()]},
        ],
        "warnings": ([f"Model check failed: the first forecast month ({round(first_month):,} units) is {abs(gap):.1f}% "
                      f"{'below' if gap < 0 else 'above'} what actually sold in the last 30 days ({round(last30):,} units). "
                      "A gap this large points to a biased model, not a real change in demand. In a back-test the model was "
                      "about 38% too low every month and a simple 90-day average was more accurate. Use the ranking of products "
                      "(which is reliable), not the totals or the change versus last year."] if biased else []),
        "derived": {"forecast_vs_same_months_last_year_percent": growth,
                    "first_forecast_month_vs_last_30_days_actual_percent": gap},
        "notes": ["About 91% of this history (2006 to 2024) is synthetic data generated to extend the sample dataset, "
                  "so these forecasts show what the model learned from simulated patterns, not real shop demand.",
                  "The forecast covers 6 months only, so it is compared with the same 6 months last year."],
        # instructions that go to the AI only (never shown on the page)
        "ai_notes": ["Never compare the 6-month forecast with yearly totals; the only fair comparison is the same 6 months last year."] + ([
                      f"WARNING: the first forecast month ({round(first_month):,} units) is {abs(gap):.1f}% "
                      f"{'below' if gap < 0 else 'above'} what actually sold in the last 30 days ({round(last30):,} units). "
                      "A gap this large means the model is biased, not that demand will really change. In a back-test the model "
                      "was about 38% too low every month and was beaten by a simple 90-day average. Tell the user the forecast "
                      "totals should not be trusted as real demand; the ranking of products is more reliable than the totals. "
                      "Do not present the drop versus last year as a real decline. Never suggest correction factors or adjusting orders by a "
                      "percentage. Instead say: plan quantities from recent actual sales (the last 30 to 90 days) and use the forecast "
                      "only to see which products rank higher or lower."] if biased else []),
    }


SOURCES = {
    "retail": {"label": "Retail database", "description": "Sales, stock levels and expiry from the shared retail tables.",
               "build": build_retail, "ready": lambda: bool(os.environ.get("RETAIL_DATABASE_URL")),
               "where": lambda: _where(os.environ.get("RETAIL_DATABASE_URL", ""))},
    "bills": {"label": "Bill Intake database", "description": "Purchase bills uploaded and reviewed in Bill Intake.",
              "build": build_bills, "ready": lambda: bool(_bills_url()), "where": lambda: _where(_bills_url())},
    "forecast": {"label": "Demand forecast data", "description": "The Transformer forecast and the sales history behind it.",
                 "build": build_forecast, "ready": lambda: (ROOT / "datasets" / "sales.csv").is_file(),
                 "where": lambda: "datasets/sales.csv + models/"},
}


def _source(source_id: str) -> dict:
    if source_id not in SOURCES:
        raise HTTPException(404, "Unknown data source")
    return SOURCES[source_id]


# ----------------------------------------------------------------------------- AI insights
class Insight(BaseModel):
    title: str
    detail: str
    tone: Literal["positive", "warning", "neutral"] = "neutral"
    action: str | None = None


class InsightList(BaseModel):
    insights: list[Insight]


SYSTEM_PROMPT = """You are a retail analytics assistant for a small shop owner in India.
You are given DATA: KPIs, chart data, action tables and pre-computed comparisons for one data source.

Rules:
- Use ONLY numbers that appear in DATA. Never invent numbers, causes or events.
- Write numbers in full (for example 1,23,456), never as "k", "lakh" or "million". Money is in rupees (₹).
- Do not do arithmetic: use the pre-computed percentages in DATA.derived when you need a change.
- Copy rupee amounts and counts exactly as they appear in DATA. Do not round them or swap a percentage for an amount.
- Only compare things that DATA directly compares. Do not mention averages, trends or events that are not in DATA.
- A percentage in DATA.derived has a specific label (for example "best_selling_single_product_..." is one product, not ten).
  Read the label before using it. A negative change means a decrease: write "drops 32.5%", not "-32.5%".
- Write 5 to 7 insights. Each has: title (at most 8 words), detail (1 or 2 plain sentences with concrete numbers),
  tone ("positive", "warning" or "neutral"), and optionally action (one short, practical suggestion).
- Put the most urgent, money-saving or money-making insight first (for example stock about to run out, stock about to expire, or lost margin).
- Do not just repeat a KPI. Say what it means for the shop and what to do.
- Skip any metric that is zero or has no decision attached to it. Fewer, better insights are better than filler.
- Never tell the owner to scale, multiply or adjust orders by a percentage you worked out.
- Put the most important or most urgent insight first.
- Reply with ONLY a JSON object: {"insights": [{"title": "...", "detail": "...", "tone": "...", "action": "..."}]}"""


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _llm(messages: list[dict], max_tokens: int = 2500) -> str:
    key = _llm_key()
    if not key:
        raise HTTPException(503, "No LLM API key configured (set SARVAM_API_KEY in bill-extractor/.env)")
    base = os.environ.get("LLM_BASE_URL", "https://api.sarvam.ai/v1")
    model = os.environ.get("LLM_MODEL", "sarvam-105b")
    last = ""
    for attempt in range(3):
        body = json.dumps({"model": model, "messages": messages, "temperature": 0.2,
                           "max_tokens": max_tokens, "reasoning_effort": None}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            text = re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"].get("content") or "", flags=re.S).strip()
            if text:
                return text
            max_tokens = min(max_tokens * 2, 16000)   # thinking used the budget
            last = "empty reply"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                raise HTTPException(502, f"The AI service rejected the request ({e.code})")
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise HTTPException(502, f"The AI service did not respond ({last})")


_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers_in(obj) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            try:
                out.add(float(m.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _numbers_in(v)
    return out


def _grounded(text: str, allowed: set[float]) -> bool:
    """True when every number in `text` appears in the data (small counts and years are exempt)."""
    for raw in _NUM.findall(text):
        try:
            x = float(raw.replace(",", ""))
        except ValueError:
            continue
        if (x == int(x) and x <= 12) or 1990 <= x <= 2100:
            continue
        if not any(abs(x - a) <= max(0.51, 0.001 * abs(a)) for a in allowed):
            return False
    return True


_AI_CACHE: dict[str, tuple[float, dict]] = {}
_AI_TTL = 30 * 60


def _facts(source_id: str, ds: dict) -> dict:
    return {"source": SOURCES[source_id]["label"], "as_of": ds["as_of"],
            "kpis": [{"label": k["label"], "value": k["value"], "note": k["hint"]} for k in ds["kpis"]],
            "charts": [{"title": c["title"], "unit": c["unit"], "series": c.get("series"), "data": c["data"]} for c in ds["charts"]],
            "tables": [{"title": t["title"], "how_it_is_calculated": t["hint"], "columns": [c["label"] for c in t["columns"]],
                        "rows": [[r.get(c["key"]) for c in t["columns"]] for r in t["rows"]]} for t in ds.get("tables", [])],
            "derived": ds["derived"], "notes": ds["notes"] + ds.get("ai_notes", [])}


# ----------------------------------------------------------------------------- routes
@router.get("/insights", include_in_schema=False)
def insights_page() -> FileResponse:
    return FileResponse(ROOT / "static" / "insights.html")


@router.get("/api/insights/sources")
def list_sources() -> dict:
    return {"llm_ready": bool(_llm_key()),
            "sources": [{"id": sid, "label": s["label"], "description": s["description"],
                         "available": bool(s["ready"]()), "where": s["where"]()} for sid, s in SOURCES.items()]}


@router.get("/api/insights/{source_id}")
def source_data(source_id: str) -> dict:
    source = _source(source_id)
    try:
        return {"source": source_id, "label": source["label"], "where": source["where"](), **source["build"]()}
    except SourceUnavailable as e:
        raise HTTPException(503, str(e))


@router.post("/api/insights/{source_id}/ai")
def source_ai(source_id: str, refresh: bool = False) -> dict:
    source = _source(source_id)
    try:
        ds = source["build"]()
    except SourceUnavailable as e:
        raise HTTPException(503, str(e))
    facts = _facts(source_id, ds)
    blob = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    key = f"{source_id}:{hashlib.sha1(blob.encode()).hexdigest()}"
    hit = _AI_CACHE.get(key)
    if hit and not refresh and time.time() - hit[0] < _AI_TTL:
        return {**hit[1], "cached": True}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "DATA:\n" + blob}]
    parsed, err = None, ""
    for _ in range(2):
        text = _llm(messages)
        raw = _first_json_object(text)
        try:
            parsed = InsightList.model_validate(json.loads(raw)) if raw else None
            if parsed and parsed.insights:
                break
            err = "no insights in the reply"
        except (ValueError, ValidationError) as e:
            err = str(e)[:200]
        messages = messages + [{"role": "assistant", "content": text[:1500]},
                               {"role": "user", "content": f"That was not valid ({err}). Reply with ONLY the JSON object."}]
    if not parsed or not parsed.insights:
        raise HTTPException(502, f"The AI did not return usable insights ({err})")

    allowed = {abs(n) for n in _numbers_in(facts)}
    items = [{**i.model_dump(), "verified": _grounded(f"{i.title} {i.detail} {i.action or ''}", allowed)} for i in parsed.insights[:8]]
    result = {"insights": items, "model": os.environ.get("LLM_MODEL", "sarvam-105b"),
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cached": False}
    _AI_CACHE[key] = (time.time(), result)
    return result
