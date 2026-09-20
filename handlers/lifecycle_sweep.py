"""
Fires on the deadline-sweep timer (POST /internal/sweep, or an EventBridge
event when deployed). A FORMED group past its accept deadline is resolved:
silence is a TIMEOUT decline, routed through the same path as a real decline so
the decline budget counts it, and a trio that loses one silent member carries on
as a pair exactly as it would after a decline.

An offered third seat that nobody answered in time is a TIMEOUT decline of the
seat: the offer ends and the confirmed pair is left alone.
"""

from __future__ import annotations

import sys
import time
import traceback

from handlers._common import response
from handlers._decline import apply_decline
from handlers._groups import REDUCED, confirm_group, resolve_departure
from repo import get_repo


def sweep(repo, now: int) -> dict:
    out = {"checked": 0, "confirmed": [], "dissolved": [], "reduced": [], "offers_expired": [], "errors": []}
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
                confirm_group(repo, group["group_id"], now)
                out["confirmed"].append(group["group_id"])
                continue

            for member in silent:  # only the silent ones are penalised, not those who accepted
                apply_decline(repo, group, member, "TIMEOUT", {})
            outcome = resolve_departure(repo, group, silent, now, reason="TIMEOUT")
            out["reduced" if outcome == REDUCED else "dissolved"].append(
                {"group_id": group["group_id"], "timed_out": silent})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            out["errors"].append({"group_id": stale["group_id"], "error": f"{type(exc).__name__}: {exc}"})

    for stale in repo.groups_with_offers():
        try:
            group = repo.get_group(stale["group_id"])  # re-read: an accept may have landed since the scan
            offer = group.get("seat_offer")
            if not offer or offer["deadline"] > now:
                continue
            apply_decline(repo, group, offer["student_id"], "TIMEOUT", {}, group_size=len(group["members"]) + 1)
            repo.decline_seat(group["group_id"], offer["student_id"])
            out["offers_expired"].append({"group_id": group["group_id"], "student_id": offer["student_id"]})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            out["errors"].append({"group_id": stale["group_id"], "error": f"{type(exc).__name__}: {exc}"})
    return out


def handler(event, context):
    summary = sweep(get_repo(), int(time.time()))
    if "httpMethod" not in event:
        return summary
    return response(500 if summary["errors"] else 200, summary)
