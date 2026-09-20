"""
POST /groups/{id}/respond   body: {"action": "accept"}
                                  {"action": "decline", "reason": "TIME", "payload": {"b": 60}}
                                  {"action": "decline", "reason": "PERSON",
                                   "payload": {"named_student_id": "imt2022103"}}

Accepting locks the member in; the group is CONFIRMED once everyone has. A
decline dissolves the whole group (consent doesn't survive a change to the
package), applies whatever on_decline() returns, and returns everyone to the pool.
"""

from __future__ import annotations

import time

from cedar_authz import is_permitted
from config import REASONS
from handlers._common import identify, parse_body, response
from handlers._decline import apply_decline
from repo import get_repo

# TIMEOUT is the sweep's reason, never a client's.
CLIENT_REASONS = [r for r in REASONS if r != "TIMEOUT"]
MAX_FLEX_MINUTES = 240


def other_members(group: dict, me: str) -> list[str]:
    return sorted(m for m in group["members"] if m != me)


def _clean_payload(raw: dict, group: dict, me: str) -> tuple[dict | None, str | None]:
    """Whitelist the keys on_decline understands and validate them at the boundary.
    Key-driven, not reason-driven: nothing here branches on the reason string."""
    if not isinstance(raw, dict):
        return None, "payload must be an object"
    payload: dict = {}

    if "named_student_id" in raw:
        # A real id, but only one of this group's other members: blocking is
        # pair-scoped, and nobody may block a stranger they never travelled with.
        others = other_members(group, me)
        named = raw["named_student_id"]
        if named not in others:
            return None, f"named_student_id must be one of {others}"
        payload["named_student_id"] = named

    for key in ("b", "a"):
        if key in raw:
            v = raw[key]
            if not (isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= MAX_FLEX_MINUTES):
                return None, f"{key} must be an integer between 5 and {MAX_FLEX_MINUTES}"
            payload[key] = v
    return payload, None


def _confirm_if_complete(repo, group_id: str) -> dict:
    group = repo.get_group(group_id)
    responses = group.get("responses") or {}
    if group["state"] == "FORMED" and all(responses.get(m) is True for m in group["members"]):
        repo.confirm(group_id)
        group = repo.get_group(group_id)
    return group


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    group_id = (event.get("pathParameters") or {}).get("id")
    try:
        body = parse_body(event)
    except ValueError:
        return response(400, {"error": "body is not valid JSON"})

    repo = get_repo()
    group = repo.get_group(group_id) if group_id else None
    if group is None:
        return response(404, {"error": "no such group"})

    resource = {"type": "Group", "id": group_id, "members": group["members"], "state": group["state"]}
    if not is_permitted(who, "RespondToProposal", resource):
        return response(403, {"error": "only members of a group may respond to it"})

    # Cedar says who may respond; whether the group is still answerable is a state question.
    if group["state"] != "FORMED":
        return response(409, {"error": f"this group is {group['state']}"})
    if int(time.time()) > group["accept_deadline"]:
        return response(409, {"error": "the deadline for this proposal has passed"})

    action = body.get("action")
    mine = (group.get("responses") or {}).get(who["id"])

    if action == "accept":
        if mine is not True:
            repo.record_response(group_id, who["id"], True)
        group = _confirm_if_complete(repo, group_id)
        accepted = sum(1 for m in group["members"] if (group.get("responses") or {}).get(m) is True)
        return response(200, {"state": group["state"], "accepted": accepted, "of": len(group["members"])})

    if action == "decline":
        if mine is True:
            return response(409, {"error": "you already accepted; accepting locks you in"})
        reason = body.get("reason")
        if reason not in CLIENT_REASONS:
            return response(400, {"error": f"reason must be one of {CLIENT_REASONS}"})
        payload, err = _clean_payload(body.get("payload") or {}, group, who["id"])
        if err:
            return response(400, {"error": err})

        apply_decline(repo, group, who["id"], reason, payload)
        repo.record_response(group_id, who["id"], False)
        repo.dissolve(group_id, reason=reason, declined_by=who["id"])
        return response(200, {"state": "DISSOLVED", "reason": reason})

    return response(400, {"error": "action must be 'accept' or 'decline'"})
