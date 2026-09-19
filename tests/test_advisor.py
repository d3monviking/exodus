import pytest

from advisor import YOU, advice, advise, advise_facts, density, simulate
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
    text = advice(f)
    assert "you would travel alone" in text and "Widening helps" in text


def test_a_full_cab_is_not_told_to_widen():
    f = advise_facts(POOL, "COLLEGE_AIRPORT", 1025, 15, 15, CONFIG)
    assert f["better"] is None and "already a full cab" in advice(f)


def test_nothing_helps_says_so():
    f = advise_facts([r("s9", 300, 5, 5)], "COLLEGE_AIRPORT", 1200, 15, 15, CONFIG)
    assert f["better"] is None and "wouldn't change that" in advice(f)


def test_a_lower_percentage_for_the_same_cab_is_not_an_improvement():
    # Same group, same departure: widening only shrinks the percentage of the
    # very same shift, which must not count as "better".
    pool = [r("s1", 1000, 30, 30), r("s2", 1000, 30, 30)]
    f = advise_facts(pool, "COLLEGE_AIRPORT", 1010, 30, 30, CONFIG)
    assert f["current"]["outcome"] == "group" and f["current"]["group_size"] == 3
    assert f["better"] is None


def test_a_window_is_only_ever_widened_never_narrowed():
    pool = generate(40, seed=3)
    f = advise_facts(pool, "COLLEGE_AIRPORT", 1025, 40, 20, CONFIG)
    if f["better"]:
        assert f["better"]["minutes_earlier_ok"] >= 40 and f["better"]["minutes_later_ok"] >= 20


@pytest.mark.parametrize("p", range(840, 1320, 60))
def test_advice_always_says_what_your_window_gets_you_and_ends_with_the_caveat(p):
    f = advise_facts(generate(40, seed=3), "COLLEGE_AIRPORT", p, 15, 15, CONFIG)
    text = advice(f)
    assert "With 15 minutes earlier and 15 minutes later allowed" in text
    assert text.endswith("requests that arrive before the release can change it.")


def test_advise_reads_the_pool_through_the_repo(tmp_path):
    repo = FakeRepo(tmp_path / "db.json")
    for x in POOL:
        repo.put_request({**x, "status": "PENDING", "decline_count": 0})
    out = advise("COLLEGE_AIRPORT", 1025, 15, 15, repo)
    assert "already a full cab" in out["text"]
    assert out["facts"]["current"]["group_size"] == 3
    # a student already in the pool is not simulated against themselves
    assert advise("COLLEGE_AIRPORT", 1020, 15, 15, repo, student_id="s1")["facts"]["density"]["waiting"] == 2
