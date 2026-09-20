import json

import pytest

from handlers import get_my_request, submit_request
from repo import FakeRepo


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = FakeRepo(tmp_path / "db.json")
    monkeypatch.setattr(submit_request, "get_repo", lambda: r)
    monkeypatch.setattr(get_my_request, "get_repo", lambda: r)
    return r


def call(handler, email=None, body=None):
    event = {"headers": {"X-Student-Email": email} if email else {}, "body": json.dumps(body or {})}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


GOOD = {"route": "COLLEGE_AIRPORT", "p": 1000, "b": 30, "a": 45}
ME = "imt2022001@iiitb.ac.in"


def test_submit_writes_pending_request(repo):
    status, body = call(submit_request, ME, GOOD)
    assert status == 201 and body["student_id"] == "imt2022001"
    assert repo.get_request("imt2022001")["status"] == "PENDING"


def test_body_student_id_is_ignored(repo):
    call(submit_request, ME, {**GOOD, "student_id": "victim"})
    assert repo.get_request("victim") is None


@pytest.mark.parametrize("email,code", [
    (None, 401),                                # no header
    ("hello@iiitb.ac.in", 401),                 # right domain, not a roll number
    ("imt2022001", 401),                        # not an address
    ("imt2022001@gmail.com", 403),              # a roll number, wrong domain (Cedar)
    ("imt2022001@iiitb.ac.in.evil.com", 403),   # the domain must be the ending
])
def test_identity_and_domain_rules(repo, email, code):
    assert call(submit_request, email, GOOD)[0] == code


@pytest.mark.parametrize("bad", [{"route": "MARS"}, {"p": 1000.5}, {"p": True}, {"p": 1440}, {"b": 4}, {"a": 999}])
def test_validation_rejects(repo, bad):
    assert call(submit_request, ME, {**GOOD, **bad})[0] == 400


def test_resubmit_while_grouped_is_rejected(repo):
    call(submit_request, ME, GOOD)
    repo.set_status("imt2022001", "GROUPED")
    assert call(submit_request, ME, GOOD)[0] == 409


def test_resubmit_keeps_decline_budget_and_sit_out(repo):
    call(submit_request, ME, GOOD)
    req = repo.get_request("imt2022001")
    req.pop("blocked_with")
    req.update(decline_count=2, min_group_size=3, status="SAT_OUT", declined_anchors=[{"T": 1000, "size": 2}])
    repo.put_request(req)
    call(submit_request, ME, {**GOOD, "p": 1100})
    after = repo.get_request("imt2022001")
    assert (after["decline_count"], after["min_group_size"], after["status"]) == (2, 3, "SAT_OUT")
    assert after["declined_anchors"] == [{"T": 1000, "size": 2}]


def test_me_never_leaks_blocked_with(repo):
    call(submit_request, ME, GOOD)
    repo.add_block("imt2022001", "imt2022009", "PERSON")
    status, body = call(get_my_request, ME)
    assert status == 200 and "blocked_with" not in body["request"]


def test_the_proposal_names_the_other_members(repo):
    for sid in ("imt2022001", "imt2022002"):
        call(submit_request, f"{sid}@iiitb.ac.in", GOOD)
    repo.put_group({"group_id": "g1", "route": "COLLEGE_AIRPORT", "members": ["imt2022001", "imt2022002"],
                    "departure_time": 1000, "state": "FORMED", "responses": {}})
    p = call(get_my_request, ME)[1]["proposal"]
    import roster
    assert p["others"] == [{"student_id": "imt2022002", "name": roster.name_for("imt2022002"),
                            "email": "imt2022002@iiitb.ac.in"}]
    assert p["others"][0]["name"]  # the shipped roster knows this one


def test_a_non_member_is_told_nothing_about_a_group(repo):
    for sid in ("imt2022001", "imt2022002", "imt2022099"):
        call(submit_request, f"{sid}@iiitb.ac.in", GOOD)
    repo.put_group({"group_id": "g1", "route": "COLLEGE_AIRPORT", "members": ["imt2022001", "imt2022002"],
                    "departure_time": 1000, "state": "FORMED", "responses": {}})
    assert call(get_my_request, "imt2022099@iiitb.ac.in")[1]["proposal"] is None


def test_a_student_may_ask_for_a_full_cab_up_front(repo):
    status, body = call(submit_request, ME, {**GOOD, "min_group_size": 3})
    assert status == 201 and body["min_group_size"] == 3
    assert repo.get_request("imt2022001")["min_group_size"] == 3


def test_min_group_size_defaults_to_two_and_is_validated(repo):
    assert call(submit_request, ME, GOOD)[1]["min_group_size"] == 2
    for bad in (1, 4, "three", True):
        status, out = call(submit_request, ME, {**GOOD, "min_group_size": bad})
        assert status == 400 and "min_group_size" in out["error"]


def test_a_resubmission_keeps_an_earlier_full_cab_preference(repo):
    call(submit_request, ME, {**GOOD, "min_group_size": 3})
    # the form need not send it again; a TOO_FEW decline sets it the same way
    assert call(submit_request, ME, GOOD)[1]["min_group_size"] == 3


def test_me_carries_the_callers_own_name(repo):
    import roster
    body = call(get_my_request, ME)[1]
    assert body["me"] == {"student_id": "imt2022001", "name": roster.name_for("imt2022001"), "email": ME}
