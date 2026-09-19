"""The model-free half of the agents: facts, template, fact lines and the reply guard.
No model or Strands call anywhere here."""

import pytest

from agents.explain import explain_for
from agents.facts import explain_facts, fact_lines, template
from agents.guarded import unsupported_numbers
from config import CONFIG
from poolgen import generate
from repo import FakeRepo
from solver import solve


def r(sid, p, b=30, a=30):
    return {"student_id": sid, "route": "COLLEGE_AIRPORT", "p": p, "b": b, "a": a,
            "min_group_size": 2, "blocked_with": [], "declined_anchors": []}


def group(T, *members):
    return {"members": [m["student_id"] for m in members], "departure_time": T, "state": "FORMED"}


ME, O1, O2 = r("s1", 1040, b=30, a=15), r("s2", 1020, b=40, a=15), r("s3", 1030, b=20, a=30)


def test_facts_are_the_solvers_numbers_and_name_nobody():
    f = explain_facts(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG)
    assert f["you"] == {"minutes_earlier_ok": 30, "minutes_later_ok": 15,
                        "minutes": 10, "direction": "earlier", "flex_used_pct": 33}
    assert f["group_size"] == f["fare_split_ways"] == 3
    assert f["others"] == [{"minutes": 10, "direction": "later", "flex_used_pct": 67},
                           {"minutes": 0, "direction": "on time", "flex_used_pct": 0}]
    assert "s2" not in str(f) and "s3" not in str(f)


def test_why_not_your_time():
    # s1's own 5:20 pm is past s2's latest, 5:15 (5:00 + 15 min)
    f = explain_facts(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG)
    assert f["why_not_your_time"] == {"reason": "outside_window", "members_affected": 1}
    # inside everyone's window but past the 90% cap for one
    other = r("s2", 1020, b=40, a=21)  # 20 of 21 minutes late = 95%
    f = explain_facts(ME, group(1030, ME, other), [other], CONFIG)
    assert f["why_not_your_time"]["reason"] == "over_cap"
    # comfortably feasible at their time, just not the group's best
    easy = r("s2", 1030, b=60, a=60)
    f = explain_facts(ME, group(1035, ME, easy), [easy], CONFIG)
    assert f["why_not_your_time"] == {"reason": "worse_overall"}
    # leaving exactly on time: nothing to explain
    assert explain_facts(ME, group(1040, ME, easy), [easy], CONFIG)["why_not_your_time"] is None


def test_template_states_the_facts():
    text = template(explain_facts(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG))
    assert "10 minutes earlier" in text and "33%" in text
    assert "2 others" in text and "3 ways" in text
    assert "outside another member's window" in text
    assert unsupported_numbers(text, explain_facts(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG)) == []


def test_template_and_fact_lines_on_every_solver_group_are_self_consistent():
    pool = generate(40, seed=3)
    by_id = {x["student_id"]: x for x in pool}
    for g in solve(pool, CONFIG)["groups"]:
        g = {**g, "state": "FORMED"}
        for m in g["members"]:
            f = explain_facts(by_id[m], g, [by_id[o] for o in g["members"] if o != m], CONFIG)
            for text in (template(f), fact_lines(f)):
                assert unsupported_numbers(text, f) == [], text
            assert f"{len(g['members']) - 1} other" in fact_lines(f)


@pytest.mark.parametrize("reply, bad", [
    ("You leave 10 minutes earlier, using 33% of your early start.", []),
    ("You share with two others and split the fare three ways.", []),
    ("You leave at 5:10 pm with the others.", ["5:10"]),
    ("Your cab goes at 5pm.", ["5pm"]),
    ("You leave 12 minutes earlier.", ["12"]),
    ("That uses 0.33 of your flexibility.", ["0.33"]),
])
def test_the_guard_rejects_numbers_the_facts_do_not_back(reply, bad):
    f = explain_facts(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG)
    assert unsupported_numbers(reply, f) == bad


# ---- explain_for, with the model swapped out --------------------------------

@pytest.fixture
def repo(tmp_path):
    rp = FakeRepo(tmp_path / "db.json")
    for x in (ME, O1, O2):
        rp.put_request({**x, "status": "PENDING", "decline_count": 0})
    rp.put_group({"group_id": "g1", "route": "COLLEGE_AIRPORT", "members": ["s1", "s2", "s3"],
                  "departure_time": 1030, "state": "FORMED", "accept_deadline": 0, "responses": {}})
    return rp


def fake_model(monkeypatch, reply, called=True):
    monkeypatch.setattr("agents.explain._ask_model",
                        lambda sid, facts: (reply, [fact_lines(facts)] if called else []))


def test_a_faithful_reply_is_used(repo, monkeypatch):
    fake_model(monkeypatch, "You leave 10 minutes earlier, which uses 33% of the flexibility you offered.")
    out = explain_for("s1", repo)
    assert out["source"] == "agent" and out["rejected"] is None


@pytest.mark.parametrize("reply, called, why", [
    ("You leave at 5:10 pm.", True, "unsupported numbers"),
    ("You leave 10 minutes earlier.", False, "tool not called"),
    ("", True, "empty reply"),
])
def test_anything_else_falls_back_to_the_template(repo, monkeypatch, reply, called, why):
    fake_model(monkeypatch, reply, called)
    out = explain_for("s1", repo)
    assert out["source"] == "template" and out["rejected"].startswith(why)
    assert out["text"] == template(out["facts"])


def test_a_model_failure_falls_back_to_the_template(repo, monkeypatch):
    def boom(sid, facts):
        raise ConnectionError("ollama is down")
    monkeypatch.setattr("agents.explain._ask_model", boom)
    out = explain_for("s1", repo)
    assert out["source"] == "template" and "ollama is down" in out["rejected"]


def test_a_slow_model_falls_back_to_the_template(repo, monkeypatch):
    import time
    monkeypatch.setattr("agents.explain._ask_model", lambda sid, facts: (time.sleep(2), ("late", ["x"]))[1])
    out = explain_for("s1", repo, timeout=0.2)
    assert out["source"] == "template" and out["rejected"].startswith("timed out")


def test_no_open_proposal(repo):
    assert explain_for("nobody", repo)["text"] is None


# ---- the claim check: real numbers with the wrong meaning --------------------------

from agents.guarded import claim_errors  # noqa: E402

TOOL = ["- You leave exactly at the time you asked for.\n"
        "- You share the cab with 2 other students, 3 people in total, and split the fare 3 ways.\n"
        "- One of them leaves 25 minutes later than they asked.\n"
        "- One of them leaves 10 minutes earlier than they asked."]


@pytest.mark.parametrize("reply", [
    "You leave on time with two other students; one leaves 25 minutes later and one 10 minutes earlier.",
    "You're one of 3 people in total, splitting the fare 3 ways.",
    "One of your fellow passengers leaves ten minutes before they asked.",
])
def test_faithful_replies_pass_the_claim_check(reply):
    assert claim_errors(reply, TOOL, group_exists=True) == []


@pytest.mark.parametrize("reply, error", [
    # each one a reply the 3B model actually gave on the demo pool
    ("You share this cab with 3 people.", "others [3]"),
    ("You are sharing with three others.", "others [3]"),
    ("Two other students who do not join your ride: one leaves 25 minutes later.", "negates the cab"),
    # right number, wrong direction
    ("One of them leaves 10 minutes later.", "shift [(10, 'later')]"),
])
def test_wrong_claims_are_caught(reply, error):
    assert any(e.startswith(error) for e in claim_errors(reply, TOOL, group_exists=True))


def test_travelling_alone_is_not_a_negated_cab_when_there_is_no_group():
    out = ["- With 15 minutes earlier and 15 minutes later allowed: you would travel alone."]
    assert claim_errors("Right now no one would share with you at that window.", out, group_exists=False) == []


def test_identical_cab_mates_are_one_fact_line():
    # "One of them leaves on time" twice made the model invent a third person.
    a, b = r("s2", 1040, 30, 30), r("s3", 1040, 30, 30)
    lines = fact_lines(explain_facts(ME, group(1040, ME, a, b), [a, b], CONFIG))
    assert "Both of them leave exactly when they asked." in lines
    assert "One of them" not in lines
