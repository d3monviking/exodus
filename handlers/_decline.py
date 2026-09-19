"""
The one path every decline takes, real or by timeout. respond and
lifecycle_sweep both call apply_decline(), so the decline budget sees
silence exactly as it sees an explicit "no".

B never interprets a reason here: on_decline() returns a delta and the repo's
dispatcher persists whichever keys are present.
"""

from __future__ import annotations

import json

from lifecycle import on_decline


def apply_decline(repo, group: dict, student_id: str, reason: str, client_payload: dict) -> dict:
    request = repo.get_request(student_id)
    if request is None:  # already withdrawn; nothing to update
        return {}

    # Group facts are set by the server, after the client's payload, so a client
    # can't forge the departure time or size that a soft anchor would record.
    payload = {**client_payload, "departure_time": group["departure_time"], "group_size": len(group["members"])}

    delta = on_decline(request, reason, payload)
    # Half of Gate 2 failures are a delta A returned correctly that B didn't write; keep it visible.
    print("decline delta", json.dumps({"student": student_id, "group": group["group_id"],
                                       "reason": reason, "delta": delta}))
    repo.apply_decline_delta(student_id, delta)
    return delta
