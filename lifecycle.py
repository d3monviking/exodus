"""
Decline decisions: what a decline changes, and when a student sits a release
out. Architecture doc sections 7.3-7.5; the contract is contracts.md.

Pure, like the solver. on_decline() returns a state delta and the platform
persists it verbatim; nothing here touches storage, and nothing on the
platform side interprets a reason.

The termination argument, which everything below exists to keep true:
a decline must change the pool or cost the decliner budget. A decline that
only *looks* like a change (a "widening" to the same window, "too few" from
someone who already only accepts full cabs, blocking someone already blocked)
must cost budget, or that student can re-decline the same group forever and
the release loop never ends. So whether something changed is decided by
comparing against the request, never by which reason was given.

Delta shape: only the keys that apply are present, and a present key always
means "do this". The platform dispatches on key presence, so a key carrying
False or None would still be acted on: `"withdraw": False` would delete the
request. increment_decline_count is the integer to add, not a flag.
"""

from __future__ import annotations

from config import CONFIG, REASONS


def on_decline(request: dict, reason: str, payload: dict) -> dict:
    """The state delta for one student declining one group.

    payload, as the platform builds it (contracts.md):
      named_student_id  PERSON: the member being blocked, resolved from a handle
      b, a              TIME: the window the student offers instead, if any
      departure_time    the declined group's time, set by the platform only
      group_size        the declined group's size, set by the platform only
    """
    if reason not in REASONS:
        raise ValueError(f"unknown reason: {reason}")
    payload = payload or {}
    delta: dict = {}

    if reason == "PERSON":
        named = payload.get("named_student_id")
        if named and named != request["student_id"] and named not in request.get("blocked_with", ()):
            delta["block_pair"] = [request["student_id"], named]

    elif reason == "TIME":
        # A window only ever widens: a smaller number offered on one side is ignored.
        b = max(request["b"], payload.get("b") or 0)
        a = max(request["a"], payload.get("a") or 0)
        if (b, a) != (request["b"], request["a"]):
            delta["update_request"] = {"b": b, "a": a}
        # Widened or not, they turned down this time at this size; the soft
        # anchor (section 7.5) remembers it without forbidding it.
        if payload.get("departure_time") is not None and payload.get("group_size") is not None:
            delta["add_anchor"] = {"T": payload["departure_time"], "size": payload["group_size"]}

    elif reason == "TOO_FEW":
        if request.get("min_group_size", 2) < CONFIG["max_group"]:
            delta["set_min_group_size"] = CONFIG["max_group"]

    elif reason == "PLANS_CHANGED":
        delta["withdraw"] = True

    # TIMEOUT: silence changes nothing on its own; it only costs budget.

    # An anchor is soft, so on its own it doesn't count as a change.
    if not delta.keys() & {"block_pair", "update_request", "set_min_group_size", "withdraw"}:
        delta["increment_decline_count"] = 1
    return delta


def should_sit_out(request: dict, config: dict) -> bool:
    """Decline budget spent: sit out the next release (the platform then revives
    the student with a fresh budget for the one after)."""
    return request.get("decline_count", 0) >= config["decline_budget"]
