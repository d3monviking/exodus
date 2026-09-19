"""
The numbers behind an explanation, computed exactly, with no model involved.

The agents only ever phrase what this module computes. That split is the
defence against a small local model's habit of adding plausible detail: the
facts are the solver's own arithmetic, and an agent reply that mentions a
number not in them is thrown away (see agents/guarded.py).

Relative minutes only, never clock times: minutes-since-midnight becomes a
clock time once, at the frontend edge (contracts.md), so nothing here or in an
agent reply says "5:10 pm". Other members stay anonymous until the cab is
confirmed, so they appear only as anonymous shifts, never by id.
"""

from __future__ import annotations

from solver import penalty


def _shift(request: dict, T: int) -> dict:
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

    `others` are the full requests of the other members. They are used for
    the arithmetic and never appear in the output by id.
    """
    T = group["departure_time"]
    size = len(group["members"])
    return {
        "you": {"minutes_earlier_ok": request["b"], "minutes_later_ok": request["a"], **_shift(request, T)},
        "group_size": size,
        "fare_split_ways": size,
        "others": sorted((_shift(o, T) for o in others), key=lambda s: (-s["minutes"], s["direction"])),
        "why_not_your_time": _why_not_preferred(request, others, group, config),
    }


def template(facts: dict) -> str:
    """The explanation without a model: always correct, a little stiff."""
    you, size = facts["you"], facts["group_size"]
    if you["direction"] == "on time":
        timing = "You leave exactly when you asked."
    else:
        side = "delay" if you["direction"] == "later" else "early start"
        timing = (f"You leave {you['minutes']} minutes {you['direction']} than you asked, "
                  f"using {you['flex_used_pct']}% of the {side} you said you could accept.")

    others = [o for o in facts["others"] if o["direction"] != "on time"]
    if not others:
        give = "the others leave exactly when they asked"
    else:
        give = " and ".join(f"one moves {o['minutes']} minutes {o['direction']}" for o in others)
        give = "the others gave something up too: " + give
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


def fact_lines(facts: dict) -> str:
    """The facts as short plain-English statements, one per line: what the
    agent's tool returns. A small model misreads JSON, attaching real numbers
    to the wrong meaning; sentences leave it only the rephrasing to do."""
    you, n = facts["you"], facts["group_size"]
    out = []
    if you["direction"] == "on time":
        out.append("You leave exactly at the time you asked for.")
    else:
        out.append(f"You leave {you['minutes']} minutes {you['direction']} than the time you asked for.")
        out.append(f"That uses {you['flex_used_pct']}% of the flexibility you offered.")
    out.append(f"You share the cab with {n - 1} other student{'s' if n > 2 else ''}, {n} people in total, "
               f"and split the fare {n} ways.")
    others = facts["others"]
    # Two identical lines read to a small model as two more people ("two of them
    # leave on time, while one..."), so identical cab-mates share one line.
    if len(others) == 2 and (others[0]["minutes"], others[0]["direction"]) == (others[1]["minutes"], others[1]["direction"]):
        o = others[0]
        out.append("Both of them leave exactly when they asked." if o["direction"] == "on time"
                   else f"Both of them leave {o['minutes']} minutes {o['direction']} than they asked.")
    else:
        for o in others:
            out.append("One of them leaves exactly when they asked." if o["direction"] == "on time"
                       else f"One of them leaves {o['minutes']} minutes {o['direction']} than they asked.")
    why = facts["why_not_your_time"]
    if why:
        out.append({
            "outside_window": "Your exact time is outside another member's window.",
            "over_cap": f"Your exact time would use more than {why.get('cap_pct')}% of another member's flexibility.",
            "worse_overall": "Your exact time works, but it costs the group more waiting in total.",
        }[why["reason"]])
    return "\n".join(f"- {line}" for line in out)
