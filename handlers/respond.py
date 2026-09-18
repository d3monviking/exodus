"""POST /groups/{id}/respond — accept/decline. (stub)"""

from __future__ import annotations

from handlers._common import parse_body, response


def handler(event, context):
    group_id = event.get("pathParameters", {}).get("id")
    body = parse_body(event)
    # TODO (hours 14-20): Cedar membership check, accept -> promote to CONFIRMED,
    # decline -> lifecycle.on_decline() + repo.apply_decline_delta() + repo.dissolve()
    return response(501, {"error": "not implemented", "group_id": group_id, "received": body})
