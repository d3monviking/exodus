"""
What happens to a group when a member leaves, and when it is confirmed. respond
and lifecycle_sweep both go through here, so a decline and a timeout are treated
alike.

A group of three that loses one member is not dissolved. The other two keep the
same departure time and are asked again: stay together as a pair, or split. If
both stay, the pair is confirmed and its departure time is locked; if either
splits, the group dissolves exactly as it always did, and the splitter's reason
still goes through on_decline, so a decline can never be free.

A confirmed pair may then gain a third rider at a later release (see
release_orchestrator.fill_open_seats) if it leaves late enough for that.
"""

from __future__ import annotations

from config import CONFIG
from explainer import explain
from schedule import next_release_at, seat_may_open

DISSOLVED = "DISSOLVED"
REDUCED = "REDUCED"


def _pair_can_stay(requests: list[dict | None]) -> bool:
    """Can these two travel as a pair, at the time the trio had?

    Not if either has withdrawn, only takes full cabs, or is blocked from the other.
    """
    if len(requests) != 2 or any(r is None for r in requests):
        return False
    a, b = requests
    return (a["min_group_size"] <= 2 and b["min_group_size"] <= 2
            and b["student_id"] not in a.get("blocked_with", []))


def resolve_departure(repo, group: dict, leavers: list[str], now: int,
                      reason: str | None = None, declined_by: str | None = None) -> str:
    """Members have left `group`: keep it going as a pair if that works, else dissolve it.

    Returns REDUCED or DISSOLVED.
    """
    remaining = [m for m in group["members"] if m not in leavers]
    if len(group["members"]) == CONFIG["max_group"] and len(leavers) == 1 and len(remaining) == 2:
        requests = [repo.get_request(m) for m in remaining]
        if _pair_can_stay(requests):
            pair = {**group, "members": remaining}
            explanations = {
                r["student_id"]: explain(r, pair, [o for o in requests if o is not r], CONFIG) for r in requests}
            repo.reduce_group(group["group_id"], leaver=leavers[0],
                              accept_deadline=now + CONFIG["accept_window_minutes"] * 60,
                              explanations=explanations)
            return REDUCED
    repo.dissolve(group["group_id"], reason=reason, declined_by=declined_by)
    return DISSOLVED


def confirm_group(repo, group_id: str, now: int) -> dict:
    """Confirm a group, and decide whether a confirmed pair keeps looking for a third rider.

    The seat stays open only if the cab leaves late enough after the next
    release. Its departure time never changes either way.
    """
    repo.confirm(group_id)
    group = repo.get_group(group_id)
    if len(group["members"]) == 2:
        at = next_release_at(repo, now) or now
        repo.update_group(group_id, {"open_seat": seat_may_open(group["departure_time"], at)})
        group = repo.get_group(group_id)
    return group
