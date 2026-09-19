"""
The grouping algorithm: partition one route's open pool into cabs of up to
three so that total inconvenience is minimal and as few people as possible
travel alone. Architecture doc section 6; the contract is contracts.md.

Pure, deterministic, stdlib only, no I/O. Everything the solver needs arrives
as an argument, including `blocked_with`, which the platform denormalises onto
each request before calling.

The model, in one place:

    penalty(i, T)  = (p - T) / b  early,  (T - p) / a  late,  0 on time
                     the fraction of i's own declared flexibility spent

    cost(G, T)     = sum penalty + beta * max penalty + |G| * s(|G|)
                     s(3) = 0, s(2) = sigma; an ungrouped person costs upsilon

What is exact and what is not (section 6.6): the departure time is exactly
optimal on the grid, and for any one ordering of the pool the DP finds the
best partition into runs contiguous in that order. Contiguity in preferred
time is lossless when flexibility is uniform, but real windows are lopsided
(a flight-bound student leaves 40 minutes early, never 5 late), and then a
student whose window reaches far past their neighbours' can only be grouped
by skipping over someone. So the DP runs under several orderings, each a
different guess at where a window really sits, and keeps the cheapest.

Measured against brute force on 600 small pools with widely varying windows:
preferred-time order alone missed the true optimum on 15% of them, the four
orderings together on 2%, with the worst miss shrinking from 3.49 to 0.70.
With uniform flexibility it matched the true optimum every time.
"""

from __future__ import annotations

INF = float("inf")

# Two costs this close are a tie. Ties then go to the option tried first, so
# the answer never depends on floating-point noise in the last bit.
EPS = 1e-9

# Orderings the DP runs under. Preferred time first, so on a tie the answer
# is the one section 6.5 describes.
ORDERINGS = (
    lambda r: (r["p"], r["student_id"]),
    lambda r: (2 * r["p"] + r["a"] - r["b"], r["student_id"]),  # window midpoint
    lambda r: (r["p"] - r["b"], r["student_id"]),                # window start
    lambda r: (r["p"] + r["a"], r["student_id"]),                # window end
)


def penalty(r: dict, T: int) -> float:
    if T < r["p"]:
        return (r["p"] - T) / r["b"]
    if T > r["p"]:
        return (T - r["p"]) / r["a"]
    return 0.0


def _allowed_together(members: list[dict]) -> bool:
    """Hard constraints that don't depend on the departure time."""
    size = len(members)
    if any(r["min_group_size"] > size for r in members):
        return False
    ids = {r["student_id"] for r in members}
    return not any(ids & set(r.get("blocked_with", ())) for r in members)


def group_cost(members: list[dict], config: dict) -> tuple[float, int | None, dict[str, float] | None]:
    """Best (cost, departure_time, penalties) for this exact group, or (INF, None, None).

    Scans every grid point in the shared window rather than only the members'
    preferred times: with beta > 0 the optimum can sit where two members'
    penalty lines cross (section 6.4). A window is at most a few dozen grid
    points, so this is cheap and exact.
    """
    if not _allowed_together(members):
        return INF, None, None

    grid = config["grid_minutes"]
    lo = max(r["p"] - r["b"] for r in members)
    hi = min(r["p"] + r["a"] for r in members)
    first = -(-lo // grid) * grid  # ceil to grid
    last = hi // grid * grid       # floor to grid
    share = config["sigma"] if len(members) == 2 else 0.0

    best, best_T, best_pens = INF, None, None
    for T in range(first, last + 1, grid):
        pens = [penalty(r, T) for r in members]
        worst = max(pens)
        if worst > config["p_cap"] + EPS:
            continue
        cost = sum(pens) + config["beta"] * worst + len(members) * share
        if cost < best - EPS:  # earliest T wins a tie
            best, best_T, best_pens = cost, T, pens

    if best_T is None:
        return INF, None, None
    return best, best_T, {r["student_id"]: p for r, p in zip(members, best_pens)}


def _partition(R: list[dict], config: dict) -> tuple[float, list[dict], list[dict]]:
    """Exact best partition of R into runs contiguous in R's order.

    Returns (total cost, groups, ungrouped requests). Groups carry raw floats;
    solve() rounds them once, at the edge.
    """
    n = len(R)
    upsilon = config["upsilon"]
    sizes = range(config["max_group"], 1, -1)  # biggest first, so a tie keeps the bigger group

    # dp[i]: least total cost of handling R[:i]. back[i]: how R[i-1] got handled.
    dp = [0.0] + [INF] * n
    back: list[tuple | None] = [None] * (n + 1)
    for i in range(1, n + 1):
        for k in sizes:
            if k > i:
                continue
            c, T, pens = group_cost(R[i - k : i], config)
            if c < INF and dp[i - k] + c < dp[i] - EPS:
                dp[i], back[i] = dp[i - k] + c, (k, T, pens, c)
        if dp[i - 1] + upsilon < dp[i] - EPS:
            dp[i], back[i] = dp[i - 1] + upsilon, (1, None, None, upsilon)

    groups, ungrouped = [], []
    i = n
    while i > 0:
        k, T, pens, c = back[i]
        if k == 1:
            ungrouped.append(R[i - 1])
        else:
            groups.append({"members": R[i - k : i], "departure_time": T, "penalties": pens, "cost": c})
        i -= k
    return dp[n], groups, ungrouped


def solve(requests: list[dict], config: dict) -> dict:
    """Partition the pool. See contracts.md for the exact return shape."""
    ids = [r["student_id"] for r in requests]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate student_id in pool")

    best = None
    for key in ORDERINGS:
        attempt = _partition(sorted(requests, key=key), config)
        if best is None or attempt[0] < best[0] - EPS:  # earlier ordering wins a tie
            best = attempt
    total, raw_groups, raw_ungrouped = best

    # One canonical output order, whichever ordering won.
    by_time = ORDERINGS[0]
    groups = []
    for g in sorted(raw_groups, key=lambda g: (g["departure_time"], min(map(by_time, g["members"])))):
        members = sorted(g["members"], key=by_time)
        groups.append({
            "members": [r["student_id"] for r in members],
            "departure_time": g["departure_time"],
            "penalties": {r["student_id"]: round(g["penalties"][r["student_id"]], 4) for r in members},
            "cost": round(g["cost"], 4),
        })
    ungrouped = [r["student_id"] for r in sorted(raw_ungrouped, key=by_time)]

    grouped = sum(len(g["members"]) for g in groups)
    stats = {
        "pool_size": len(requests),
        "groups_of_3": sum(1 for g in groups if len(g["members"]) == 3),
        "groups_of_2": sum(1 for g in groups if len(g["members"]) == 2),
        "ungrouped": len(ungrouped),
        "cabs_saved": grouped - len(groups),
        # the whole objective, upsilon for each ungrouped person included
        "total_cost": round(total, 4),
    }
    return {"groups": groups, "ungrouped": ungrouped, "stats": stats}
