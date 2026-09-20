"""GET /requests/me — the caller's request, plus their proposal if one exists."""

from __future__ import annotations

from cedar_authz import is_permitted
from config import EMAIL_DOMAIN
from handlers._common import identify, response
from repo import get_repo
from roster import name_for


def _person(student_id: str) -> dict:
    return {"student_id": student_id, "name": name_for(student_id), "email": f"{student_id}@{EMAIL_DOMAIN}"}


def _proposal(group: dict, who: dict) -> dict:
    """The cab as this student sees it.

    Three kinds of viewer: a member of a proposed or confirmed group; a member
    of a trio that lost someone and is being asked whether to carry on as a
    pair (`reduced`, and `left_by` says who); and a student who has been offered
    the third seat of a confirmed pair (`invited`), who sees the cab as it would
    be with them in it.
    """
    members = group["members"]
    offer = group.get("seat_offer")
    invited = bool(offer) and offer["student_id"] == who["id"]
    visible = members + ([who["id"]] if invited else [])
    responses = group.get("responses") or {}
    left_by = group.get("left_by") if group["state"] == "FORMED" else None
    proposal = {
        "group_id": group["group_id"],
        "state": "FORMED" if invited else group["state"],
        "departure_time": group["departure_time"],
        "accept_deadline": offer["deadline"] if invited else group.get("accept_deadline"),
        "size": len(visible),
        "my_response": None if invited else responses.get(who["id"]),
        "accepted": len(members) if invited else sum(1 for m in members if responses.get(m) is True),
        "explanation": offer.get("explanation") if invited else group.get("explanations", {}).get(who["id"]),
        "invited": invited,
        "reduced": bool(group.get("reduced")) and group["state"] == "FORMED",
        "left_by": {"student_id": left_by, "name": name_for(left_by)} if left_by else None,
        "open_seat": bool(group.get("open_seat")) and group["state"] == "CONFIRMED",
        "seat_offered": bool(offer) and not invited,
        "others": [],
    }
    resource = {"type": "Group", "id": group["group_id"], "members": visible, "state": group["state"]}
    # Cedar decides whether this caller may see who else is in the cab. They can:
    # naming someone in a "not with this person" decline needs a name.
    if is_permitted(who, "ViewContactDetails", resource):
        proposal["others"] = [_person(m) for m in sorted(visible) if m != who["id"]]
    return proposal


def _last_outcome(repo, request: dict, me: str) -> dict | None:
    """What happened to the cab this student was last in, if it fell through.

    The reason someone gave is deliberately not exposed: "they didn't want to
    travel with you" is not a thing to put on a screen. Who, and whether it was
    a decline or silence, is enough to explain why the group is gone.
    """
    group = repo.get_group(request["last_group_id"]) if request.get("last_group_id") else None
    if group is None or group["state"] != "DISSOLVED":
        return None
    if me not in group["members"]:  # they left it earlier; its end is none of their business
        return None
    by = group.get("declined_by")
    return {
        "group_id": group["group_id"],
        "departure_time": group["departure_time"],
        "cause": "you_declined" if by == me else ("timeout" if group.get("dissolved_reason") == "TIMEOUT" else "declined"),
        "who": None if by in (None, me) else {"student_id": by, "name": name_for(by)},
    }


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    me = {"student_id": who["id"], "name": name_for(who["id"]), "email": who["email"]}
    repo = get_repo()
    request = repo.get_request(who["id"])
    if request is None:
        return response(200, {"me": me, "request": None, "proposal": None, "last_outcome": None})

    # blocked_with is symmetric, so returning it would reveal who blocked this student
    request.pop("blocked_with", None)

    group = repo.group_for(who["id"])
    proposal = _proposal(group, who) if group and group["state"] in ("FORMED", "CONFIRMED") else None
    last_outcome = None if proposal else _last_outcome(repo, request, who["id"])
    return response(200, {"me": me, "request": request, "proposal": proposal, "last_outcome": last_outcome})
