import itertools
import random

import pytest

from config import CONFIG
from poolgen import generate
from solver import INF, group_cost, penalty, solve


def req(sid, p, b=30, a=30, min_group_size=2, blocked_with=()):
    return {"student_id": sid, "route": "COLLEGE_AIRPORT", "p": p, "b": b, "a": a,
            "min_group_size": min_group_size, "blocked_with": list(blocked_with), "declined_anchors": []}


def cfg(**overrides):
    return {**CONFIG, **overrides}


# ---- penalty -----------------------------------------------------------------

def test_penalty_is_the_fraction_of_declared_flexibility_spent():
    r = req("x", 1040, b=40, a=10)
    assert penalty(r, 1040) == 0
    assert penalty(r, 1020) == pytest.approx(0.5)   # 20 of 40 minutes early
    assert penalty(r, 1045) == pytest.approx(0.5)   # 5 of 10 minutes late
    assert penalty(r, 1000) == pytest.approx(1.0)   # edge of the window


# ---- group_cost: the hand-worked cases from section 6.5 ------------------------

def test_three_at_ten_minute_spacing_leave_together_at_the_middle_time():
    # 5:00, 5:10, 5:20 pm, all +-30: the triple leaves at 5:10.
    # 0.33 + 0 + 0.33 timing, plus beta * 0.33 fairness, no sharing cost for three.
    c, T, pens = group_cost([req("a", 1020), req("b", 1030), req("c", 1040)], CONFIG)
    assert T == 1030
    assert c == pytest.approx(1.0)
    assert pens == pytest.approx({"a": 1 / 3, "b": 0.0, "c": 1 / 3})


def test_a_pair_pays_sigma_per_person():
    c, T, _ = group_cost([req("a", 1020), req("b", 1030)], CONFIG)
    # best at 5:05: 5/30 each, plus beta * 5/30, plus 2 * sigma
    assert T == 1025
    assert c == pytest.approx(2 * 5 / 30 + 5 / 30 + 2 * CONFIG["sigma"])


def test_disjoint_windows_are_infeasible():
    assert group_cost([req("a", 600, 15, 15), req("b", 900, 15, 15)], CONFIG) == (INF, None, None)


def test_the_fairness_cap_rejects_a_time_that_maxes_someone_out():
    # the only shared time, 5:30, is the edge of both windows: penalty 1.0 each
    assert group_cost([req("a", 1000), req("b", 1060)], CONFIG)[0] == INF
    # without the cap it would be allowed
    assert group_cost([req("a", 1000), req("b", 1060)], cfg(p_cap=1.0))[0] < INF


def test_the_cap_filters_times_not_whole_groups():
    # Uncapped, the cheapest time is 5:00 pm, which spends all of b's lateness.
    # The cap must move the group to 4:55, not throw the group away.
    group = [req("a", 1040, b=30, a=60), req("b", 1000, b=5, a=20), req("c", 1025, b=15, a=60)]
    _, T_free, pens_free = group_cost(group, cfg(p_cap=1.0))
    assert T_free == 1020 and pens_free["b"] == pytest.approx(1.0)
    c, T, pens = group_cost(group, CONFIG)
    assert c < INF and T == 1015
    assert max(pens.values()) <= CONFIG["p_cap"]


def test_departure_time_is_the_exact_grid_optimum():
    rng = random.Random(0)
    for _ in range(300):
        members = [req(f"s{i}", 5 * rng.randint(190, 220), 5 * rng.randint(1, 18), 5 * rng.randint(1, 18))
                   for i in range(rng.choice((2, 3)))]
        c, T, _ = group_cost(members, CONFIG)
        # brute force over every grid time in a wide band
        best = INF
        for t in range(800, 1300, 5):
            pens = [penalty(r, t) for r in members]
            if all(r["p"] - r["b"] <= t <= r["p"] + r["a"] for r in members) and max(pens) <= CONFIG["p_cap"]:
                share = CONFIG["sigma"] if len(members) == 2 else 0
                best = min(best, sum(pens) + CONFIG["beta"] * max(pens) + len(members) * share)
        assert c == pytest.approx(best)


def test_min_group_size_and_blocks_are_hard_constraints():
    assert group_cost([req("a", 1020, min_group_size=3), req("b", 1030)], CONFIG)[0] == INF
    assert group_cost([req("a", 1020, min_group_size=3), req("b", 1030), req("c", 1040)], CONFIG)[0] < INF
    assert group_cost([req("a", 1020, blocked_with=["b"]), req("b", 1030)], CONFIG)[0] == INF
    # one-sided blocked_with still blocks: the check doesn't rely on the platform's symmetry
    assert group_cost([req("a", 1020), req("b", 1030, blocked_with=["a"])], CONFIG)[0] == INF


# ---- solve: the partition ----------------------------------------------------

def test_the_close_triple_wins():
    out = solve([req("a", 1020), req("b", 1030), req("c", 1040)], CONFIG)
    assert [g["members"] for g in out["groups"]] == [["a", "b", "c"]]
    assert out["groups"][0]["departure_time"] == 1030
    assert out["ungrouped"] == []


def test_a_far_third_forces_a_pair_and_is_left_alone():
    # third student moved to 6:10 pm: the triple is infeasible
    out = solve([req("a", 1020), req("b", 1030), req("c", 1090)], CONFIG)
    assert [g["members"] for g in out["groups"]] == [["a", "b"]]
    assert out["ungrouped"] == ["c"]
    assert out["stats"]["total_cost"] == pytest.approx(0.5 + 2 * CONFIG["sigma"] + CONFIG["upsilon"], abs=1e-4)


def test_upsilon_is_the_knob_between_pairing_up_and_travelling_alone():
    pool = [req("a", 1020), req("b", 1040)]  # a pair that works, but is a stretch
    assert len(solve(pool, CONFIG)["groups"]) == 1
    # make going alone cheap and the same two people are better off apart
    cheap = solve(pool, cfg(upsilon=0.5))
    assert cheap["groups"] == [] and cheap["ungrouped"] == ["a", "b"]


def test_blocks_reshape_the_grouping_instead_of_being_violated():
    pool = [req("a", 1020), req("b", 1030, blocked_with=["c"]), req("c", 1040, blocked_with=["b"]), req("d", 1045)]
    out = solve(pool, CONFIG)
    for g in out["groups"]:
        assert not {"b", "c"} <= set(g["members"])


def test_empty_and_single_pools():
    assert solve([], CONFIG) == {"groups": [], "ungrouped": [], "stats": {
        "pool_size": 0, "groups_of_3": 0, "groups_of_2": 0, "ungrouped": 0, "cabs_saved": 0, "total_cost": 0.0}}
    out = solve([req("a", 1020)], CONFIG)
    assert out["groups"] == [] and out["ungrouped"] == ["a"]


def test_duplicate_ids_fail_loudly():
    with pytest.raises(ValueError):
        solve([req("a", 1020), req("a", 1030)], CONFIG)


# ---- the contract invariants, on realistic pools ---------------------------------

POOLS = [generate(n, route, seed, blocks=blocks)
         for n, blocks in ((40, 0), (40, 4), (120, 10), (7, 1))
         for route in ("COLLEGE_AIRPORT", "AIRPORT_COLLEGE")
         for seed in range(3)]


@pytest.mark.parametrize("pool", POOLS)
def test_contract_invariants(pool):
    out = solve(pool, CONFIG)
    by_id = {r["student_id"]: r for r in pool}

    # every id exactly once
    seen = [m for g in out["groups"] for m in g["members"]] + out["ungrouped"]
    assert sorted(seen) == sorted(by_id)

    for g in out["groups"]:
        T, members = g["departure_time"], [by_id[m] for m in g["members"]]
        assert 2 <= len(members) <= CONFIG["max_group"]
        assert type(T) is int and T % CONFIG["grid_minutes"] == 0
        assert set(g["penalties"]) == set(g["members"])
        for r in members:
            assert r["p"] - r["b"] <= T <= r["p"] + r["a"]
            assert r["min_group_size"] <= len(members)
            assert not set(g["members"]) & set(r["blocked_with"])
            assert g["penalties"][r["student_id"]] == pytest.approx(penalty(r, T), abs=1e-4)
            assert g["penalties"][r["student_id"]] <= CONFIG["p_cap"]
        # the reported cost is the section 6.3 cost of this group at this time
        assert g["cost"] == pytest.approx(group_cost(members, CONFIG)[0], abs=1e-4)

    s = out["stats"]
    assert s["pool_size"] == len(pool)
    assert s["groups_of_3"] + s["groups_of_2"] == len(out["groups"])
    assert s["ungrouped"] == len(out["ungrouped"])
    grouped = sum(len(g["members"]) for g in out["groups"])
    assert s["cabs_saved"] == grouped - len(out["groups"])
    assert s["total_cost"] == pytest.approx(
        sum(g["cost"] for g in out["groups"]) + CONFIG["upsilon"] * s["ungrouped"], abs=1e-3)


@pytest.mark.parametrize("pool", POOLS[:6])
def test_same_input_same_output_whatever_the_order(pool):
    first = solve(pool, CONFIG)
    rng = random.Random(0)
    for _ in range(5):
        shuffled = pool[:]
        rng.shuffle(shuffled)
        assert solve(shuffled, CONFIG) == first


# ---- optimality, against brute force ---------------------------------------------

def _cost_of(partition, config):
    total = 0.0
    for g in partition:
        if len(g) == 1:
            total += config["upsilon"]
        else:
            c = group_cost(list(g), config)[0]
            if c == INF:
                return INF
            total += c
    return total


def _all_partitions(items):
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for k in (0, 1, 2):
        for partners in itertools.combinations(range(len(rest)), k):
            remaining = [x for j, x in enumerate(rest) if j not in partners]
            for p in _all_partitions(remaining):
                yield [[first, *(rest[j] for j in partners)]] + p


def _brute_force_optimum(pool, config):
    return min(_cost_of(p, config) for p in _all_partitions(pool))


def test_never_better_than_the_true_optimum_and_exact_with_uniform_flexibility():
    # With uniform flexibility the contiguity argument holds, so the DP must
    # hit the true optimum exactly (section 6.6).
    rng = random.Random(1)
    for _ in range(150):
        w = rng.choice((15, 30, 45))
        pool = [req(f"s{i}", 1000 + 5 * rng.randint(0, 24), w, w) for i in range(rng.randint(2, 7))]
        assert solve(pool, CONFIG)["stats"]["total_cost"] == pytest.approx(_brute_force_optimum(pool, CONFIG), abs=1e-3)


def test_the_gap_with_lopsided_windows_stays_small():
    # Varying, lopsided windows are where contiguity is only a heuristic. This
    # pins the measured behaviour so a regression shows up: rarely off the true
    # optimum, and never by much.
    rng = random.Random(2)
    trials, misses, worst = 300, 0, 0.0
    flex = (5, 10, 15, 30, 60, 90)
    for _ in range(trials):
        pool = [req(f"s{i}", 1000 + 5 * rng.randint(0, 24), rng.choice(flex), rng.choice(flex))
                for i in range(rng.randint(3, 7))]
        got = solve(pool, CONFIG)["stats"]["total_cost"]
        opt = _brute_force_optimum(pool, CONFIG)
        assert got >= opt - 1e-3  # can never beat the true optimum
        if got - opt > 1e-3:
            misses += 1
            worst = max(worst, got - opt)
    assert misses / trials <= 0.05
    assert worst <= CONFIG["upsilon"] / 2
