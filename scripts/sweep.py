#!/usr/bin/env python3
"""
Tune the solver's knobs and produce the demo's numbers. Offline: poolgen pools
straight into solve(), no stack needed. Stdlib only.

  scripts/sweep.py                                   # upsilon sweep on the demo pool
  scripts/sweep.py --param sigma --values 0,0.3,0.8,1.2
  scripts/sweep.py --seeds 3,7,11                    # the same sweep, averaged over pools
  scripts/sweep.py --compare 0.5 2.5                 # the side-by-side for the video
  scripts/sweep.py --compare 0.5 2.5 --all           # ...listing every student, not just the ones who moved
  scripts/sweep.py --as-submitted ...                # the pool as the API stores it, to match a live release

Every knob comes from config.py; a sweep overrides one of them per run and
changes nothing on disk.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG  # noqa: E402
from poolgen import WAVES, generate  # noqa: E402
from solver import solve  # noqa: E402


def clock(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{(h - 1) % 12 + 1}:{m:02d}{'am' if h < 12 else 'pm'}"


def metrics(out: dict) -> dict:
    s = out["stats"]
    pens = [p for g in out["groups"] for p in g["penalties"].values()]
    return {
        "triples": s["groups_of_3"],
        "pairs": s["groups_of_2"],
        "alone": s["ungrouped"],
        "cabs": s["groups_of_3"] + s["groups_of_2"] + s["ungrouped"],
        "saved": s["cabs_saved"],
        "mean_pen": statistics.mean(pens) if pens else 0.0,
        "worst_pen": max(pens, default=0.0),
        "cost": s["total_cost"],
    }


# (metric, header, width, decimals); counts get one decimal when averaged over pools
COLUMNS = [("triples", "3s", 6, 0), ("pairs", "2s", 6, 0), ("alone", "alone", 7, 0), ("cabs", "cabs", 7, 0),
           ("saved", "saved", 7, 0), ("mean_pen", "mean pen", 10, 2), ("worst_pen", "worst pen", 11, 2),
           ("cost", "cost", 8, 2)]


def sweep(pools: list[list[dict]], param: str, values: list[float]) -> None:
    n = len(pools[0])
    avg = len(pools) > 1
    print(f"{param} sweep over {len(pools)} pool{'s' if avg else ''} of {n}"
          f"{' (averages)' if avg else ''}; everything else from config.py\n")
    print(f"{param:>8}" + "".join(f"{h:>{w}}" for _, h, w, _ in COLUMNS))
    for v in values:
        runs = [metrics(solve(p, {**CONFIG, param: v})) for p in pools]
        row = {k: statistics.mean(r[k] for r in runs) for k in runs[0]}
        cells = "".join(f"{row[k]:>{w}.{d or (1 if avg else 0)}f}" for k, _, w, d in COLUMNS)
        mark = "  <- config.py" if v == CONFIG[param] else ""
        print(f"{v:>8g}{cells}{mark}")
    print("\npen = fraction of their own flexibility a grouped student spends (0 on time, 1 at the edge)")


def outcome_by_student(out: dict) -> dict[str, tuple[str, int | None, int]]:
    """student -> (group label, departure time, group size); labels by departure order."""
    res = {}
    for i, g in enumerate(out["groups"]):
        for m in g["members"]:
            res[m] = (chr(ord("A") + i) if i < 26 else f"G{i}", g["departure_time"], len(g["members"]))
    for m in out["ungrouped"]:
        res[m] = ("-", None, 1)
    return res


def compare(pool: list[dict], param: str, lo: float, hi: float, show_all: bool) -> None:
    runs = [(v, solve(pool, {**CONFIG, param: v})) for v in (lo, hi)]
    outcomes = [outcome_by_student(out) for _, out in runs]

    def cell(o):
        label, T, size = o
        return "alone" if T is None else f"{label} {clock(T)} ({size})"

    def moved(r):
        a, b = (o[r["student_id"]] for o in outcomes)
        return (a[1], a[2]) != (b[1], b[2])

    rows = [r for r in sorted(pool, key=lambda r: (r["p"], r["student_id"])) if show_all or moved(r)]
    head = [f"{param} = {v:g}" for v, _ in runs]
    print(f"Same {len(pool)} students, solved twice. Only {param} changes.\n")
    print(f"{'student':<12}{'wants':>8}  {'window':<16}{head[0]:<20}{head[1]:<20}")
    for r in rows:
        window = f"-{r['b']}/+{r['a']} min"
        print(f"{r['student_id']:<12}{clock(r['p']):>8}  {window:<16}"
              f"{cell(outcomes[0][r['student_id']]):<20}{cell(outcomes[1][r['student_id']]):<20}"
              f"{'' if not show_all or not moved(r) else '*'}")
    if not show_all:
        print(f"({len(rows)} of {len(pool)} students end up differently; --all lists everyone)")

    print()
    for v, out in runs:
        m = metrics(out)
        print(f"{param} = {v:<5g} {len(pool)} students -> {m['cabs']} cabs  "
              f"({m['triples']} of three, {m['pairs']} pair{'' if m['pairs'] == 1 else 's'}, {m['alone']} alone)   "
              f"mean pen {m['mean_pen']:.2f}, worst {m['worst_pen']:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--param", default="upsilon", choices=["upsilon", "sigma", "beta", "p_cap"])
    ap.add_argument("--values", default="0.5,0.8,1,1.5,2.5,5,10", help="comma-separated values to sweep")
    ap.add_argument("--compare", nargs=2, type=float, metavar=("LOW", "HIGH"), help="side-by-side of two values")
    ap.add_argument("--all", action="store_true", help="with --compare, list every student")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--route", default="COLLEGE_AIRPORT", choices=sorted(WAVES))
    ap.add_argument("--seeds", default="3", help="comma-separated poolgen seeds; several are averaged")
    ap.add_argument("--blocks", type=int, default=0)
    ap.add_argument("--as-submitted", action="store_true",
                    help="min_group_size 2 for everyone, as POST /requests stores it, so the numbers match a live release")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    pools = [generate(args.n, args.route, s, blocks=args.blocks) for s in seeds]
    if args.as_submitted:
        pools = [[{**r, "min_group_size": 2} for r in p] for p in pools]
    if args.compare:
        if len(pools) > 1:
            ap.error("--compare takes a single seed")
        compare(pools[0], args.param, *args.compare, show_all=args.all)
    else:
        sweep(pools, args.param, [float(v) for v in args.values.split(",")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
