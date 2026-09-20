"""GET /board — public: per-route pool size, last release stats, next-release countdown."""

from __future__ import annotations

import time

from config import ROUTES
from handlers._common import response
from repo import get_repo
from schedule import RELEASE_INTERVAL_SECONDS


def handler(event, context):
    repo = get_repo()
    now = int(time.time())

    routes = {
        route: {"pool_size": len(repo.open_pool(route)), "last_release": repo.latest_release(route)}
        for route in ROUTES
    }

    # Approximate: the timer loop fires every RELEASE_INTERVAL_SECONDS, so the
    # next run lands one interval after the last. The run-level row ("ALL") is
    # written on every run, idle or not, so this stays accurate while nothing is pending.
    last_run = repo.latest_release("ALL")
    next_release_at = last_run["ran_at"] + RELEASE_INTERVAL_SECONDS if last_run else None

    return response(
        200,
        {
            "server_time": now,
            "next_release_at": next_release_at,
            "release_interval_seconds": RELEASE_INTERVAL_SECONDS,
            "routes": routes,
        },
    )
