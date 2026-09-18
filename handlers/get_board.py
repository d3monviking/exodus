"""GET /board — countdown, pool size, last release stats. (stub)"""

from __future__ import annotations

from handlers._common import response


def handler(event, context):
    # TODO (hours 8-14): repo.open_pool + repo.latest_release per route
    return response(501, {"error": "not implemented"})
