"""
STUB lifecycle — hours 0-2 placeholder, same rationale as solver.py.

Pure functions per contracts.md. Implements the reason taxonomy from the
architecture doc section 7.4 and the decline-budget termination backstop
from 7.5.
"""

from __future__ import annotations

VALID_REASONS = {"PERSON", "TIME", "TOO_FEW", "PLANS_CHANGED", "TIMEOUT"}


def on_decline(request: dict, reason: str, payload: dict) -> dict:
    if reason not in VALID_REASONS:
        raise ValueError(f"unknown reason: {reason}")

    delta: dict = {}
    payload = payload or {}

    if reason == "PERSON":
        named = payload.get("named_student_id")
        if named:
            delta["block_pair"] = [request["student_id"], named]

    elif reason == "TIME":
        new_b = payload.get("b")
        new_a = payload.get("a")
        if new_b is not None or new_a is not None:
            delta["update_request"] = {
                "b": new_b if new_b is not None else request["b"],
                "a": new_a if new_a is not None else request["a"],
            }
        else:
            group_size = payload.get("group_size")
            departure_time = payload.get("departure_time")
            if departure_time is not None and group_size is not None:
                delta["add_anchor"] = {"T": departure_time, "size": group_size}

    elif reason == "TOO_FEW":
        delta["set_min_group_size"] = 3

    elif reason == "PLANS_CHANGED":
        delta["withdraw"] = True

    elif reason == "TIMEOUT":
        pass  # silence produces no direct state change on its own

    state_changed = any(
        k in delta for k in ("block_pair", "update_request", "set_min_group_size", "withdraw")
    )
    if not state_changed:
        delta["increment_decline_count"] = 1

    return delta


def should_sit_out(request: dict, config: dict) -> bool:
    return request.get("decline_count", 0) >= config["decline_budget"]
