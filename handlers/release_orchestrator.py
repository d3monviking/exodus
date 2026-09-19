"""
Fires a release: for each route, solve the whole open pool at once and turn
the result into FORMED groups. Triggered by the timer loop over
POST /internal/release (or by an EventBridge event when deployed to AWS).

Not yet wired: applying the decline-budget sit-out (needs the decline flow,
which lands with POST /groups/{id}/respond).
"""

from __future__ import annotations

import sys
import time
import traceback
import uuid

from cedar_authz import is_permitted
from config import CONFIG, ROUTES
from explain_templated import explain
from handlers._common import response
from repo import get_repo
from solver import solve

ORCHESTRATOR = {"type": "Service", "id": "orchestrator"}
RUN_ROUTE = "ALL"


def _has_blocked_pair(member_ids: list[str], by_id: dict[str, dict]) -> bool:
    return any(o in by_id[m].get("blocked_with", []) for m in member_ids for o in member_ids if o != m)


def _stats_from_formed(pool_size: int, formed: list[dict]) -> dict:
    grouped = sum(len(g["members"]) for g in formed)
    return {
        "pool_size": pool_size,
        "groups_of_3": sum(1 for g in formed if len(g["members"]) == 3),
        "groups_of_2": sum(1 for g in formed if len(g["members"]) == 2),
        "ungrouped": pool_size - grouped,
        "cabs_saved": grouped - len(formed),
        "total_cost": round(sum(g["cost"] for g in formed), 4),
    }


def release_route(repo, route: str, now: int) -> dict:
    pool = repo.open_pool(route)
    by_id = {r["student_id"]: r for r in pool}
    result = solve(pool, CONFIG) if pool else {"groups": [], "ungrouped": [], "stats": None}

    formed, rejected = [], 0
    for g in result["groups"]:
        group_id = uuid.uuid4().hex[:12]
        members = g["members"]
        permitted = is_permitted(
            ORCHESTRATOR,
            "FormGroup",
            {
                "type": "Group",
                "id": group_id,
                "members": members,
                "state": "FORMED",
                "contains_blocked_pair": _has_blocked_pair(members, by_id),
            },
        )
        if not permitted:
            rejected += 1
            continue
        record = {
            "group_id": group_id,
            "route": route,
            "members": members,
            "departure_time": g["departure_time"],
            "state": "FORMED",
            "accept_deadline": now + CONFIG["accept_window_minutes"] * 60,
            "responses": {},
            "penalties": g["penalties"],
            "cost": g["cost"],
        }
        record["explanations"] = {m: explain(by_id[m], record) for m in members}
        repo.put_group(record)
        formed.append(record)

    if result["stats"] is None or rejected:
        stats = _stats_from_formed(len(pool), formed)
    else:
        stats = result["stats"]

    # An idle route writes no row: "latest release" must keep meaning the last
    # time something was actually solved, or the demo's cabs-saved number is
    # overwritten by the next empty tick.
    audit_key = None
    if pool:
        release = {"release_id": f"{route}-{now}-{uuid.uuid4().hex[:6]}", "route": route, "ran_at": now,
                   "groups_rejected": rejected, **stats}
        repo.put_release(release)
        try:
            audit_key = repo.append_release_log(release, formed)
        except Exception:
            # groups are already persisted; a failed audit line must not undo the release
            traceback.print_exc(file=sys.stderr)

    return {"status": "released", "stats": stats, "groups_rejected": rejected,
            "groups": [{"group_id": g["group_id"], "members": g["members"],
                        "departure_time": g["departure_time"]} for g in formed],
            "audit_log": audit_key}


def run_release(repo, now: int | None = None, force: bool = False) -> dict:
    now = int(time.time()) if now is None else now

    # Run-level bookkeeping lives in a pseudo-route "ALL": one row per run, read
    # by the double-fire guard here and by the board's countdown.
    tick = repo.latest_release(RUN_ROUTE)
    if not force and tick and now - tick["ran_at"] < CONFIG["min_release_gap_seconds"]:
        return {r: {"status": "skipped_too_soon", "seconds_since_last": now - tick["ran_at"]} for r in ROUTES}

    out = {}
    for route in ROUTES:
        try:
            out[route] = release_route(repo, route, now)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            out[route] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    ok = [r["stats"] for r in out.values() if r["status"] == "released"]
    repo.put_release({"release_id": f"run-{now}-{uuid.uuid4().hex[:6]}", "route": RUN_ROUTE, "ran_at": now,
                      **{k: sum(s[k] for s in ok) for k in ("pool_size", "groups_of_3", "groups_of_2", "ungrouped", "cabs_saved")},
                      "total_cost": round(sum(s["total_cost"] for s in ok), 4)})
    return out


def handler(event, context):
    force = ((event.get("queryStringParameters") or {}).get("force") or "").lower() in ("1", "true")
    summary = run_release(get_repo(), force=force)
    if "httpMethod" not in event:
        return summary
    failed = any(r["status"] == "error" for r in summary.values())
    return response(500 if failed else 200, summary)
