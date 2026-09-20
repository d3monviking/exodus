"""
POST /groups/{id}/respond   body: {"action": "accept"}
                                  {"action": "decline", "reason": "TIME", "payload": {"b": 60}}
                                  {"action": "decline", "reason": "PERSON",
                                   "payload": {"named_student_id": "imt2022103"}}

Accepting locks the member in; the group is CONFIRMED once everyone has.

A decline applies whatever on_decline() returns and sends the decliner back to
the pool. What happens to the rest of the group depends on its size:

  - a group of three is not dissolved: the other two keep the same departure
    time and are asked again (stay as a pair, or split), because consent
    doesn't survive a change to the package. Both staying confirms the pair;
    either splitting is a decline of the pair, which dissolves it.
  - a group of two, or a trio that can't continue as a pair, is dissolved.

The response says which happened, and when the next release is, so the page can
tell the student how long they'll wait.

A confirmed pair may have a third seat on offer (release_orchestrator). The
student it was offered to answers here too: accepting adds them, at the pair's
departure time; declining leaves the pair untouched.
"""

from __future__ import annotations

import time

from cedar_authz import is_permitted
from config import REASONS
from handlers._common import identify, parse_body, response
from handlers._decline import apply_decline
from handlers._groups import confirm_group, resolve_departure
from repo import get_repo
from schedule import next_release_at

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


def _confirm_if_complete(repo, group_id: str, now: int) -> dict:
    group = repo.get_group(group_id)
    responses = group.get("responses") or {}
    if group["state"] == "FORMED" and all(responses.get(m) is True for m in group["members"]):
        group = confirm_group(repo, group_id, now)
    return group


def _validated_decline(body: dict, group: dict, me: str):
    """(reason, payload, error response) for a decline request body."""
    reason = body.get("reason")
    if reason not in CLIENT_REASONS:
        return None, None, response(400, {"error": f"reason must be one of {CLIENT_REASONS}"})
    payload, err = _clean_payload(body.get("payload") or {}, group, me)
    if err:
        return None, None, response(400, {"error": err})
    return reason, payload, None


def _respond_to_seat(repo, who: dict, group: dict, body: dict, now: int):
    """The student a confirmed pair's third seat was offered to."""
    offer = group["seat_offer"]
    if now > offer["deadline"]:
        return response(409, {"error": "this offer has expired"})

    action = body.get("action")
    if action == "accept":
        repo.accept_seat(group["group_id"], who["id"])
        return response(200, {"state": "CONFIRMED", "accepted": len(group["members"]) + 1,
                              "of": len(group["members"]) + 1})

    if action == "decline":
        reason, payload, err = _validated_decline(body, group, who["id"])
        if err:
            return err
        apply_decline(repo, group, who["id"], reason, payload, group_size=len(group["members"]) + 1)
        repo.decline_seat(group["group_id"], who["id"])
        # the pair is untouched: only this offer ends
        return response(200, {"state": "OFFER_DECLINED", "reason": reason,
                              "next_release_at": next_release_at(repo, now)})

    return response(400, {"error": "action must be 'accept' or 'decline'"})


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

    now = int(time.time())
    # A student offered a seat is a prospective member: Cedar may let them answer.
    offer = group.get("seat_offer")
    invitee = bool(offer) and offer["student_id"] == who["id"]
    members = group["members"] + ([who["id"]] if invitee else [])
    resource = {"type": "Group", "id": group_id, "members": members, "state": group["state"]}
    if not is_permitted(who, "RespondToProposal", resource):
        return response(403, {"error": "only members of a group may respond to it"})
    if invitee:
        return _respond_to_seat(repo, who, group, body, now)

    # Cedar says who may respond; whether the group is still answerable is a state question.
    if group["state"] != "FORMED":
        return response(409, {"error": f"this group is {group['state']}"})
    if now > group["accept_deadline"]:
        return response(409, {"error": "the deadline for this proposal has passed"})

    action = body.get("action")
    mine = (group.get("responses") or {}).get(who["id"])

    if action == "accept":
        if mine is not True:
            repo.record_response(group_id, who["id"], True)
        group = _confirm_if_complete(repo, group_id, now)
        accepted = sum(1 for m in group["members"] if (group.get("responses") or {}).get(m) is True)
        return response(200, {"state": group["state"], "accepted": accepted, "of": len(group["members"])})

    if action == "decline":
        if mine is True:
            return response(409, {"error": "you already accepted; accepting locks you in"})
        reason, payload, err = _validated_decline(body, group, who["id"])
        if err:
            return err

        apply_decline(repo, group, who["id"], reason, payload)
        repo.record_response(group_id, who["id"], False)
        outcome = resolve_departure(repo, group, [who["id"]], now, reason=reason, declined_by=who["id"])
        return response(200, {"state": outcome, "reason": reason, "next_release_at": next_release_at(repo, now)})

    return response(400, {"error": "action must be 'accept' or 'decline'"})
