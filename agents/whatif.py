"""
What-ifs for a request that hasn't been submitted yet, computed with the real
solver. Pure: the pool is an argument, nothing is read or written.

`simulate` answers "if I joined the pool now with this window, what would I
get?" by solving the current pool plus a hypothetical request. It is an
estimate of the next release, not a promise: requests that arrive before it
can change the answer, and the advice says so.

Relative minutes only, like facts.py: the student's preferred time goes in as
minutes since midnight and never comes out as a clock time.
"""

from __future__ import annotations

from agents.facts import _shift
from solver import solve

YOU = "__you__"  # the hypothetical request's id; can't collide with a real student id
WIDEN_TO = (15, 30, 45, 60, 90)  # windows the fallback advice tries, each side at least this wide
DENSITY_SPANS = (30, 60)
RANK = {"alone": 0, 2: 1, 3: 2}  # alone < pair < full cab


def density(pool: list[dict], p: int, student_id: str | None = None) -> dict:
    others = [r for r in pool if r["student_id"] != student_id]
    return {"waiting": len(others),
            **{f"within_{s}": sum(abs(r["p"] - p) <= s for r in others) for s in DENSITY_SPANS}}


def simulate(pool: list[dict], route: str, p: int, b: int, a: int, config: dict,
             student_id: str | None = None) -> dict:
    """Your outcome if you joined this pool now with window (-b, +a)."""
    me = {"student_id": YOU, "route": route, "p": p, "b": b, "a": a,
          "min_group_size": 2, "blocked_with": [], "declined_anchors": []}
    others = [r for r in pool if r["student_id"] != student_id]
    out = solve([*others, me], config)
    mine = next((g for g in out["groups"] if YOU in g["members"]), None)
    base = {"minutes_earlier_ok": b, "minutes_later_ok": a}
    if mine is None:
        return {**base, "outcome": "alone"}
    return {**base, "outcome": "group", "group_size": len(mine["members"]), **_shift(me, mine["departure_time"])}


def _rank(sim: dict) -> tuple:
    """Higher is better: bigger group first, then leaving closer to your preferred time.

    Minutes, never the percentage: widening a window lowers the percentage of
    the very same shift (10 of 30 minutes is 33%, 10 of 45 is 22%), which would
    count as an improvement when nothing about the cab changed."""
    return (RANK["alone"] if sim["outcome"] == "alone" else RANK[sim["group_size"]], -sim.get("minutes", 0))


def sim_line(sim: dict) -> str:
    window = f"With {sim['minutes_earlier_ok']} minutes earlier and {sim['minutes_later_ok']} minutes later allowed"
    if sim["outcome"] == "alone":
        return f"{window}: you would travel alone."
    n = sim["group_size"]
    when = ("leaving exactly when you want" if sim["direction"] == "on time" else
            f"leaving {sim['minutes']} minutes {sim['direction']} than you want, "
            f"which uses {sim['flex_used_pct']}% of your flexibility")
    return f"{window}: you would share a cab with {n - 1} other student{'s' if n > 2 else ''}, {when}."


def density_line(d: dict) -> str:
    return (f"{d['waiting']} students are waiting on this route right now: {d['within_30']} of them want to leave "
            f"within 30 minutes of your time, {d['within_60']} within 60 minutes.")


def advise_facts(pool: list[dict], route: str, p: int, b: int, a: int, config: dict,
                 student_id: str | None = None) -> dict:
    current = simulate(pool, route, p, b, a, config, student_id)
    wider = []
    for w in WIDEN_TO:
        nb, na = max(b, w), max(a, w)
        if (nb, na) != (b, a) and (nb, na) not in [(x["minutes_earlier_ok"], x["minutes_later_ok"]) for x in wider]:
            wider.append(simulate(pool, route, p, nb, na, config, student_id))
    better = next((s for s in wider if _rank(s) > _rank(current)), None)
    return {"density": density(pool, p, student_id), "current": current, "better": better}


def conclusion_line(facts: dict) -> str:
    """The comparison, decided in code: a 3B model can rewrite facts, but it
    can't reliably compare two scenarios."""
    cur, better = facts["current"], facts["better"]
    if better:
        return "Widening your window helps: the wider window gets you a better result than your own."
    if cur["outcome"] == "group" and cur["group_size"] == 3:
        return "Your own window already gets you a full cab, so there is no need to widen it."
    return "Widening your window would not change your result right now."


def advise_template(facts: dict) -> str:
    """The advice without a model: exact, a little stiff."""
    parts = [density_line(facts["density"]), sim_line(facts["current"])]
    cur, better = facts["current"], facts["better"]
    if better:
        parts.append("Widening helps. " + sim_line(better))
    elif cur["outcome"] == "group" and cur["group_size"] == 3:
        parts.append("That's already a full cab.")
    else:
        parts.append(f"Widening to {WIDEN_TO[-1]} minutes each way wouldn't change that.")
    parts.append("This is based on who is waiting right now; requests that arrive before the release can change it.")
    return " ".join(parts)
