"""
Synthetic request pools for tuning and demoing the solver.

Pure and stdlib only, like the solver: `generate()` takes a seed and returns
requests in exactly the contracts.md shape, so a pool goes straight into
`solve()` or through the API with no reshaping. Same arguments, same pool.

What makes a pool look like a real end-of-semester evening rather than noise:

- Preferred times cluster around a few departure waves per route (the evening
  flights, the overnight trains), not spread uniformly across the day.
- Flexibility is uneven. A tight cohort has a fixed deadline at the far end
  (a flight, a train), so it is asymmetric: towards the college a flight-bound
  student will happily leave 40 minutes early but not 10 minutes late, and
  coming back from the airport it flips, since nobody can leave before landing.
  A loose cohort has no deadline and declares wide, roughly symmetric windows.
- A few deliberate outliers sit well away from every wave, so the solver has
  someone it is right to leave alone.

Every time and every tolerance is a multiple of 5, because that is all the
submission form can produce.

    python poolgen.py --n 40 --seed 3              # JSON pool on stdout
    python poolgen.py --n 40 --seed 3 --hist       # eyeball the clustering
"""

from __future__ import annotations

import random

# Departure waves per route: (centre, spread, weight). Minutes since midnight;
# spread is the standard deviation, in minutes.
WAVES = {
    "COLLEGE_AIRPORT": [(930, 25, 0.30), (1050, 30, 0.45), (1200, 25, 0.25)],  # 3:30, 5:30, 8:00 pm
    "COLLEGE_STATION": [(1110, 20, 0.55), (1260, 20, 0.45)],                   # 6:30, 9:00 pm
    "AIRPORT_COLLEGE": [(540, 40, 0.50), (780, 40, 0.50)],                     # 9:00 am, 1:00 pm
    "STATION_COLLEGE": [(390, 20, 0.60), (480, 25, 0.40)],                     # 6:30, 8:00 am
}

# Cohorts: (name, weight, deadline-side range, other-side range). For a trip
# out of college the deadline is lateness (`a`); for a trip back it is
# earliness (`b`). The loose cohort has no deadline, so both ranges match.
COHORTS = [
    ("tight", 0.40, (5, 15), (20, 45)),
    ("medium", 0.35, (15, 30), (15, 30)),
    ("loose", 0.25, (45, 90), (45, 90)),
]

OUTLIER_SHARE = 0.06      # fraction of the pool placed away from every wave
OUTLIER_GAP = 75          # minutes an outlier sits from the nearest wave centre, at least
WANTS_TRIPLE_SHARE = 0.05  # fraction with min_group_size 3
BLOCK_NEAR = 30           # a generated block only joins students this close in preferred time
GRID = 5


def _snap(minutes: float) -> int:
    return min(max(GRID * round(minutes / GRID), 0), 1440 - GRID)


def _tolerance(rng: random.Random, lo: int, hi: int) -> int:
    return GRID * rng.randint(lo // GRID, hi // GRID)


def _outlier_time(rng: random.Random, waves: list[tuple]) -> int:
    centres = [c for c, _, _ in waves]
    while True:
        p = _snap(rng.uniform(min(centres) - 180, max(centres) + 180))
        if all(abs(p - c) >= OUTLIER_GAP for c in centres):
            return p


def generate(
    n: int,
    route: str = "COLLEGE_AIRPORT",
    seed: int = 0,
    *,
    first_id: int = 101,
    blocks: int = 0,
) -> list[dict]:
    """A pool of `n` requests on `route`, in the contracts.md request shape.

    `blocks` adds that many symmetric blocked pairs, each between two students
    who are close in preferred time, since a block between people who would
    never share a cab anyway tests nothing.
    """
    if route not in WAVES:
        raise ValueError(f"unknown route: {route}")
    rng = random.Random(seed)
    waves = WAVES[route]
    outbound = route.startswith("COLLEGE_")
    n_outliers = round(n * OUTLIER_SHARE) if n >= 10 else 0

    pool = []
    for i in range(n):
        if i < n_outliers:
            p = _outlier_time(rng, waves)
        else:
            centre, spread, _ = rng.choices(waves, weights=[w for _, _, w in waves])[0]
            p = _snap(rng.gauss(centre, spread))

        _, _, deadline_side, other_side = rng.choices(COHORTS, weights=[w for _, w, _, _ in COHORTS])[0]
        deadline, other = _tolerance(rng, *deadline_side), _tolerance(rng, *other_side)
        b, a = (other, deadline) if outbound else (deadline, other)

        pool.append({
            "student_id": f"imt2022{first_id + i}",
            "route": route,
            "p": p, "b": b, "a": a,
            "min_group_size": 3 if rng.random() < WANTS_TRIPLE_SHARE else 2,
            "blocked_with": [],
            "declined_anchors": [],
        })

    # Outliers were drawn first; shuffle so their ids are not a giveaway.
    rng.shuffle(pool)
    for i, r in enumerate(pool):
        r["student_id"] = f"imt2022{first_id + i}"

    _add_blocks(rng, pool, blocks)
    return pool


def _add_blocks(rng: random.Random, pool: list[dict], count: int) -> None:
    by_time = sorted(pool, key=lambda r: (r["p"], r["student_id"]))
    neighbours = [(x, y) for i, x in enumerate(by_time) for y in by_time[i + 1 : i + 3]
                  if y["p"] - x["p"] <= BLOCK_NEAR]
    for x, y in rng.sample(neighbours, min(count, len(neighbours))):
        x["blocked_with"].append(y["student_id"])
        y["blocked_with"].append(x["student_id"])


def histogram(pool: list[dict], bucket: int = 15) -> str:
    """Text histogram of preferred times, one row per `bucket` minutes."""
    counts: dict[int, int] = {}
    for r in pool:
        counts[r["p"] // bucket * bucket] = counts.get(r["p"] // bucket * bucket, 0) + 1
    if not counts:
        return ""
    lo, hi = min(counts), max(counts)
    return "\n".join(
        f"{t // 60:02d}:{t % 60:02d}  {'#' * counts.get(t, 0)}" for t in range(lo, hi + bucket, bucket)
    )


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--route", default="COLLEGE_AIRPORT", choices=sorted(WAVES))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--first-id", type=int, default=101)
    ap.add_argument("--blocks", type=int, default=0)
    ap.add_argument("--hist", action="store_true", help="print a histogram of preferred times instead of JSON")
    args = ap.parse_args()

    pool = generate(args.n, args.route, args.seed, first_id=args.first_id, blocks=args.blocks)
    print(histogram(pool) if args.hist else json.dumps(pool, indent=2))
