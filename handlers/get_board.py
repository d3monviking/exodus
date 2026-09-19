"""GET /board — public: per-route pool size, last release stats, next-release countdown."""

from __future__ import annotations

import os
import time

from config import ROUTES
from handlers._common import response
from repo import get_repo

RELEASE_INTERVAL_SECONDS = int(os.environ.get("RELEASE_INTERVAL_SECONDS", "120"))


def handler(event, context):
    repo = get_repo()
    now = int(time.time())

    routes = {}
    last_ran = None
    for route in ROUTES:
        release = repo.latest_release(route)
        routes[route] = {
            "pool_size": len(repo.open_pool(route)),
            "last_release": release,
        }
        if release:
            last_ran = max(last_ran or 0, release["ran_at"])

    # Approximate: the timer loop fires every RELEASE_INTERVAL_SECONDS, so the
    # next one lands one interval after the last. Unknown until a release has run.
    next_release_at = last_ran + RELEASE_INTERVAL_SECONDS if last_ran else None

    return response(
        200,
        {
            "server_time": now,
            "next_release_at": next_release_at,
            "release_interval_seconds": RELEASE_INTERVAL_SECONDS,
            "routes": routes,
        },
    )
