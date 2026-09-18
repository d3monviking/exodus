"""
STUB solver — hours 0-2 placeholder so the platform side can be built and
tested end to end before the real algorithm exists.

Matches the contract exactly (contracts.md): pure, deterministic, stdlib
only, no I/O. Real implementation replaces this file; nothing on the
platform side should need to change when that happens.

This stub does simple contiguous grouping by sorted preferred time with no
cost optimisation — just enough realism that handlers, Cedar checks and the
frontend have believable data to work against.
"""

from __future__ import annotations


def _round_down_to_grid(t: int, grid: int) -> int:
    return t - (t % grid)


def solve(requests: list[dict], config: dict) -> dict:
    grid = config["grid_minutes"]
    max_group = config["max_group"]

    pool = sorted(requests, key=lambda r: r["p"])
    groups: list[dict] = []
    ungrouped: list[str] = []

    i = 0
    n = len(pool)
    while i < n:
        chunk = pool[i : i + max_group]
        if len(chunk) < 2:
            for r in chunk:
                ungrouped.append(r["student_id"])
            i += len(chunk)
            continue

        blocked = False
        ids = {r["student_id"] for r in chunk}
        for r in chunk:
            if ids & set(r.get("blocked_with", [])):
                blocked = True
                break

        if blocked:
            ungrouped.append(chunk[0]["student_id"])
            i += 1
            continue

        mid = chunk[len(chunk) // 2]["p"]
        T = _round_down_to_grid(mid, grid)

        penalties = {}
        for r in chunk:
            if T < r["p"]:
                penalties[r["student_id"]] = min(1.0, (r["p"] - T) / max(r["b"], 1))
            elif T > r["p"]:
                penalties[r["student_id"]] = min(1.0, (T - r["p"]) / max(r["a"], 1))
            else:
                penalties[r["student_id"]] = 0.0

        cost = sum(penalties.values())

        groups.append(
            {
                "members": [r["student_id"] for r in chunk],
                "departure_time": T,
                "penalties": penalties,
                "cost": round(cost, 4),
            }
        )
        i += len(chunk)

    groups_of_3 = sum(1 for g in groups if len(g["members"]) == 3)
    groups_of_2 = sum(1 for g in groups if len(g["members"]) == 2)
    cabs_used = len(groups) + len(ungrouped)
    cabs_without_sharing = len(requests)

    stats = {
        "pool_size": len(requests),
        "groups_of_3": groups_of_3,
        "groups_of_2": groups_of_2,
        "ungrouped": len(ungrouped),
        "cabs_saved": cabs_without_sharing - cabs_used,
        "total_cost": round(sum(g["cost"] for g in groups), 4),
    }

    return {"groups": groups, "ungrouped": ungrouped, "stats": stats}
