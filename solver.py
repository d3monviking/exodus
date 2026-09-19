"""
STUB solver — placeholder so the platform side can be built and tested end to
end before the real algorithm exists.

Matches the contract exactly (contracts.md): pure, deterministic, stdlib
only, no I/O. The real implementation replaces this file; nothing on the
platform side should need to change when that happens.

Greedy over requests sorted by preferred time: try a triple, then a pair,
else leave the request ungrouped. A group is only formed if it is feasible
(a grid-aligned departure time exists inside every member's window), nobody
is blocked from anyone else in it, and every member accepts that group size.
No cost optimisation.
"""

from __future__ import annotations


def _penalty(r: dict, T: int) -> float:
    if T < r["p"]:
        return (r["p"] - T) / r["b"]
    if T > r["p"]:
        return (T - r["p"]) / r["a"]
    return 0.0


def _try_group(chunk: list[dict], grid: int) -> tuple[int, dict] | None:
    size = len(chunk)
    ids = {r["student_id"] for r in chunk}
    if any(r["min_group_size"] > size or ids & set(r.get("blocked_with", [])) for r in chunk):
        return None

    lo = max(r["p"] - r["b"] for r in chunk)
    hi = min(r["p"] + r["a"] for r in chunk)
    first = -(-lo // grid) * grid  # ceil to grid
    last = (hi // grid) * grid     # floor to grid
    if first > last:
        return None

    target = sorted(r["p"] for r in chunk)[size // 2]
    T = min(max(target - target % grid, first), last)
    return T, {r["student_id"]: round(_penalty(r, T), 4) for r in chunk}


def solve(requests: list[dict], config: dict) -> dict:
    grid = config["grid_minutes"]
    pool = sorted(requests, key=lambda r: (r["p"], r["student_id"]))
    groups: list[dict] = []
    ungrouped: list[str] = []

    i = 0
    while i < len(pool):
        placed = False
        for size in range(min(config["max_group"], len(pool) - i), 1, -1):
            chunk = pool[i : i + size]
            found = _try_group(chunk, grid)
            if found:
                T, penalties = found
                groups.append(
                    {
                        "members": [r["student_id"] for r in chunk],
                        "departure_time": T,
                        "penalties": penalties,
                        "cost": round(sum(penalties.values()), 4),
                    }
                )
                i += size
                placed = True
                break
        if not placed:
            ungrouped.append(pool[i]["student_id"])
            i += 1

    stats = {
        "pool_size": len(requests),
        "groups_of_3": sum(1 for g in groups if len(g["members"]) == 3),
        "groups_of_2": sum(1 for g in groups if len(g["members"]) == 2),
        "ungrouped": len(ungrouped),
        "cabs_saved": sum(len(g["members"]) for g in groups) - len(groups),
        "total_cost": round(sum(g["cost"] for g in groups), 4),
    }
    return {"groups": groups, "ungrouped": ungrouped, "stats": stats}
