"""GET /requests/me — the student's request and proposal if any. (stub)"""

from __future__ import annotations

from handlers._common import response


def handler(event, context):
    # TODO (hours 8-14): read student_id from auth context, repo.get_request + repo.group_for
    return response(501, {"error": "not implemented"})
