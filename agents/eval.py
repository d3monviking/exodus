"""
Measure the agents: fixed cases, the real model, the same guard as production.

Offline: a poolgen pool solved by the real solver, no stack needed. Every
reply is scored by agents/guarded.py exactly as a student's would be, and
printed in full so a person can check what the guard can't.

  python -m agents.eval explain            # 10 students from the demo pool
  python -m agents.eval advise             # 8 hypothetical requests
  python -m agents.eval explain --n 4      # a quicker run
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from agents import advise as advise_mod
from agents import explain as explain_mod
from agents import guarded
from agents.facts import explain_facts, template
from agents.whatif import advise_facts, advise_template
from config import CONFIG
from poolgen import generate
from solver import solve

ROUTE = "COLLEGE_AIRPORT"
# preferred time (minutes), earlier, later: a spread of easy, lonely and tight cases
ADVISE_CASES = [(825, 15, 15), (1025, 15, 15), (1230, 10, 20), (930, 5, 5),
                (1290, 15, 15), (1080, 30, 30), (870, 20, 10), (1170, 45, 15)]


def explain_cases(n: int) -> list[tuple[str, dict]]:
    """n members of the demo pool's groups, spread across groups and positions."""
    pool = generate(40, ROUTE, seed=3)
    by_id = {r["student_id"]: r for r in pool}
    members = [(m, g) for g in solve(pool, CONFIG)["groups"] for m in g["members"]]
    picked = members[:: max(1, len(members) // n)][:n]
    return [(m, explain_facts(by_id[m], g, [by_id[o] for o in g["members"] if o != m], CONFIG)) for m, g in picked]


def run(kind: str, n: int, timeout: float) -> int:
    pool = generate(40, ROUTE, seed=3)
    if kind == "explain":
        jobs = [(sid, (lambda s=sid, f=facts: explain_mod._ask_model(s, f)), template(facts), True)
                for sid, facts in explain_cases(n)]
    else:
        jobs = []
        for p, b, a in ADVISE_CASES[:n]:
            facts = advise_facts(pool, ROUTE, p, b, a, CONFIG)
            ask = (lambda p=p, b=b, a=a: advise_mod._ask_model(pool, ROUTE, p, b, a, CONFIG, None))
            jobs.append((f"p={p} -{b}/+{a}", ask, advise_template(facts), False))

    results = []
    for label, ask, fallback, group_exists in jobs:
        t = time.monotonic()
        out = guarded.run(ask, fallback, timeout, group_exists=group_exists)
        dt = time.monotonic() - t
        results.append((out["source"] == "agent", dt))
        print(f"--- {label}  [{'PASS' if out['source'] == 'agent' else 'FAIL'}, {dt:.0f}s]"
              + (f"  {out['rejected']}" if out["rejected"] else ""))
        print(f"    {(out.get('reply') if out['rejected'] else out['text']) or '(no reply)'}".replace("\n", " "))
        sys.stdout.flush()

    ok = sum(p for p, _ in results)
    times = [t for _, t in results]
    print(f"\n{kind}: {ok}/{len(results)} passed the guard; median {statistics.median(times):.0f}s, "
          f"max {max(times):.0f}s  (model {guarded.MODEL_ID})")
    print("Passing is necessary, not sufficient: read the passing replies too.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["explain", "advise"])
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args(argv)
    return run(args.kind, args.n or (10 if args.kind == "explain" else len(ADVISE_CASES)), args.timeout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
