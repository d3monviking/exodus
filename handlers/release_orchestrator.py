"""
Fires on the release schedule (EventBridge) or, as a fallback, on
POST /internal/release. (stub)

Handles both event shapes: a scheduled-rule event has no "httpMethod", a
direct HTTP call does — so the response format differs at the end.
"""

from __future__ import annotations

from handlers._common import response


def handler(event, context):
    is_http = "httpMethod" in event
    # TODO (hours 14-20): for each route, repo.open_pool -> solve() -> write
    # groups FORMED with accept_deadline, mark members GROUPED, call explainer,
    # dispatch proposals, repo.put_release, append S3 audit line. Must be
    # idempotent: bail if the route already has FORMED groups.
    if is_http:
        return response(501, {"error": "not implemented"})
    return {"status": "not implemented"}
