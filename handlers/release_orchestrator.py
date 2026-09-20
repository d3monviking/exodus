"""
Fires a release: for each route, solve the whole open pool at once and turn
the result into FORMED groups. Triggered by the timer loop over
POST /internal/release (or by an EventBridge event when deployed to AWS).

Decline budget: a student who has used it up sits out this release (SAT_OUT)
and is revived, with a fresh budget, at the start of the next. That guarantees
a re-proposed, re-declined group can't loop forever.

Open seats: a confirmed pair whose cab leaves late enough may still gain a
third rider. Before the pool is solved, each such seat is offered to the pending
student who fits it best. The pair's departure time is locked, so a candidate
fits only if that exact time is inside their own window.
"""

from __future__ import annotations

import sys
import time
import traceback
import uuid

from cedar_authz import is_permitted
from config import CONFIG, ROUTES
from explainer import explain
from handlers._common import response
from lifecycle import should_sit_out
from repo import get_repo
from schedule import seat_fillable
from solver import penalty, solve

ORCHESTRATOR = {"type": "Service", "id": "orchestrator"}
RUN_ROUTE = "ALL"


def _has_blocked_pair(member_ids: list[str], by_id: dict[str, dict]) -> bool:
    return any(o in by_id[m].get("blocked_with", []) for m in member_ids for o in member_ids if o != m)


def _stats_from_formed(pool_size: int, formed: list[dict]) -> dict:
    """Stats recomputed when the solver's own can't be used (a Cedar rejection).

    total_cost must mean what solve() means by it — the whole objective,
    upsilon per ungrouped student included — or the Releases table would hold
    two different measures in one column.
    """
    grouped = sum(len(g["members"]) for g in formed)
    ungrouped = pool_size - grouped
    return {
        "pool_size": pool_size,
        "groups_of_3": sum(1 for g in formed if len(g["members"]) == 3),
        "groups_of_2": sum(1 for g in formed if len(g["members"]) == 2),
        "ungrouped": ungrouped,
        "cabs_saved": grouped - len(formed),
        "total_cost": round(sum(g["cost"] for g in formed) + CONFIG["upsilon"] * ungrouped, 4),
    }


def fill_open_seats(repo, route: str, pool: list[dict], now: int) -> tuple[list[dict], list[dict]]:
    """Offer each open third seat to the best-fitting pending student.

    Returns (offers made, the pool that is left to solve). Seats go first: a cab
    already agreed for two people is completed rather than left for the solver
    to route around. The smallest penalty wins each seat, ties broken by the
    earlier departure and then by id, and nobody is offered two seats. The
    student must still accept, and can decline like any proposal.

    Heuristic, not optimal: a student who takes a seat is unavailable to the
    solver even if a better group existed for them.
    """
    fits = []
    for g in repo.open_seat_groups(route):
        T = g["departure_time"]
        if not seat_fillable(T, now):
            continue
        for c in pool:
            if c["student_id"] in g.get("excluded", []) or set(c.get("blocked_with", [])) & set(g["members"]):
                continue
            if not c["p"] - c["b"] <= T <= c["p"] + c["a"]:
                continue
            pen = penalty(c, T)
            if pen <= CONFIG["p_cap"] + 1e-9:
                fits.append((round(pen, 6), T, g["group_id"], c["student_id"], g))
    fits.sort(key=lambda f: f[:4])

    by_id = {c["student_id"]: c for c in pool}
    offers, taken_groups, taken_students = [], set(), set()
    for pen, T, group_id, student_id, g in fits:
        if group_id in taken_groups or student_id in taken_students:
            continue
        members = [repo.get_request(m) for m in g["members"]]
        candidate = by_id[student_id]
        permitted = is_permitted(
            ORCHESTRATOR, "FormGroup",
            {"type": "Group", "id": group_id, "members": [*g["members"], student_id], "state": "FORMED",
             "contains_blocked_pair": bool(set(candidate.get("blocked_with", [])) & set(g["members"]))})
        if not permitted or any(m is None for m in members):
            continue
        explanation = explain(candidate, {**g, "members": [*g["members"], student_id]}, members, CONFIG)
        repo.offer_seat(group_id, student_id, now + CONFIG["accept_window_minutes"] * 60, explanation)
        taken_groups.add(group_id)
        taken_students.add(student_id)
        offers.append({"group_id": group_id, "student_id": student_id, "departure_time": T, "penalty": pen})
    return offers, [c for c in pool if c["student_id"] not in taken_students]


def release_route(repo, route: str, now: int) -> dict:
    # Order matters: revive first, so anyone marked SAT_OUT below sits out exactly this release.
    revived = repo.revive_sat_out(route)
    pool = repo.open_pool(route)
    sat_out = [r["student_id"] for r in pool if should_sit_out(r, CONFIG)]
    for sid in sat_out:
        repo.set_status(sid, "SAT_OUT")
    pool = [r for r in pool if r["student_id"] not in sat_out]
    offers, pool = fill_open_seats(repo, route, pool, now)
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
        record["explanations"] = {
            m: explain(by_id[m], record, [by_id[o] for o in members if o != m], CONFIG) for m in members
        }
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
                   "groups_rejected": rejected, "sat_out": len(sat_out), "seats_offered": len(offers), **stats}
        repo.put_release(release)
        try:
            audit_key = repo.append_release_log(release, formed)
        except Exception:
            # groups are already persisted; a failed audit line must not undo the release
            traceback.print_exc(file=sys.stderr)

    return {"status": "released", "stats": stats, "groups_rejected": rejected,
            "sat_out": sat_out, "revived": revived, "seats": offers,
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
