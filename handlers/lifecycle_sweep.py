"""
Fires on the deadline-sweep schedule (EventBridge) or, as a fallback, on
POST /internal/sweep. (stub)
"""

from __future__ import annotations

from handlers._common import response


def handler(event, context):
    is_http = "httpMethod" in event
    # TODO (hours 14-20): find FORMED groups past accept_deadline, route each
    # through lifecycle.on_decline(request, "TIMEOUT", {}) same as a real decline,
    # then repo.dissolve(group_id).
    if is_http:
        return response(501, {"error": "not implemented"})
    return {"status": "not implemented"}
