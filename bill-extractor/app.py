"""Bill extractor: JSON API + a plain HTML UI. One process, one SQLite file.

Endpoints are plain `def` (not async) because the Sarvam call blocks; FastAPI
runs them in a thread pool.
"""
import os
from contextlib import asynccontextmanager
from itertools import zip_longest

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import db
import pipeline
import retail
from sarvam_client import SarvamError

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(pipeline.UPLOAD_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


app = FastAPI(title="Bill Extractor", lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=pipeline.UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
def _num(value):
    """Show 2000.0 as 2000 and 12.50 as 12.5; empty for None."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return str(int(f)) if f == int(f) else f"{f:.3f}".rstrip("0").rstrip(".")


templates.env.filters["num"] = _num
templates.env.globals.update(
    assistant_url=os.environ.get("ASSISTANT_URL", "http://localhost:5173"),
    status_labels={
        "verified": "Ready", "pending_review": "Needs review", "posted": "In inventory",
        "posting": "Adding…", "rejected": "Rejected",
    },
    language_names={
        "en-IN": "English", "hi-IN": "Hindi", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada",
        "ml-IN": "Malayalam", "mr-IN": "Marathi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    },
)


def _process(file: UploadFile, language: str):
    """Run the pipeline; returns (bill_id, error_message, http_status)."""
    try:
        bill_id = pipeline.process_bill(file.file.read(), file.filename or "", language)
        return bill_id, None, 201
    except pipeline.DuplicateBill as e:
        return e.existing_id, str(e), 409
    except pipeline.InvalidFile as e:
        return None, str(e), 400
    except SarvamError as e:
        return None, str(e), 502


# ---------------------------------------------------------------- JSON API

class StatusUpdate(BaseModel):
    status: str


@app.post("/bills")
def create_bill(file: UploadFile = File(...), language: str = Form("en-IN")):
    bill_id, error, code = _process(file, language)
    if error:
        return JSONResponse({"error": error, "existing_id": bill_id if code == 409 else None},
                            status_code=code)
    return JSONResponse(db.get_bill(bill_id), status_code=201)


@app.get("/bills")
def list_bills():
    return db.list_bills()


@app.get("/bills/{bill_id}")
def get_bill(bill_id: int):
    bill = db.get_bill(bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    return bill


def _apply_edit(bill_id, fields):
    """Shared by the JSON and form edit endpoints."""
    return pipeline.update_bill(bill_id, fields)


@app.put("/bills/{bill_id}")
def edit_bill(bill_id: int, body: dict):
    try:
        _apply_edit(bill_id, body)
    except LookupError:
        raise HTTPException(404, "Bill not found")
    except pipeline.InvalidEdit as e:
        raise HTTPException(400, str(e))
    return db.get_bill(bill_id)


@app.post("/bills/{bill_id}/post")
def post_bill(bill_id: int):
    """Add the bill's matched items to the retail batches and inventory tables."""
    try:
        created, skipped = pipeline.post_to_inventory(bill_id)
    except LookupError:
        raise HTTPException(404, "Bill not found")
    except pipeline.PostError as e:
        raise HTTPException(400, str(e))
    return {"batches": created, "skipped_items": skipped}


@app.post("/bills/{bill_id}/status")
def set_status(bill_id: int, body: StatusUpdate):
    if body.status not in db.STATUSES:
        raise HTTPException(400, f"status must be one of {db.STATUSES}")
    if not db.update_status(bill_id, body.status):
        raise HTTPException(404, "Bill not found")
    return db.get_bill(bill_id)


# ---------------------------------------------------------------- Web UI

def _index(request: Request, message=None, error=None, status_code=200):
    bills = db.list_bills()
    count = lambda status: sum(1 for b in bills if b["status"] == status)
    kpis = {"total": len(bills), "review": count("pending_review"),
            "ready": count("verified"), "posted": count("posted"), "rejected": count("rejected")}
    return templates.TemplateResponse(request, "index.html", {
        "bills": bills, "kpis": kpis,
        "languages": pipeline.LANGUAGES,
        "message": message,
        "error": error,
    }, status_code=status_code)


@app.get("/")
def index(request: Request):
    return _index(request)


@app.post("/upload")
def upload(request: Request, file: UploadFile = File(...), language: str = Form("en-IN")):
    bill_id, error, code = _process(file, language)
    if not error:
        return RedirectResponse(f"/view/{bill_id}", status_code=303)
    if code == 409:
        return _index(request, error=f"{error}. Open bill #{bill_id} in the list below.",
                      status_code=409)
    return _index(request, error=error, status_code=code)


def _view_page(request, bill, error=None, saved=False, posted=False, dirty=False, status_code=200):
    is_pdf = (bill["image_path"] or "").lower().endswith(".pdf")
    products, supplier, retail_error = [], None, None
    try:
        products = retail.list_products()
        supplier = retail.find_supplier(bill.get("merchant_name"))
    except retail.RetailError as e:
        retail_error = str(e)
    return templates.TemplateResponse(request, "view.html", {
        "bill": bill, "is_pdf": is_pdf, "error": error, "saved": saved, "posted": posted, "dirty": dirty,
        "payment_modes": ["cash", "upi", "card", "credit", "other"],
        "products": products, "supplier": supplier, "retail_error": retail_error,
    }, status_code=status_code)


@app.get("/view/{bill_id}")
def view(request: Request, bill_id: int, saved: int = 0, posted: int = 0):
    bill = db.get_bill(bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    return _view_page(request, bill, saved=bool(saved), posted=bool(posted))


@app.post("/view/{bill_id}/edit")
def view_edit(
    request: Request,
    bill_id: int,
    merchant_name: str = Form(""), merchant_gstin: str = Form(""),
    bill_number: str = Form(""), bill_date: str = Form(""), payment_mode: str = Form(""),
    subtotal: str = Form(""), tax_amount: str = Form(""),
    discount: str = Form(""), total_amount: str = Form(""),
    item_description: list[str] = Form([]), item_quantity: list[str] = Form([]),
    item_unit_price: list[str] = Form([]), item_amount: list[str] = Form([]),
    item_product_id: list[str] = Form([]), item_units: list[str] = Form([]),
):
    items = [
        {"description": d, "quantity": q, "unit_price": u, "amount": a,
         "product_id": pid, "units": un}
        for d, q, u, a, pid, un in zip_longest(item_description, item_quantity, item_unit_price,
                                               item_amount, item_product_id, item_units,
                                               fillvalue="")
    ]
    fields = {
        "merchant_name": merchant_name, "merchant_gstin": merchant_gstin,
        "bill_number": bill_number, "bill_date": bill_date, "payment_mode": payment_mode,
        "subtotal": subtotal, "tax_amount": tax_amount, "discount": discount,
        "total_amount": total_amount, "line_items": items,
    }
    try:
        _apply_edit(bill_id, fields)
    except LookupError:
        raise HTTPException(404, "Bill not found")
    except pipeline.InvalidEdit as e:
        # Show the page again with what the user typed, so nothing is lost.
        bill = db.get_bill(bill_id)
        bill.update({k: v for k, v in fields.items() if k != "line_items"})
        bill["line_items"] = items
        return _view_page(request, bill, error=str(e), dirty=True, status_code=400)
    return RedirectResponse(f"/view/{bill_id}?saved=1", status_code=303)


@app.post("/view/{bill_id}/post")
def view_post(request: Request, bill_id: int):
    try:
        pipeline.post_to_inventory(bill_id)
    except LookupError:
        raise HTTPException(404, "Bill not found")
    except pipeline.PostError as e:
        return _view_page(request, db.get_bill(bill_id), error=str(e), status_code=400)
    return RedirectResponse(f"/view/{bill_id}?posted=1", status_code=303)


@app.post("/view/{bill_id}/status")
def view_set_status(bill_id: int, status: str = Form(...)):
    if status not in db.STATUSES or not db.update_status(bill_id, status):
        raise HTTPException(400, "Invalid status or bill")
    return RedirectResponse(f"/view/{bill_id}", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
