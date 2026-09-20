"""Shared helpers for API Gateway proxy-integration Lambda handlers."""

from __future__ import annotations

import json
import re

from config import STUDENT_ID_RE

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$")
_STUDENT_ID_RE = re.compile(STUDENT_ID_RE, re.IGNORECASE)


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

    Demo-grade: nothing verifies that the address belongs to the caller.
    student_id is the local part, so imt2022001@iiitb.ac.in -> imt2022001.

    Returns None if the header is missing, malformed, or not a roll number:
    hello@iiitb.ac.in passes the domain rule but is nobody. The domain itself
    is Cedar's job (policy 1), the shape of the id is ours.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    email = (headers.get("x-student-email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return None
    student_id = email.split("@", 1)[0]
    if not _STUDENT_ID_RE.match(student_id):
        return None
    return {"id": student_id, "email": email}
