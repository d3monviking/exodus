"""
Templated stand-in for the `explain` agent (the documented fallback: "a
templated sentence from the solver's numbers"). Swap this call for the
Strands agent later; the orchestrator only depends on the signature.

Deliberately uses relative minutes only. Turning minutes-since-midnight into
a clock time happens once, at the frontend edge, so no "5:10 pm" is built here.
"""

from __future__ import annotations


def explain(request: dict, group: dict) -> str:
    shift = group["departure_time"] - request["p"]
    others = len(group["members"]) - 1
    company = f"You share a cab with {others} other{'s' if others != 1 else ''}, splitting the fare {others + 1} ways."

    if shift == 0:
        timing = "You leave exactly when you asked."
    elif shift > 0:
        used = round(100 * shift / request["a"])
        timing = f"You leave {shift} minutes later than you asked, using {used}% of the delay you said you could accept."
    else:
        used = round(100 * -shift / request["b"])
        timing = f"You leave {-shift} minutes earlier than you asked, using {used}% of the advance you said you could accept."

    return f"{timing} {company}"
