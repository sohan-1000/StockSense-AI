import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checks  # noqa: E402
import cleaning  # noqa: E402
import db  # noqa: E402
import dev_retail_db  # noqa: E402
import matching  # noqa: E402
import pipeline  # noqa: E402
import retail  # noqa: E402
import sarvam_client  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def use_throwaway_schema(test, stub_retail=True):
    """Point db at a fresh Postgres schema for this test and drop it afterwards.

    By default the retail DB is stubbed out so tests never touch real data.
    """
    name = "test_" + uuid.uuid4().hex[:12]
    p = mock.patch.object(db, "SCHEMA_NAME", name)
    p.start()
    test.addCleanup(db.drop_schema, name)
    test.addCleanup(p.stop)
    db.init_db()
    if stub_retail:
        for target, value in (("list_products", []), ("find_supplier", None)):
            q = mock.patch.object(retail, target, return_value=value)
            q.start()
            test.addCleanup(q.stop)


PRODUCTS = [
    {"product_id": "P008", "product_name": "Basmati Rice 1kg"},
    {"product_id": "P010", "product_name": "Toor Dal 1kg"},
    {"product_id": "P012", "product_name": "Sunflower Oil 1L"},
    {"product_id": "P002", "product_name": "Curd 500g"},
]


def use_retail_schema(test):
    """Create a throwaway copy of the retail tables, seeded with a few rows."""
    import psycopg
    if not retail.configured():
        raise unittest.SkipTest("RETAIL_DATABASE_URL is not set")
    name = "rtest_" + uuid.uuid4().hex[:12]
    url = os.environ["RETAIL_DATABASE_URL"]
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        conn.execute(dev_retail_db.DDL)
        conn.execute("INSERT INTO suppliers VALUES ('S9', 'Sri Sai Traders', 'Hyderabad', 2, 10)")
        conn.execute("INSERT INTO products VALUES "
                     "('P008', 'Basmati Rice 1kg', 'Staples', 90, 70, 365, 3, 5, 2, 'S9', 10), "
                     "('P010', 'Toor Dal 1kg', 'Staples', 150, 120, 270, 3, 5, 2, 'S9', 10), "
                     "('P012', 'Sunflower Oil 1L', 'Staples', 170, 140, 180, 3, 5, 2, 'S9', 10)")
        conn.execute("INSERT INTO inventory VALUES ('P008', 10, '2026-10-31', 1.5, 5)")
        conn.execute("INSERT INTO batches VALUES ('B00801', 'P008', 10, '2026-10-01', '2027-10-01')")

    def drop():
        with psycopg.connect(url) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')

    p = mock.patch.object(retail, "SCHEMA_NAME", name)
    p.start()
    test.addCleanup(drop)
    test.addCleanup(p.stop)


GOOD_RAW = {
    "merchant_name": "  Sri Sai   Traders ",
    "merchant_gstin": "27aapfu0939f1zv",
    "bill_number": "INV-42",
    "bill_date": "25/12/2024",
    "payment_mode": "Paid via PhonePe",
    "subtotal": "₹1,000.00",
    "tax_amount": "180",
    "discount": None,
    "total_amount": "Rs. 1,180/-",
    "line_items": [
        {"description": "Rice 5kg", "quantity": 2, "unit_price": "300", "amount": "600"},
        {"description": "Oil 1L", "quantity": 2, "unit_price": 200, "amount": 400},
    ],
}


def fake_extract(raw):
    return lambda filename, content, language: raw


class CleaningTests(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(cleaning.clean_amount("₹1,234.50"), 1234.5)
        self.assertEqual(cleaning.clean_amount("Rs. 99/-"), 99.0)
        self.assertEqual(cleaning.clean_amount(12), 12.0)
        self.assertIsNone(cleaning.clean_amount("n/a"))
        self.assertIsNone(cleaning.clean_amount(None))

    def test_dates_are_day_first(self):
        self.assertEqual(cleaning.clean_date("05/03/2024"), "2024-03-05")
        self.assertEqual(cleaning.clean_date("5-Mar-24"), "2024-03-05")
        self.assertEqual(cleaning.clean_date("2024-03-05 14:32"), "2024-03-05")
        self.assertIsNone(cleaning.clean_date("yesterday"))

    def test_gstin(self):
        self.assertEqual(cleaning.clean_gstin(" 27aapfu0939f1zv "), ("27AAPFU0939F1ZV", True))
        self.assertEqual(cleaning.clean_gstin("27AAPFU0939F1Z"), ("27AAPFU0939F1Z", False))
        self.assertEqual(cleaning.clean_gstin(None), (None, True))


class CheckTests(unittest.TestCase):
    def test_good_bill_has_no_issues(self):
        self.assertEqual(checks.run_checks(pipeline.normalize(GOOD_RAW)), [])

    def test_bad_totals_and_missing_fields_are_flagged(self):
        raw = dict(GOOD_RAW, total_amount=5000, merchant_name=None, bill_date="??")
        issues = " | ".join(checks.run_checks(pipeline.normalize(raw)))
        self.assertIn("Merchant name is missing", issues)
        self.assertIn("date is missing", issues)
        self.assertIn("Totals do not add up", issues)


class SarvamParsingTests(unittest.TestCase):
    def test_reads_result_key(self):
        self.assertEqual(sarvam_client.parse_results({"result": {"a": 1}}), {"a": 1})

    def test_result_as_json_string(self):
        self.assertEqual(sarvam_client.parse_results({"result": '{"a": 1}'}), {"a": 1})

    def test_bad_format_raises(self):
        with self.assertRaises(sarvam_client.SarvamError):
            sarvam_client.parse_results({"result": "not json"})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(pipeline, "UPLOAD_DIR", os.path.join(self.tmp.name, "up"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        use_throwaway_schema(self)

    def test_good_bill_is_saved_verified(self):
        bill_id = pipeline.process_bill(b"img1", "a.jpg", "en-IN", fake_extract(GOOD_RAW))
        bill = db.get_bill(bill_id)
        self.assertEqual(bill["status"], "verified")
        self.assertEqual(bill["merchant_name"], "Sri Sai Traders")
        self.assertEqual(bill["merchant_gstin"], "27AAPFU0939F1ZV")
        self.assertEqual(bill["bill_date"], "2024-12-25")
        self.assertEqual(bill["payment_mode"], "upi")
        self.assertEqual(bill["total_amount"], 1180.0)
        self.assertEqual(len(bill["line_items"]), 2)
        self.assertTrue(os.path.exists(os.path.join(pipeline.UPLOAD_DIR, bill["image_path"])))

    def test_failed_check_goes_to_pending_review(self):
        raw = dict(GOOD_RAW, total_amount=9999)
        bill = db.get_bill(pipeline.process_bill(b"img2", "b.png", "en-IN", fake_extract(raw)))
        self.assertEqual(bill["status"], "pending_review")
        self.assertTrue(bill["issues"])

    def test_duplicate_is_rejected_before_calling_sarvam(self):
        pipeline.process_bill(b"img3", "c.jpg", "en-IN", fake_extract(GOOD_RAW))
        called = mock.Mock()
        with self.assertRaises(pipeline.DuplicateBill):
            pipeline.process_bill(b"img3", "c.jpg", "en-IN", called)
        called.assert_not_called()

    def test_bad_file_type_and_status_update(self):
        with self.assertRaises(pipeline.InvalidFile):
            pipeline.process_bill(b"x", "notes.txt", "en-IN", fake_extract(GOOD_RAW))
        bill_id = pipeline.process_bill(b"img4", "d.jpg", "en-IN", fake_extract(GOOD_RAW))
        self.assertTrue(db.update_status(bill_id, "rejected"))
        self.assertEqual(db.get_bill(bill_id)["status"], "rejected")
        with self.assertRaises(ValueError):
            db.update_status(bill_id, "bogus")


class EditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(pipeline, "UPLOAD_DIR", os.path.join(self.tmp.name, "up"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        use_throwaway_schema(self)

    def test_edit_fixes_a_bad_bill_and_keeps_raw(self):
        raw = dict(GOOD_RAW, total_amount=9999)
        bill_id = pipeline.process_bill(b"e1", "e.jpg", "en-IN", fake_extract(raw))
        self.assertEqual(db.get_bill(bill_id)["status"], "pending_review")

        fields = dict(raw, total_amount="1180", merchant_name="Sai Traders",
                      line_items=[{"description": "Rice", "quantity": "2",
                                   "unit_price": "500", "amount": "1000"}])
        self.assertEqual(pipeline.update_bill(bill_id, fields), "verified")
        bill = db.get_bill(bill_id)
        self.assertEqual(bill["merchant_name"], "Sai Traders")
        self.assertEqual(bill["total_amount"], 1180.0)
        self.assertEqual(len(bill["line_items"]), 1)
        self.assertTrue(bill["manually_edited"])
        self.assertEqual(bill["raw_json"]["total_amount"], 9999)  # original kept

    def test_unreadable_values_are_rejected_and_nothing_saved(self):
        bill_id = pipeline.process_bill(b"e2", "f.jpg", "en-IN", fake_extract(GOOD_RAW))
        with self.assertRaises(pipeline.InvalidEdit):
            pipeline.update_bill(bill_id, dict(GOOD_RAW, bill_date="someday", total_amount="abc"))
        self.assertEqual(db.get_bill(bill_id)["merchant_name"], "Sri Sai Traders")
        with self.assertRaises(LookupError):
            pipeline.update_bill(99999, GOOD_RAW)


PURCHASE_RAW = {
    "merchant_name": "Sri Sai Traders", "bill_number": "P-1", "bill_date": "12/11/2026",
    "subtotal": 1400, "tax_amount": 0, "total_amount": 1400,
    "line_items": [
        {"description": "Basmati Rice 5kg", "quantity": 2, "unit_price": 450, "amount": 900},
        {"description": "Sunflower Oil 1 Ltr", "quantity": 3, "unit_price": 160, "amount": 480},
        {"description": "Carry bag", "quantity": 1, "unit_price": 20, "amount": 20},
    ],
}


class MatchingTests(unittest.TestCase):
    def test_matches_names_and_converts_pack_sizes(self):
        items = matching.apply([dict(i) for i in PURCHASE_RAW["line_items"]], PRODUCTS)
        rice, oil, bag = items
        self.assertEqual((rice["product_id"], rice["units"]), ("P008", 10))  # 2 x 5kg of a 1kg product
        self.assertEqual((oil["product_id"], oil["units"]), ("P012", 3))     # "1 Ltr" == "1L"
        self.assertIsNone(bag["product_id"])                                 # not guessed

    def test_pack_factor(self):
        self.assertEqual(matching.pack_factor("Rice 5kg", "Rice 1kg"), 5)
        self.assertEqual(matching.pack_factor("Oil 2L", "Oil 500ml"), 4)
        self.assertEqual(matching.pack_factor("Bread", "Bread"), 1)
        self.assertIsNone(matching.pack_factor("Curd 500g", "Curd 1kg"))  # cannot convert cleanly


class PostToInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(pipeline, "UPLOAD_DIR", os.path.join(self.tmp.name, "up"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        use_throwaway_schema(self, stub_retail=False)
        use_retail_schema(self)

    def stock(self, product_id):
        with retail.connect() as c:
            row = c.execute("SELECT current_stock FROM inventory WHERE product_id = %s",
                            (product_id,)).fetchone()
        return float(row["current_stock"]) if row else None

    def new_bill(self, content=b"p1"):
        return pipeline.process_bill(content, "p.jpg", "en-IN", fake_extract(PURCHASE_RAW))

    def test_items_are_matched_on_upload(self):
        bill = db.get_bill(self.new_bill())
        self.assertEqual([(i["product_id"], i["units"]) for i in bill["line_items"]],
                         [("P008", 10.0), ("P012", 3.0), (None, None)])

    def test_post_creates_batches_and_adds_stock(self):
        bill_id = self.new_bill()
        created, skipped = pipeline.post_to_inventory(bill_id)
        self.assertEqual(skipped, 1)
        by_product = {b["product_id"]: b for b in created}
        self.assertEqual(by_product["P008"]["batch_id"], "B00802")  # B00801 already exists
        self.assertEqual(by_product["P008"]["expiry_date"], "2027-11-12")  # + 365 days
        self.assertEqual(by_product["P012"]["batch_id"], "B01201")
        self.assertEqual(self.stock("P008"), 20)   # 10 existing + 10 bought
        self.assertEqual(self.stock("P012"), 3)    # no inventory row before: inserted
        with retail.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) AS n FROM sales").fetchone()["n"], 0)
            # the chatbot's invariant: batches add up to current stock, per product
            mismatch = c.execute(
                "SELECT i.product_id FROM inventory i JOIN (SELECT product_id, sum(quantity_remaining) q "
                "FROM batches GROUP BY product_id) b USING (product_id) WHERE b.q <> i.current_stock"
            ).fetchall()
            self.assertEqual(mismatch, [])
            # snapshot_date is the dataset's shared "as of" date and must not move
            dates = {str(r["snapshot_date"]) for r in c.execute("SELECT snapshot_date FROM inventory").fetchall()}
            self.assertEqual(dates, {"2026-10-31"})
            # the chatbot's views still work and see the new batches
            expiry = c.execute("SELECT batch_id, days_to_expiry FROM v_batch_expiry "
                               "WHERE batch_id = 'B00802'").fetchone()
            self.assertEqual(expiry["days_to_expiry"], 377)   # 2027-11-12 minus 2026-10-31
            status = c.execute("SELECT current_stock FROM v_inventory_status "
                               "WHERE product_id = 'P012'").fetchone()
            self.assertEqual(status["current_stock"], 3)
        bill = db.get_bill(bill_id)
        self.assertEqual(bill["status"], "posted")
        self.assertEqual(len(bill["posted_batches"]), 2)

    def test_cannot_post_or_edit_twice(self):
        bill_id = self.new_bill()
        pipeline.post_to_inventory(bill_id)
        with self.assertRaises(pipeline.PostError):
            pipeline.post_to_inventory(bill_id)
        with self.assertRaises(pipeline.InvalidEdit):
            pipeline.update_bill(bill_id, PURCHASE_RAW)
        self.assertEqual(self.stock("P008"), 20)  # not added again

    def test_fractional_units_are_refused(self):
        bill_id = self.new_bill()
        fields = dict(PURCHASE_RAW, line_items=[
            {"description": "Rice", "quantity": "2.5", "amount": "100", "product_id": "P008", "units": "2.5"}])
        pipeline.update_bill(bill_id, fields)
        with self.assertRaises(pipeline.PostError) as ctx:
            pipeline.post_to_inventory(bill_id)
        self.assertIn("whole number", str(ctx.exception))
        self.assertEqual(self.stock("P008"), 10)

    def test_failed_post_changes_nothing(self):
        bill_id = self.new_bill()
        fields = dict(PURCHASE_RAW, line_items=[
            {"description": "Rice", "quantity": "1", "amount": "100", "product_id": "P008", "units": "5"},
            {"description": "Ghost", "quantity": "1", "amount": "100", "product_id": "P999", "units": "5"},
        ])
        pipeline.update_bill(bill_id, fields)
        with self.assertRaises(pipeline.PostError):
            pipeline.post_to_inventory(bill_id)
        self.assertEqual(self.stock("P008"), 10)              # first item rolled back too
        self.assertIn(db.get_bill(bill_id)["status"], ("verified", "pending_review"))

    def test_rejected_and_unmatched_bills_are_not_posted(self):
        bill_id = self.new_bill()
        db.update_status(bill_id, "rejected")
        with self.assertRaises(pipeline.PostError):
            pipeline.post_to_inventory(bill_id)
        other = pipeline.process_bill(b"p2", "q.jpg", "en-IN", fake_extract(dict(
            PURCHASE_RAW, line_items=[{"description": "Carry bag", "quantity": 1, "amount": 1400}])))
        with self.assertRaises(pipeline.PostError):
            pipeline.post_to_inventory(other)

    def test_supplier_lookup_and_web_flow(self):
        from fastapi.testclient import TestClient
        import app as app_module
        self.assertEqual(retail.find_supplier("Sri Sai Traders")["supplier_id"], "S9")
        self.assertIsNone(retail.find_supplier("Nobody Ltd"))
        with mock.patch.object(sarvam_client, "extract_bill", fake_extract(PURCHASE_RAW)):
            client = TestClient(app_module.app, follow_redirects=False)
            bill_id = client.post("/bills", files={"file": ("w.jpg", b"w1", "image/jpeg")}).json()["id"]
            page = client.get(f"/view/{bill_id}")
            self.assertIn("Known supplier: S9", page.text)
            self.assertIn("P008 · Basmati Rice 1kg", page.text)
            r = client.post(f"/view/{bill_id}/post")
            self.assertEqual((r.status_code, r.headers["location"]), (303, f"/view/{bill_id}?posted=1"))
            self.assertIn("B00802", client.get(f"/view/{bill_id}").text)
            again = client.post(f"/bills/{bill_id}/post")
            self.assertEqual(again.status_code, 400)
        self.assertEqual(self.stock("P008"), 20)


class WebTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app as app_module

        self.tmp = tempfile.TemporaryDirectory()
        for p in (
            mock.patch.object(pipeline, "UPLOAD_DIR", os.path.join(self.tmp.name, "up")),
            mock.patch.object(sarvam_client, "extract_bill", fake_extract(GOOD_RAW)),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        use_throwaway_schema(self)
        self.client = TestClient(app_module.app, follow_redirects=False)

    def test_api_and_ui_flow(self):
        r = self.client.post("/bills", files={"file": ("a.jpg", b"web1", "image/jpeg")})
        self.assertEqual(r.status_code, 201)
        bill_id = r.json()["id"]
        self.assertEqual(r.json()["merchant_name"], "Sri Sai Traders")

        dup = self.client.post("/bills", files={"file": ("a.jpg", b"web1", "image/jpeg")})
        self.assertEqual(dup.status_code, 409)

        page = self.client.get("/")
        self.assertIn("Sri Sai Traders", page.text)

        up = self.client.post("/upload", files={"file": ("b.jpg", b"web2", "image/jpeg")},
                              data={"language": "hi-IN"})
        self.assertEqual(up.status_code, 303)
        self.assertTrue(up.headers["location"].startswith("/view/"))

        dup_ui = self.client.post("/upload", files={"file": ("a.jpg", b"web1", "image/jpeg")})
        self.assertEqual(dup_ui.status_code, 409)
        self.assertIn("already uploaded", dup_ui.text)

        view = self.client.get(f"/view/{bill_id}")
        self.assertIn("Rice 5kg", view.text)

        ok = self.client.post(f"/view/{bill_id}/status", data={"status": "rejected"})
        self.assertEqual(ok.status_code, 303)
        self.assertEqual(self.client.get(f"/bills/{bill_id}").json()["status"], "rejected")

    def test_edit_form_and_json_edit(self):
        bill_id = self.client.post(
            "/bills", files={"file": ("a.jpg", b"web9", "image/jpeg")}).json()["id"]
        data = {"merchant_name": "Edited Store", "merchant_gstin": "", "bill_number": "X1",
                "bill_date": "01/01/2026", "payment_mode": "card", "subtotal": "100",
                "tax_amount": "0", "discount": "", "total_amount": "100",
                "item_description": ["Pen", "Book"], "item_quantity": ["1", "1"],
                "item_unit_price": ["40", "60"], "item_amount": ["40", "60"]}
        r = self.client.post(f"/view/{bill_id}/edit", data=data)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], f"/view/{bill_id}?saved=1")
        bill = self.client.get(f"/bills/{bill_id}").json()
        self.assertEqual((bill["merchant_name"], bill["status"], len(bill["line_items"])),
                         ("Edited Store", "verified", 2))
        page = self.client.get(f"/view/{bill_id}?saved=1")
        self.assertIn("Saved.", page.text)
        self.assertIn('value="Edited Store"', page.text)

        bad = self.client.post(f"/view/{bill_id}/edit", data=dict(data, bill_date="nope"))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not a readable date", bad.text)
        self.assertIn('value="nope"', bad.text)  # typed value is not lost

        put = self.client.put(f"/bills/{bill_id}", json=dict(GOOD_RAW))
        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.json()["merchant_name"], "Sri Sai Traders")

    def test_sarvam_error_shows_message_not_crash(self):
        boom = mock.Mock(side_effect=sarvam_client.SarvamError("Sarvam submit failed (402): quota"))
        with mock.patch.object(sarvam_client, "extract_bill", boom):
            r = self.client.post("/upload", files={"file": ("z.jpg", b"web3", "image/jpeg")})
        self.assertEqual(r.status_code, 502)
        self.assertIn("quota", r.text)


if __name__ == "__main__":
    unittest.main()
