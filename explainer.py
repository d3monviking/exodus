"""
Why a student got the group they got, in words they can weigh up.

Pure, stdlib only, like the solver: the request, the group and the other
members' requests come in as arguments. Every number is the solver's own
arithmetic, so an explanation is exact and instant.

Relative minutes only, never clock times: minutes-since-midnight becomes a
clock time once, at the frontend edge (contracts.md), so nothing here says
"5:10 pm". Other members stay anonymous until the cab is confirmed, so they
appear only as anonymous shifts, never by id.

    explain(request, group, others, CONFIG) -> the sentence for this student
    explain_facts(...)                      -> the same, structured
"""

from __future__ import annotations

from solver import penalty


def shift(request: dict, T: int) -> dict:
    """How far `T` moves this student, and what share of their flexibility that spends."""
    delta = T - request["p"]
    return {
        "minutes": abs(delta),
        "direction": "later" if delta > 0 else "earlier" if delta < 0 else "on time",
        "flex_used_pct": round(100 * penalty(request, T)),
    }


def _why_not_preferred(me: dict, others: list[dict], group: dict, config: dict) -> dict | None:
    """Why the group isn't leaving at this student's own preferred time."""
    T = group["departure_time"]
    grid = config["grid_minutes"]
    mine = me["p"] // grid * grid  # the nearest grid time at or before what they asked for
    if mine == T:
        return None
    blocking = [o for o in others if not o["p"] - o["b"] <= mine <= o["p"] + o["a"]]
    if blocking:
        return {"reason": "outside_window", "members_affected": len(blocking)}
    over_cap = [o for o in others if penalty(o, mine) > config["p_cap"]]
    if over_cap:
        return {"reason": "over_cap", "members_affected": len(over_cap),
                "cap_pct": round(100 * config["p_cap"])}
    # Feasible at their time, just more total inconvenience for the group.
    return {"reason": "worse_overall"}


def explain_facts(request: dict, group: dict, others: list[dict], config: dict) -> dict:
    """Everything an explanation may say about `request`'s place in `group`.

    `others` are the full requests of the other members. They are used for the
    arithmetic and never appear in the output by id.
    """
    T = group["departure_time"]
    size = len(group["members"])
    return {
        "you": {"minutes_earlier_ok": request["b"], "minutes_later_ok": request["a"], **shift(request, T)},
        "group_size": size,
        "fare_split_ways": size,
        "others": sorted((shift(o, T) for o in others), key=lambda s: (-s["minutes"], s["direction"])),
        "why_not_your_time": _why_not_preferred(request, others, group, config),
    }


def explanation(facts: dict) -> str:
    """The facts as a sentence a student can act on."""
    you, size = facts["you"], facts["group_size"]
    if you["direction"] == "on time":
        timing = "You leave exactly when you asked."
    else:
        side = "delay" if you["direction"] == "later" else "early start"
        timing = (f"You leave {you['minutes']} minutes {you['direction']} than you asked, "
                  f"using {you['flex_used_pct']}% of the {side} you said you could accept.")

    moved = [o for o in facts["others"] if o["direction"] != "on time"]
    if not moved:
        give = "the others leave exactly when they asked"
    else:
        give = ("the others gave something up too: "
                + " and ".join(f"one moves {o['minutes']} minutes {o['direction']}" for o in moved))
    company = f"You share with {size - 1} other{'s' if size > 2 else ''}, splitting the fare {size} ways, and {give}."

    why = facts["why_not_your_time"]
    reason = ""
    if why and why["reason"] == "outside_window":
        reason = " Your exact time is outside another member's window."
    elif why and why["reason"] == "over_cap":
        reason = f" Your exact time would use more than {why['cap_pct']}% of another member's flexibility."
    elif why and why["reason"] == "worse_overall":
        reason = " Your exact time works, but it costs the group more waiting in total."
    return f"{timing} {company}{reason}"


def explain(request: dict, group: dict, others: list[dict], config: dict) -> str:
    """The one call the release path needs."""
    return explanation(explain_facts(request, group, others, config))
