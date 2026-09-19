import pytest

from config import CONFIG
from explainer import explain, explain_facts, explanation
from poolgen import generate
from solver import penalty, solve


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


def test_the_explanation_states_the_facts():
    text = explain(ME, group(1030, ME, O1, O2), [O1, O2], CONFIG)
    assert "10 minutes earlier" in text and "33%" in text
    assert "2 others" in text and "3 ways" in text
    assert "one moves 10 minutes later" in text
    assert "outside another member's window" in text


def test_a_pair_reads_as_a_pair():
    other = r("s2", 1030, b=60, a=60)
    text = explain(ME, group(1040, ME, other), [other], CONFIG)
    assert "You leave exactly when you asked." in text
    assert "1 other," in text and "2 ways" in text


def test_every_group_in_a_real_pool_gets_an_explanation_that_matches_the_solver():
    pool = generate(40, seed=3)
    by_id = {x["student_id"]: x for x in pool}
    for g in solve(pool, CONFIG)["groups"]:
        for m in g["members"]:
            others = [by_id[o] for o in g["members"] if o != m]
            f = explain_facts(by_id[m], g, others, CONFIG)
            # the student's own shift, as the solver computed it
            assert f["you"]["flex_used_pct"] == round(100 * penalty(by_id[m], g["departure_time"]))
            assert f["you"]["minutes"] == abs(g["departure_time"] - by_id[m]["p"])
            assert len(f["others"]) == len(g["members"]) - 1
            text = explanation(f)
            assert text.startswith("You leave") and f"{len(g['members'])} ways" in text
            for o in g["members"]:
                assert o not in text
