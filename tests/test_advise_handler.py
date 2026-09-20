import json

import pytest

from handlers import advise as advise_handler
from repo import FakeRepo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = FakeRepo(tmp_path / "db.json")
    monkeypatch.setattr(advise_handler, "get_repo", lambda: r)
    return r


def pending(repo, sid, p, b=30, a=30, route="COLLEGE_AIRPORT"):
    repo.put_request({"student_id": sid, "route": route, "p": p, "b": b, "a": a, "min_group_size": 2,
                      "status": "PENDING", "decline_count": 0, "declined_anchors": []})


def call(email=None, body=None):
    event = {"headers": {"X-Student-Email": email} if email else {}, "body": json.dumps(body or {})}
    resp = advise_handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


ME = "imt2022001@iiitb.ac.in"
GOOD = {"route": "COLLEGE_AIRPORT", "p": 1025, "b": 15, "a": 15}


def test_advice_comes_from_the_real_solver_on_the_real_pool(repo):
    pending(repo, "s1", 1020)
    pending(repo, "s2", 1030)
    status, body = call(ME, GOOD)
    assert status == 200
    assert body["simulated_outcome"] == {"minutes_earlier_ok": 15, "minutes_later_ok": 15, "outcome": "group",
                                        "group_size": 3, "minutes": 0, "direction": "on time", "flex_used_pct": 0}
    assert body["pool"]["waiting"] == 2
    assert "already a full cab" in body["message"]


def test_an_empty_pool_means_travelling_alone(repo):
    status, body = call(ME, GOOD)
    assert status == 200 and body["simulated_outcome"]["outcome"] == "alone"
    assert "you would travel alone" in body["message"]


def test_widening_is_offered_when_it_helps(repo):
    pending(repo, "s1", 1000, 60, 20)
    pending(repo, "s2", 1010, 60, 20)
    status, body = call(ME, {"route": "COLLEGE_AIRPORT", "p": 1050, "b": 15, "a": 15})
    assert status == 200 and body["simulated_outcome"]["outcome"] == "alone"
    assert body["better_window"]["outcome"] == "group"
    assert "Widening helps" in body["message"]


def test_the_callers_own_pending_request_is_left_out_of_the_simulation(repo):
    # their old window would otherwise be grouped alongside the new one
    pending(repo, "imt2022001", 1025, 5, 5)
    pending(repo, "s2", 1030)
    _, body = call(ME, GOOD)
    assert body["pool"]["waiting"] == 1


def test_no_identity_is_401(repo):
    assert call(None, GOOD)[0] == 401


def test_a_non_college_address_may_not_ask(repo):
    assert call("imt2022001@gmail.com", GOOD)[0] == 403  # Cedar: wrong domain


def test_an_address_that_is_not_a_roll_number_is_nobody(repo):
    assert call("hello@iiitb.ac.in", GOOD)[0] == 401


@pytest.mark.parametrize("body, missing", [
    ({"p": 1025, "b": 15, "a": 15}, "route"),
    ({"route": "COLLEGE_AIRPORT", "b": 15, "a": 15}, "p"),
    ({"route": "COLLEGE_AIRPORT", "p": 1025, "a": 15}, "b"),
])
def test_missing_fields_are_400(repo, body, missing):
    status, out = call(ME, body)
    assert status == 400 and missing in out["error"]


@pytest.mark.parametrize("body", [
    {"route": "MARS", "p": 1025, "b": 15, "a": 15},
    {"route": "COLLEGE_AIRPORT", "p": "5pm", "b": 15, "a": 15},
    {"route": "COLLEGE_AIRPORT", "p": 1500, "b": 15, "a": 15},
    {"route": "COLLEGE_AIRPORT", "p": 1025, "b": 0, "a": 15},
    {"route": "COLLEGE_AIRPORT", "p": 1025, "b": 15, "a": 999},
])
def test_bad_values_are_400(repo, body):
    assert call(ME, body)[0] == 400


def test_advice_writes_nothing(repo):
    pending(repo, "s1", 1020)
    before = repo.get_request("s1")
    call(ME, GOOD)
    assert repo.get_request("imt2022001") is None  # asking is not submitting
    assert repo.get_request("s1") == before
