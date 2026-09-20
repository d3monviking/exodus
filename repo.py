"""
Data layer. Two implementations of the same interface, selected by env var
EXODUS_REPO=fake|dynamo (default: fake).

FakeRepo is JSON-file backed and fully functional today, so solver-side
work (agents, algorithm tuning) never has to wait on LocalStack being up.
DynamoRepo is added once the local stack exists (hours 5-8 of the platform
plan) but must satisfy the exact same interface.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from config import CONFIG

DEFAULT_FAKE_DB_PATH = Path(__file__).parent / ".data" / "fake_repo.json"


class Repo(ABC):
    """The five contract methods. See contracts.md."""

    @abstractmethod
    def open_pool(self, route: str) -> list[dict]:
        """PENDING requests for a route, blocked_with denormalised onto each."""

    @abstractmethod
    def get_request(self, student_id: str) -> dict | None:
        ...

    @abstractmethod
    def latest_release(self, route: str) -> dict | None:
        ...

    @abstractmethod
    def group_for(self, student_id: str) -> dict | None:
        ...

    @abstractmethod
    def config(self) -> dict:
        ...


class FakeRepo(Repo):
    """JSON-file backed. Single process, lock-protected. Demo/dev only."""

    def __init__(self, path: Path | str = DEFAULT_FAKE_DB_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(
                {"requests": {}, "groups": {}, "blocks": {}, "releases": {}}
            )

    # -- storage plumbing -------------------------------------------------

    def _read(self) -> dict:
        with open(self.path) as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(self.path)

    def reset(self) -> None:
        with self._lock:
            self._write({"requests": {}, "groups": {}, "blocks": {}, "releases": {}})

    # -- contract reads -----------------------------------------------------

    def open_pool(self, route: str) -> list[dict]:
        with self._lock:
            data = self._read()
            blocked_by_student = self._blocked_with_map(data)
            pool = []
            for r in data["requests"].values():
                if r["route"] == route and r["status"] == "PENDING":
                    r = dict(r)
                    r["blocked_with"] = blocked_by_student.get(r["student_id"], [])
                    pool.append(r)
            return pool

    def get_request(self, student_id: str) -> dict | None:
        with self._lock:
            data = self._read()
            r = data["requests"].get(student_id)
            if r is None:
                return None
            r = dict(r)
            r["blocked_with"] = self._blocked_with_map(data).get(student_id, [])
            return r

    def latest_release(self, route: str) -> dict | None:
        with self._lock:
            data = self._read()
            releases = [r for r in data["releases"].values() if r["route"] == route]
            if not releases:
                return None
            return max(releases, key=lambda r: r["ran_at"])

    def group_for(self, student_id: str) -> dict | None:
        with self._lock:
            data = self._read()
            gid = data["requests"].get(student_id, {}).get("current_group_id")
            if not gid:
                return None
            return data["groups"].get(gid)

    def config(self) -> dict:
        return CONFIG

    def _blocked_with_map(self, data: dict) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for block in data["blocks"].values():
            a, b = block["student_a"], block["student_b"]
            out.setdefault(a, []).append(b)
            out.setdefault(b, []).append(a)
        return out

    # -- internal writes, used by handlers -----------------------------------

    def put_request(self, request: dict) -> None:
        with self._lock:
            data = self._read()
            data["requests"][request["student_id"]] = request
            self._write(data)

    def set_status(self, student_id: str, status: str) -> None:
        with self._lock:
            data = self._read()
            data["requests"][student_id]["status"] = status
            self._write(data)

    def put_group(self, group: dict) -> None:
        with self._lock:
            data = self._read()
            data["groups"][group["group_id"]] = group
            for member in group["members"]:
                data["requests"][member]["status"] = "GROUPED"
                data["requests"][member]["current_group_id"] = group["group_id"]
            self._write(data)

    def set_group_state(self, group_id: str, state: str) -> None:
        with self._lock:
            data = self._read()
            data["groups"][group_id]["state"] = state
            self._write(data)

    def record_response(self, group_id: str, student_id: str, accepted: bool) -> None:
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group.setdefault("responses", {})[student_id] = accepted
            self._write(data)

    def get_group(self, group_id: str) -> dict | None:
        with self._lock:
            return self._read()["groups"].get(group_id)

    def formed_groups(self) -> list[dict]:
        with self._lock:
            return [g for g in self._read()["groups"].values() if g["state"] == "FORMED"]

    def confirm(self, group_id: str) -> None:
        """The one place a group is confirmed: group and every member move together."""
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group["state"] = "CONFIRMED"
            for member in group["members"]:
                req = data["requests"].get(member)
                if req is not None and req.get("current_group_id") == group_id:
                    req["status"] = "CONFIRMED"
            self._write(data)

    def revive_sat_out(self, route: str) -> list[str]:
        """Requests that sat out the previous release rejoin the pool with a fresh decline budget."""
        with self._lock:
            data = self._read()
            revived = []
            for req in data["requests"].values():
                if req["route"] == route and req["status"] == "SAT_OUT":
                    req["status"] = "PENDING"
                    req["decline_count"] = 0
                    revived.append(req["student_id"])
            self._write(data)
            return revived

    def dissolve(self, group_id: str, reason: str | None = None, declined_by: str | None = None) -> None:
        """The one place a group is torn down. respond and lifecycle_sweep both call this."""
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group["state"] = "DISSOLVED"
            if reason:
                group["dissolved_reason"] = reason
            if declined_by:
                group["declined_by"] = declined_by
            for member in group["members"]:
                req = data["requests"].get(member)
                if req is not None and req.get("current_group_id") == group_id:
                    req["status"] = "PENDING"
                    req["current_group_id"] = None
                    # so GET /requests/me can say why the cab fell through
                    req["last_group_id"] = group_id
            self._write(data)

    # -- a group that loses a member without dissolving ------------------------

    def update_group(self, group_id: str, fields: dict) -> None:
        with self._lock:
            data = self._read()
            data["groups"][group_id].update(fields)
            self._write(data)

    def reduce_group(self, group_id: str, leaver: str, accept_deadline: int, explanations: dict) -> None:
        """A member leaves and the rest are asked again, at the same departure time.

        Consent doesn't survive a change of package, so every response is cleared.
        """
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group["members"] = [m for m in group["members"] if m != leaver]
            group.update(responses={}, reduced=True, left_by=leaver, accept_deadline=accept_deadline,
                         explanations=explanations)
            group["excluded"] = [*group.get("excluded", []), leaver]
            req = data["requests"].get(leaver)
            if req is not None and req.get("current_group_id") == group_id:
                req["status"] = "PENDING"
                req["current_group_id"] = None
                req["last_group_id"] = group_id
            self._write(data)

    def open_seat_groups(self, route: str) -> list[dict]:
        """Confirmed pairs still looking for a third rider, with no offer outstanding."""
        with self._lock:
            return [g for g in self._read()["groups"].values()
                    if g["route"] == route and g["state"] == "CONFIRMED" and g.get("open_seat")
                    and not g.get("seat_offer")]

    def groups_with_offers(self) -> list[dict]:
        with self._lock:
            return [g for g in self._read()["groups"].values() if g.get("seat_offer")]

    def offer_seat(self, group_id: str, student_id: str, deadline: int, explanation: str) -> None:
        """Offer a confirmed pair's third seat to a pending student, who must accept it."""
        with self._lock:
            data = self._read()
            data["groups"][group_id]["seat_offer"] = {
                "student_id": student_id, "deadline": deadline, "explanation": explanation}
            req = data["requests"][student_id]
            req["status"] = "GROUPED"
            req["current_group_id"] = group_id
            self._write(data)

    def accept_seat(self, group_id: str, student_id: str) -> None:
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group["members"] = [*group["members"], student_id]
            group["seat_offer"] = None
            group["open_seat"] = False
            req = data["requests"].get(student_id)
            if req is not None:
                req["status"] = "CONFIRMED"
            self._write(data)

    def decline_seat(self, group_id: str, student_id: str) -> None:
        """The offer ends without the pair being touched; this student is not asked again for this cab."""
        with self._lock:
            data = self._read()
            group = data["groups"][group_id]
            group["seat_offer"] = None
            group["excluded"] = [*group.get("excluded", []), student_id]
            req = data["requests"].get(student_id)
            if req is not None and req.get("current_group_id") == group_id:
                req["status"] = "PENDING"
                req["current_group_id"] = None
            self._write(data)

    def add_block(self, student_a: str, student_b: str, reason: str) -> None:
        with self._lock:
            data = self._read()
            pair_key = "|".join(sorted([student_a, student_b]))
            data["blocks"][pair_key] = {
                "pair_key": pair_key,
                "student_a": student_a,
                "student_b": student_b,
                "reason": reason,
            }
            self._write(data)

    def put_release(self, release: dict) -> None:
        with self._lock:
            data = self._read()
            data["releases"][release["release_id"]] = release
            self._write(data)

    def append_release_log(self, release: dict, groups: list[dict]) -> str:
        """Local stand-in for the S3 audit log. Returns the file path used as the key."""
        log_dir = self.path.parent / "release_log"
        log_dir.mkdir(parents=True, exist_ok=True)
        key = f"releases/{release['ran_at']}-{release['release_id']}.json"
        out = log_dir / key.replace("/", "_")
        out.write_text(json.dumps({"release": release, "groups": groups}, indent=2, default=str))
        return key

    def apply_decline_delta(self, student_id: str, delta: dict) -> None:
        """Dispatcher: persists each key of on_decline()'s return value.
        No branching on reason strings — this is purely key-driven."""
        with self._lock:
            data = self._read()
            req = data["requests"][student_id]

            if "block_pair" in delta:
                a, b = delta["block_pair"]
                pair_key = "|".join(sorted([a, b]))
                data["blocks"][pair_key] = {
                    "pair_key": pair_key,
                    "student_a": a,
                    "student_b": b,
                    "reason": "PERSON",
                }
            if "update_request" in delta:
                req.update(delta["update_request"])
            if "set_min_group_size" in delta:
                req["min_group_size"] = delta["set_min_group_size"]
            if "withdraw" in delta:
                del data["requests"][student_id]
                self._write(data)
                return
            if "increment_decline_count" in delta:
                req["decline_count"] = req.get("decline_count", 0) + delta[
                    "increment_decline_count"
                ]
            if "add_anchor" in delta:
                req.setdefault("declined_anchors", []).append(delta["add_anchor"])

            self._write(data)


def get_repo() -> Repo:
    kind = os.environ.get("EXODUS_REPO", "fake")
    if kind == "fake":
        return FakeRepo()
    if kind == "dynamo":
        from repo_dynamo import DynamoRepo  # deferred import, added with the infra step

        return DynamoRepo()
    raise ValueError(f"unknown EXODUS_REPO: {kind}")
