"""Shared helpers for API Gateway proxy-integration Lambda handlers."""

from __future__ import annotations

import json
import re

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$")


def response(status: int, body: dict | list) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw)


def identify(event: dict) -> dict | None:
    """Caller identity from the X-Student-Email header.

    Demo-grade: nothing verifies the address. student_id is the local part, so
    imt2022001@iiitb.ac.in -> imt2022001. Returns None if the header is
    missing or malformed; the domain rule itself is Cedar's job, not ours.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    email = (headers.get("x-student-email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return None
    return {"id": email.split("@", 1)[0], "email": email}
