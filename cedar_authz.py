"""
The only place in the codebase that talks to Cedar. Everything else calls
is_permitted(); swapping the engine (bindings -> CLI) changes this file only.

principal: {"id": "imt2022001", "email": "imt2022001@iiitb.ac.in"}
           (optional "type", default "Student"; use "Service" for system callers)
resource:  {"type": "Request" | "Group", "id": "...",
            "members": [student ids], "state": "FORMED", "contains_blocked_pair": False}
           (members/state/contains_blocked_pair only needed for Group resources)

Fails closed: if Cedar reports any evaluation error, the answer is deny. A
forbid policy that errors is skipped by Cedar, which would otherwise silently
turn a deny into an allow.
"""

from __future__ import annotations

from pathlib import Path

from cedarpy import PolicySet, is_authorized

_POLICIES = PolicySet.from_str((Path(__file__).parent / "policies.cedar").read_text())


def _entities(principal: dict, resource: dict) -> list[dict]:
    p_type = principal.get("type", "Student")
    p_attrs = {"email": principal["email"]} if "email" in principal else {}

    r_attrs: dict = {}
    if "members" in resource:
        r_attrs["members"] = [
            {"__entity": {"type": "Student", "id": m}} for m in resource["members"]
        ]
    if "state" in resource:
        r_attrs["state"] = resource["state"]
    if "contains_blocked_pair" in resource:
        r_attrs["contains_blocked_pair"] = bool(resource["contains_blocked_pair"])

    return [
        {"uid": {"type": p_type, "id": principal["id"]}, "attrs": p_attrs, "parents": []},
        {"uid": {"type": resource["type"], "id": resource["id"]}, "attrs": r_attrs, "parents": []},
    ]


def is_permitted(principal: dict, action: str, resource: dict) -> bool:
    result = is_authorized(
        request={
            "principal": {"type": principal.get("type", "Student"), "id": principal["id"]},
            "action": {"type": "Action", "id": action},
            "resource": {"type": resource["type"], "id": resource["id"]},
        },
        policies=_POLICIES,
        entities=_entities(principal, resource),
    )
    if result.diagnostics.errors:
        return False
    return bool(result.allowed)
