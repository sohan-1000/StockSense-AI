"""Thin client for Sarvam Document AI Extract (submit -> poll -> results)."""
import json
import mimetypes
import os
import time

import requests

from schema import BILL_SCHEMA

BASE_URL = "https://api.sarvam.ai/doc-ai/v1/job"
TERMINAL = {"completed", "partially_completed", "failed", "rejected"}
POLL_SECONDS = 3
TIMEOUT_SECONDS = 180


class SarvamError(Exception):
    pass


def _headers():
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise SarvamError("SARVAM_API_KEY is not set (put it in .env)")
    return {"api-subscription-key": key}


def _check(resp, what):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail") or resp.text
        except ValueError:
            detail = resp.text
        raise SarvamError(f"Sarvam {what} failed ({resp.status_code}): {detail}")
    return resp.json()


def extract_bill(filename, content, language="en-IN"):
    """Send one bill to Sarvam and return the extracted fields as a dict."""
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    resp = requests.post(
        f"{BASE_URL}/extract",
        headers=_headers(),
        files={"file": (filename, content, mime)},
        data={
            "schema": json.dumps(BILL_SCHEMA),
            "language": language,
            "output_format": "json",
        },
        timeout=60,
    )
    job_id = _check(resp, "submit")["job_id"]

    deadline = time.time() + TIMEOUT_SECONDS
    while True:
        status = _check(
            requests.get(f"{BASE_URL}/{job_id}/status", headers=_headers(), timeout=30),
            "status",
        ).get("status", "")
        if status.lower() in TERMINAL:
            break
        if time.time() > deadline:
            raise SarvamError(f"Sarvam job {job_id} timed out after {TIMEOUT_SECONDS}s")
        time.sleep(POLL_SECONDS)

    if status.lower() not in ("completed", "partially_completed"):
        raise SarvamError(f"Sarvam job {job_id} ended with status '{status}'")

    body = _check(
        requests.get(f"{BASE_URL}/{job_id}/results", headers=_headers(), timeout=30),
        "results",
    )
    return parse_results(body)


def parse_results(body):
    """Pull the extracted fields out of a /results response."""
    result = body.get("result", body) if isinstance(body, dict) else body
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            raise SarvamError("Sarvam result was not valid JSON")
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    if not isinstance(result, dict):
        raise SarvamError(f"Unexpected Sarvam result format: {type(result).__name__}")
    return result
