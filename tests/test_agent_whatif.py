"""advise's model-free half: the what-ifs, the templated advice, and its fallbacks. No model calls."""

import pytest

from agents.advise import advise
from agents.guarded import unsupported_numbers
from agents.whatif import WIDEN_TO, YOU, advise_facts, advise_template, density, density_line, sim_line, simulate
from config import CONFIG
from poolgen import generate
from repo import FakeRepo


def r(sid, p, b=30, a=30):
    return {"student_id": sid, "route": "COLLEGE_AIRPORT", "p": p, "b": b, "a": a,
            "min_group_size": 2, "blocked_with": [], "declined_anchors": []}


POOL = [r("s1", 1020), r("s2", 1030), r("s9", 1300)]


def test_density_counts_the_pool_around_your_time_and_leaves_you_out():
    assert density(POOL, 1025) == {"waiting": 3, "within_30": 2, "within_60": 2}
    assert density(POOL, 1025, student_id="s1") == {"waiting": 2, "within_30": 1, "within_60": 1}


def test_simulate_joins_you_to_the_real_solver_outcome():
    sim = simulate(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG)
    assert sim["outcome"] == "group" and sim["group_size"] == 3
    assert sim["direction"] == "on time" and sim["flex_used_pct"] == 0
    assert simulate([], "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG) == {
        "minutes_earlier_ok": 15, "minutes_later_ok": 15, "outcome": "alone"}


def test_simulate_does_not_touch_the_pool():
    before = [dict(x) for x in POOL]
    simulate(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG)
    assert POOL == before and all(x["student_id"] != YOU for x in POOL)


def test_a_narrow_window_left_alone_is_told_what_widening_buys():
    # the others can leave no later than 5:10 pm; you, at 5:30 +-15, can't go before 5:15
    f = advise_facts([r("s1", 1000, 60, 20), r("s2", 1010, 60, 20)], "COLLEGE_AIRPORT", 1050, 15, 15, CONFIG)
    assert f["current"]["outcome"] == "alone"
    assert f["better"]["outcome"] == "group"
    text = advise_template(f)
    assert "you would travel alone" in text and "Widening helps" in text


def test_a_full_cab_is_not_told_to_widen():
    f = advise_facts(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG)
    assert f["better"] is None and "already a full cab" in advise_template(f)


def test_nothing_helps_says_so():
    f = advise_facts([r("s9", 300, 5, 5)], "COLLEGE_AIRPORT", 1200, 15, 15, CONFIG)
    assert f["better"] is None and "wouldn't change that" in advise_template(f)


@pytest.mark.parametrize("p", range(840, 1320, 35))
def test_templated_advice_never_states_an_unsupported_number(p):
    pool = generate(40, seed=3)
    f = advise_facts(pool, "COLLEGE_AIRPORT", p, 15, 15, CONFIG)
    # exactly what the template draws on: the tool sentences, and the widest window it tried
    sources = [density_line(f["density"]), sim_line(f["current"]), WIDEN_TO[-1]]
    if f["better"]:
        sources.append(sim_line(f["better"]))
    assert unsupported_numbers(advise_template(f), sources) == []


# ---- advise(), with the model swapped out ----------------------------------------

@pytest.fixture
def repo(tmp_path):
    rp = FakeRepo(tmp_path / "db.json")
    for x in POOL:
        rp.put_request({**x, "status": "PENDING", "decline_count": 0})
    return rp


def test_template_mode_never_calls_the_model(repo, monkeypatch):
    monkeypatch.setattr("agents.advise._ask_model", lambda *a: pytest.fail("model called"))
    out = advise("COLLEGE_AIRPORT", 1025, 15, 15, repo, use_model=False)
    assert out["source"] == "template" and "already a full cab" in out["text"]


def test_a_reply_backed_by_the_tools_is_used(repo, monkeypatch):
    line = sim_line(simulate(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG))
    monkeypatch.setattr("agents.advise._ask_model",
                        lambda *a: ("Right now your window already gets you a cab with 2 others.", [line]))
    assert advise("COLLEGE_AIRPORT", 1025, 15, 15, repo)["source"] == "agent"


def test_a_reply_quoting_numbers_no_tool_gave_falls_back(repo, monkeypatch):
    line = sim_line(simulate(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG))
    monkeypatch.setattr("agents.advise._ask_model",
                        lambda *a: ("Widen to 40 minutes and you'll leave at 5:15.", [line]))
    out = advise("COLLEGE_AIRPORT", 1025, 15, 15, repo)
    assert out["source"] == "template" and out["rejected"].startswith("unsupported numbers")


def test_a_lower_percentage_for_the_same_cab_is_not_an_improvement():
    # Same group, same departure: widening only shrinks the percentage. The
    # advice must not call that "better" (it did, and the model then claimed
    # the wider window leaves "even earlier").
    pool = [r("s1", 1000, 30, 30), r("s2", 1000, 30, 30)]
    f = advise_facts(pool, "COLLEGE_AIRPORT", 1010, 30, 30, CONFIG)
    assert f["current"]["outcome"] == "group" and f["current"]["group_size"] == 3
    assert f["better"] is None
