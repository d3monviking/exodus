"""
Real Repo implementation, boto3 against LocalStack DynamoDB. Same interface
as FakeRepo in repo.py -- see contracts.md.

The trap this file exists to avoid: boto3 returns every DynamoDB number as
Decimal. If p/b/a/min_group_size reach the solver as Decimal, arithmetic
either raises or silently produces non-integer departure times, and it
presents at Gate 1 as "the solver is broken" when it's actually a repo bug.
Every read path here coerces to plain int.
"""

from __future__ import annotations

import os
from decimal import Decimal

import boto3

from config import CONFIG
from repo import Repo

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_REGION", "us-east-1")

REQUESTS_TABLE = os.environ.get("REQUESTS_TABLE", "Requests")
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "Groups")
BLOCKS_TABLE = os.environ.get("BLOCKS_TABLE", "Blocks")
RELEASES_TABLE = os.environ.get("RELEASES_TABLE", "Releases")

_NUMERIC_REQUEST_FIELDS = ("p", "b", "a", "min_group_size", "decline_count")


def _clean_numbers(item: dict) -> dict:
    """Decimal -> int for known numeric fields; leave everything else as-is."""
    item = dict(item)
    for key in _NUMERIC_REQUEST_FIELDS:
        if key in item and isinstance(item[key], Decimal):
            item[key] = int(item[key])
    if "declined_anchors" in item:
        item["declined_anchors"] = [
            {k: (int(v) if isinstance(v, Decimal) else v) for k, v in anchor.items()}
            for anchor in item["declined_anchors"]
        ]
    return item


def _to_native(obj):
    """Recursively turn Decimal into int (or float if fractional), for JSON-safe output."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj


class DynamoRepo(Repo):
    def __init__(self):
        self._ddb = boto3.resource(
            "dynamodb",
            endpoint_url=ENDPOINT,
            region_name=REGION,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        )
        self.requests = self._ddb.Table(REQUESTS_TABLE)
        self.groups = self._ddb.Table(GROUPS_TABLE)
        self.blocks = self._ddb.Table(BLOCKS_TABLE)
        self.releases = self._ddb.Table(RELEASES_TABLE)

    # -- contract reads -------------------------------------------------

    def open_pool(self, route: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        resp = self.requests.query(
            IndexName="route-status-index",
            KeyConditionExpression=Key("route").eq(route) & Key("status").eq("PENDING"),
        )
        blocked_by_student = self._blocked_with_map()
        pool = []
        for item in resp.get("Items", []):
            item = _clean_numbers(item)
            item["blocked_with"] = blocked_by_student.get(item["student_id"], [])
            pool.append(item)
        return pool

    def get_request(self, student_id: str) -> dict | None:
        resp = self.requests.get_item(Key={"student_id": student_id})
        item = resp.get("Item")
        if item is None:
            return None
        item = _clean_numbers(item)
        item["blocked_with"] = self._blocked_with_map().get(student_id, [])
        return item

    def latest_release(self, route: str) -> dict | None:
        from boto3.dynamodb.conditions import Key

        resp = self.releases.query(
            IndexName="route-ran_at-index",
            KeyConditionExpression=Key("route").eq(route),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return _to_native(items[0]) if items else None

    def group_for(self, student_id: str) -> dict | None:
        req = self.requests.get_item(Key={"student_id": student_id}).get("Item")
        gid = (req or {}).get("current_group_id")
        if not gid:
            return None
        group = self.groups.get_item(Key={"group_id": gid}).get("Item")
        return _to_native(group) if group else None

    def config(self) -> dict:
        return CONFIG

    def _blocked_with_map(self) -> dict[str, list[str]]:
        resp = self.blocks.scan()
        out: dict[str, list[str]] = {}
        for block in resp.get("Items", []):
            a, b = block["student_a"], block["student_b"]
            out.setdefault(a, []).append(b)
            out.setdefault(b, []).append(a)
        return out

    # -- internal writes --------------------------------------------------

    def put_request(self, request: dict) -> None:
        self.requests.put_item(Item=request)

    def set_status(self, student_id: str, status: str) -> None:
        self.requests.update_item(
            Key={"student_id": student_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )

    def put_group(self, group: dict) -> None:
        self.groups.put_item(Item=group)
        for member in group["members"]:
            self.requests.update_item(
                Key={"student_id": member},
                UpdateExpression="SET #s = :s, current_group_id = :g",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "GROUPED", ":g": group["group_id"]},
            )

    def set_group_state(self, group_id: str, state: str) -> None:
        self.groups.update_item(
            Key={"group_id": group_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":s": state},
        )

    def record_response(self, group_id: str, student_id: str, accepted: bool) -> None:
        self.groups.update_item(
            Key={"group_id": group_id},
            UpdateExpression="SET responses.#sid = :a",
            ExpressionAttributeNames={"#sid": student_id},
            ExpressionAttributeValues={":a": accepted},
        )

    def dissolve(self, group_id: str) -> None:
        """The one place a group is torn down. respond and lifecycle_sweep both call this."""
        group = self.groups.get_item(Key={"group_id": group_id}).get("Item")
        if group is None:
            return
        self.groups.update_item(
            Key={"group_id": group_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":s": "DISSOLVED"},
        )
        for member in group.get("members", []):
            req = self.requests.get_item(Key={"student_id": member}).get("Item")
            if req is not None and req.get("current_group_id") == group_id:
                self.requests.update_item(
                    Key={"student_id": member},
                    UpdateExpression="SET #s = :s REMOVE current_group_id",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "PENDING"},
                )

    def add_block(self, student_a: str, student_b: str, reason: str) -> None:
        pair_key = "|".join(sorted([student_a, student_b]))
        self.blocks.put_item(
            Item={
                "pair_key": pair_key,
                "student_a": student_a,
                "student_b": student_b,
                "reason": reason,
            }
        )

    def put_release(self, release: dict) -> None:
        self.releases.put_item(Item=release)

    def apply_decline_delta(self, student_id: str, delta: dict) -> None:
        """Dispatcher: persists each key of on_decline()'s return value.
        No branching on reason strings -- purely key-driven, same as FakeRepo."""
        if "block_pair" in delta:
            a, b = delta["block_pair"]
            self.add_block(a, b, "PERSON")
        if "update_request" in delta:
            fields = delta["update_request"]
            self.requests.update_item(
                Key={"student_id": student_id},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ExpressionAttributeNames={f"#{k}": k for k in fields},
                ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
            )
        if "set_min_group_size" in delta:
            self.requests.update_item(
                Key={"student_id": student_id},
                UpdateExpression="SET min_group_size = :m",
                ExpressionAttributeValues={":m": delta["set_min_group_size"]},
            )
        if "withdraw" in delta:
            self.requests.delete_item(Key={"student_id": student_id})
            return
        if "increment_decline_count" in delta:
            self.requests.update_item(
                Key={"student_id": student_id},
                UpdateExpression="SET decline_count = if_not_exists(decline_count, :zero) + :inc",
                ExpressionAttributeValues={
                    ":inc": delta["increment_decline_count"],
                    ":zero": 0,
                },
            )
        if "add_anchor" in delta:
            self.requests.update_item(
                Key={"student_id": student_id},
                UpdateExpression="SET declined_anchors = list_append(if_not_exists(declined_anchors, :empty), :a)",
                ExpressionAttributeValues={":a": [delta["add_anchor"]], ":empty": []},
            )
