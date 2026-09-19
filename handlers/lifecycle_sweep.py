"""
Fires on the deadline-sweep timer (POST /internal/sweep, or an EventBridge
event when deployed). A FORMED group past its accept deadline is resolved:
silence is a TIMEOUT decline, routed through the same path as a real decline so
the decline budget counts it.
"""

from __future__ import annotations

import sys
import time
import traceback

from handlers._common import response
from handlers._decline import apply_decline
from repo import get_repo


def sweep(repo, now: int) -> dict:
    out = {"checked": 0, "confirmed": [], "dissolved": [], "errors": []}
    for stale in repo.formed_groups():
        out["checked"] += 1
        if stale["accept_deadline"] > now:
            continue
        try:
            # re-read: a respond may have resolved this group since the scan
            group = repo.get_group(stale["group_id"])
            if group["state"] != "FORMED":
                continue

            responses = group.get("responses") or {}
            silent = [m for m in group["members"] if responses.get(m) is not True]

            if not silent:  # everyone accepted but the confirming write was lost in a race
                repo.confirm(group["group_id"])
                out["confirmed"].append(group["group_id"])
                continue

            for member in silent:  # only the silent ones are penalised, not those who accepted
                apply_decline(repo, group, member, "TIMEOUT", {})
            repo.dissolve(group["group_id"], reason="TIMEOUT")
            out["dissolved"].append({"group_id": group["group_id"], "timed_out": silent})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            out["errors"].append({"group_id": stale["group_id"], "error": f"{type(exc).__name__}: {exc}"})
    return out


def handler(event, context):
    summary = sweep(get_repo(), int(time.time()))
    if "httpMethod" not in event:
        return summary
    return response(500 if summary["errors"] else 200, summary)
