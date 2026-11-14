"""Bulk-load a folder of bills: python ingest.py ./bills [language]"""
import os
import sys

from dotenv import load_dotenv

import db
import pipeline
from sarvam_client import SarvamError


def main(folder, language="en-IN"):
    load_dotenv()
    db.init_db()
    files = sorted(
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in pipeline.ALLOWED_EXTENSIONS
    )
    counts = {"verified": 0, "pending_review": 0, "duplicate": 0, "failed": 0}
    for name in files:
        with open(os.path.join(folder, name), "rb") as f:
            content = f.read()
        try:
            bill_id = pipeline.process_bill(content, name, language)
            bill = db.get_bill(bill_id)
            counts[bill["status"]] += 1
            print(f"[{bill['status']:>14}] {name} -> #{bill_id} "
                  f"{bill['merchant_name']} total={bill['total_amount']}")
            for issue in bill["issues"]:
                print(f"{'':>17}- {issue}")
        except pipeline.DuplicateBill as e:
            counts["duplicate"] += 1
            print(f"[     duplicate] {name} -> already bill #{e.existing_id}")
        except (pipeline.InvalidFile, SarvamError) as e:
            counts["failed"] += 1
            print(f"[        failed] {name}: {e}")
    print(f"\nDone: {len(files)} files. " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python ingest.py <folder> [language]")
    main(sys.argv[1], *sys.argv[2:3])
