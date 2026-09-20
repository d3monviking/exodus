import json

import pytest

from config import CONFIG
from handlers import get_my_request, lifecycle_sweep, release_orchestrator as ro, respond
from lifecycle import on_decline
from repo import FakeRepo

NOW = 1_800_000_000
DEADLINE = NOW + CONFIG["accept_window_minutes"] * 60
EMAIL = {s: f"{s}@iiitb.ac.in" for s in ("imt2022001", "imt2022002", "imt2022003", "imt2022099")}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = FakeRepo(tmp_path / "db.json")
    for mod in (respond, get_my_request, lifecycle_sweep):
        monkeypatch.setattr(mod, "get_repo", lambda r=r: r)
    monkeypatch.setattr(respond.time, "time", lambda: NOW + 10)
    return r


def add(repo, sid, p, b, a, route="COLLEGE_AIRPORT"):
    repo.put_request({"student_id": sid, "route": route, "p": p, "b": b, "a": a, "min_group_size": 2,
                      "status": "PENDING", "decline_count": 0, "declined_anchors": []})


@pytest.fixture
def grouped(repo):
    """s1, s2, s3 released into one FORMED triple."""
    add(repo, "imt2022001", 1020, 30, 30); add(repo, "imt2022002", 1030, 20, 20); add(repo, "imt2022003", 1040, 15, 30)
    ro.run_release(repo, now=NOW)
    return repo.group_for("imt2022001")["group_id"]


def call(sid, group_id, body, headers=True):
    event = {"headers": {"X-Student-Email": EMAIL[sid]} if headers else {},
             "pathParameters": {"id": group_id}, "body": json.dumps(body)}
    resp = respond.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def statuses(repo, *ids):
    return {i: (repo.get_request(i) or {}).get("status") for i in ids}


# ---- accepting ----

def test_all_accept_confirms_group_and_members(repo, grouped):
    for sid in ("imt2022001", "imt2022002"):
        code, body = call(sid, grouped, {"action": "accept"})
        assert code == 200 and body["state"] == "FORMED"
    code, body = call("imt2022003", grouped, {"action": "accept"})
    assert body == {"state": "CONFIRMED", "accepted": 3, "of": 3}
    assert set(statuses(repo, "imt2022001", "imt2022002", "imt2022003").values()) == {"CONFIRMED"}


def test_accept_is_idempotent_and_locks_the_member_in(repo, grouped):
    call("imt2022001", grouped, {"action": "accept"})
    assert call("imt2022001", grouped, {"action": "accept"})[1]["accepted"] == 1
    code, body = call("imt2022001", grouped, {"action": "decline", "reason": "TIME"})
    assert code == 409 and "locks you in" in body["error"]
    assert repo.get_group(grouped)["state"] == "FORMED"


@pytest.mark.parametrize("who,gid,body,code", [
    ("imt2022001", "nope", {"action": "accept"}, 404),
    ("imt2022099", "GROUP", {"action": "accept"}, 403),                     # not a member (Cedar)
    ("imt2022001", "GROUP", {"action": "dance"}, 400),
    ("imt2022001", "GROUP", {"action": "decline", "reason": "BORED"}, 400),
    ("imt2022001", "GROUP", {"action": "decline", "reason": "TIMEOUT"}, 400),  # system-only reason
    ("imt2022001", "GROUP", {"action": "decline", "reason": "TIME", "payload": {"b": 3}}, 400),
    ("imt2022001", "GROUP", {"action": "decline", "reason": "PERSON", "payload": {"named_student_id": "imt2022099"}}, 400),
    ("imt2022001", "GROUP", {"action": "decline", "reason": "PERSON", "payload": {"named_student_id": "9"}}, 400),
])
def test_bad_requests_are_rejected(repo, grouped, who, gid, body, code):
    assert call(who, grouped if gid == "GROUP" else gid, body)[0] == code
    assert repo.get_group(grouped)["state"] == "FORMED"  # nothing was dissolved by a bad request


def test_no_identity_is_401(repo, grouped):
    assert call("imt2022001", grouped, {"action": "accept"}, headers=False)[0] == 401


def test_late_and_stale_responses_are_409(repo, grouped, monkeypatch):
    monkeypatch.setattr(respond.time, "time", lambda: DEADLINE + 1)
    assert call("imt2022001", grouped, {"action": "accept"})[0] == 409
    monkeypatch.setattr(respond.time, "time", lambda: NOW + 10)
    call("imt2022001", grouped, {"action": "decline", "reason": "TOO_FEW"})
    assert call("imt2022002", grouped, {"action": "accept"})[0] == 409  # already DISSOLVED


# ---- declining: every reason, and that everyone returns to the pool ----

def test_decline_dissolves_and_returns_everyone_to_the_pool(repo, grouped):
    code, body = call("imt2022002", grouped, {"action": "decline", "reason": "TOO_FEW"})
    assert (code, body) == (200, {"state": "DISSOLVED", "reason": "TOO_FEW"})
    g = repo.get_group(grouped)
    assert (g["state"], g["dissolved_reason"], g["declined_by"]) == ("DISSOLVED", "TOO_FEW", "imt2022002")
    assert set(statuses(repo, "imt2022001", "imt2022002", "imt2022003").values()) == {"PENDING"}
    assert all(repo.get_request(s).get("current_group_id") is None for s in ("imt2022001", "imt2022002", "imt2022003"))


def test_person_decline_blocks_the_named_member(repo, grouped):
    code, body = call("imt2022001", grouped,
                      {"action": "decline", "reason": "PERSON", "payload": {"named_student_id": "imt2022003"}})
    assert code == 200 and body["state"] == "DISSOLVED"
    assert "imt2022003" in repo.get_request("imt2022001")["blocked_with"]
    assert repo.get_request("imt2022001")["decline_count"] == 0  # a block is a state change: no budget used

    # the next release routes around it: they are never together again
    out = ro.run_release(repo, now=NOW + 60)["COLLEGE_AIRPORT"]
    for g in out["groups"]:
        assert not {"imt2022001", "imt2022003"} <= set(g["members"])
    assert out["groups"] and out["stats"]["ungrouped"] == 1


@pytest.mark.parametrize("named", ["imt2022001", "imt2022099", "", 2, None])
def test_only_another_member_of_this_group_may_be_named(repo, grouped, named):
    code, body = call("imt2022001", grouped,
                      {"action": "decline", "reason": "PERSON", "payload": {"named_student_id": named}})
    assert code == 400 and "named_student_id" in body["error"]
    assert repo.get_group(grouped)["state"] == "FORMED"  # nothing happened


def test_time_decline_that_widens_updates_the_window_and_uses_no_budget(repo, grouped):
    call("imt2022001", grouped, {"action": "decline", "reason": "TIME", "payload": {"b": 60, "a": 10}})
    r = repo.get_request("imt2022001")
    assert (r["b"], r["a"], r["decline_count"]) == (60, 30, 0)  # a is never shrunk


def test_time_decline_that_changes_nothing_uses_budget_and_anchors_server_facts(repo, grouped):
    call("imt2022001", grouped, {"action": "decline", "reason": "TIME",
                         "payload": {"b": 30, "a": 30, "departure_time": 9999, "group_size": 9}})
    r = repo.get_request("imt2022001")
    assert r["decline_count"] == 1
    assert r["declined_anchors"] == [{"T": repo.get_group(grouped)["departure_time"], "size": 3}]


def test_too_few_sets_min_group_size(repo, grouped):
    call("imt2022001", grouped, {"action": "decline", "reason": "TOO_FEW"})
    assert repo.get_request("imt2022001")["min_group_size"] == 3


def test_plans_changed_withdraws_the_request_but_frees_the_others(repo, grouped):
    call("imt2022001", grouped, {"action": "decline", "reason": "PLANS_CHANGED"})
    assert statuses(repo, "imt2022001", "imt2022002", "imt2022003") == {"imt2022001": None, "imt2022002": "PENDING", "imt2022003": "PENDING"}


# ---- termination: the decline budget ----

def test_unchanged_declines_exhaust_the_budget_and_the_loop_ends(repo, grouped):
    same = {"action": "decline", "reason": "TIME", "payload": {"b": 30, "a": 30}}
    call("imt2022001", grouped, same)
    ro.run_release(repo, now=NOW + 60)
    call("imt2022001", repo.group_for("imt2022001")["group_id"], same)
    assert repo.get_request("imt2022001")["decline_count"] == 2

    out = ro.run_release(repo, now=NOW + 120)["COLLEGE_AIRPORT"]
    assert out["sat_out"] == ["imt2022001"] and repo.get_request("imt2022001")["status"] == "SAT_OUT"
    assert all("imt2022001" not in g["members"] for g in out["groups"])

    out = ro.run_release(repo, now=NOW + 180)["COLLEGE_AIRPORT"]  # next release: s1 is back, budget reset
    assert out["revived"] == ["imt2022001"]
    assert (repo.get_request("imt2022001")["status"], repo.get_request("imt2022001")["decline_count"]) == ("PENDING", 0)


# ---- the sweep ----

def test_sweep_penalises_only_the_silent_and_dissolves(repo, grouped):
    call("imt2022001", grouped, {"action": "accept"})
    assert lifecycle_sweep.sweep(repo, DEADLINE - 1)["dissolved"] == []  # not yet due

    out = lifecycle_sweep.sweep(repo, DEADLINE + 1)
    assert out["dissolved"] == [{"group_id": grouped, "timed_out": ["imt2022002", "imt2022003"]}]
    assert (repo.get_request("imt2022001")["decline_count"], repo.get_request("imt2022002")["decline_count"],
            repo.get_request("imt2022003")["decline_count"]) == (0, 1, 1)
    assert repo.get_group(grouped)["dissolved_reason"] == "TIMEOUT"
    assert set(statuses(repo, "imt2022001", "imt2022002", "imt2022003").values()) == {"PENDING"}


def test_sweep_confirms_a_fully_accepted_group_instead_of_dissolving_it(repo, grouped):
    for sid in ("imt2022001", "imt2022002", "imt2022003"):  # responses recorded but the confirming write was "lost"
        repo.record_response(grouped, sid, True)
    out = lifecycle_sweep.sweep(repo, DEADLINE + 1)
    assert out["confirmed"] == [grouped] and out["dissolved"] == []
    assert repo.get_group(grouped)["state"] == "CONFIRMED"


def test_sweep_is_idempotent(repo, grouped):
    lifecycle_sweep.sweep(repo, DEADLINE + 1)
    again = lifecycle_sweep.sweep(repo, DEADLINE + 2)
    assert again["dissolved"] == [] and repo.get_request("imt2022002")["decline_count"] == 1


def test_timeouts_count_toward_the_budget(repo, grouped):
    lifecycle_sweep.sweep(repo, DEADLINE + 1)
    ro.run_release(repo, now=NOW + 2000)
    lifecycle_sweep.sweep(repo, NOW + 2000 + DEADLINE)
    assert repo.get_request("imt2022002")["decline_count"] == 2
    assert on_decline(repo.get_request("imt2022002"), "TIMEOUT", {}) == {"increment_decline_count": 1}


# ---- what the student sees ----

def test_the_proposal_names_the_other_members(repo, grouped):
    ev = {"headers": {"X-Student-Email": EMAIL["imt2022001"]}}
    p = json.loads(get_my_request.handler(ev, None)["body"])["proposal"]
    # you can only decline "not with this person" if you know who they are
    assert [(o["student_id"], o["email"]) for o in p["others"]] == [
        ("imt2022002", "imt2022002@iiitb.ac.in"), ("imt2022003", "imt2022003@iiitb.ac.in")]
    assert all(o["name"] for o in p["others"])  # from the roster
    assert p["accepted"] == 0
    call("imt2022002", grouped, {"action": "accept"})
    assert json.loads(get_my_request.handler(ev, None)["body"])["proposal"]["accepted"] == 1
