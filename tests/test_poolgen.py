from config import CONFIG, ROUTES
from poolgen import BLOCK_NEAR, OUTLIER_GAP, WAVES, generate
from solver import solve

CONTRACT_KEYS = {"student_id", "route", "p", "b", "a", "min_group_size", "blocked_with", "declined_anchors"}


def test_same_seed_same_pool():
    assert generate(40, seed=3) == generate(40, seed=3)
    assert generate(40, seed=3) != generate(40, seed=4)


def test_every_route_produces_contract_shaped_requests():
    for route in ROUTES:
        pool = generate(40, route, seed=1)
        assert len(pool) == 40
        assert len({r["student_id"] for r in pool}) == 40
        for r in pool:
            assert set(r) == CONTRACT_KEYS
            assert r["route"] == route
            assert all(type(r[k]) is int for k in ("p", "b", "a", "min_group_size"))
            assert 0 <= r["p"] < 1440 and r["p"] % 5 == 0
            # exactly what submit_request accepts
            assert 5 <= r["b"] <= 240 and 5 <= r["a"] <= 240
            assert r["b"] % 5 == 0 and r["a"] % 5 == 0
            assert r["min_group_size"] in (2, 3)
            assert r["declined_anchors"] == []


def test_ids_are_real_looking_student_ids():
    pool = generate(5, first_id=500)
    assert sorted(r["student_id"] for r in pool) == [f"imt2022{n}" for n in range(500, 505)]


def test_preferred_times_cluster_around_the_waves():
    pool = generate(200, "COLLEGE_AIRPORT", seed=7)
    centres = [c for c, _, _ in WAVES["COLLEGE_AIRPORT"]]
    near = sum(any(abs(r["p"] - c) <= 60 for c in centres) for r in pool)
    assert near / len(pool) >= 0.85


def test_a_realistic_pool_has_outliers_well_away_from_every_wave():
    pool = generate(40, "COLLEGE_AIRPORT", seed=3)
    centres = [c for c, _, _ in WAVES["COLLEGE_AIRPORT"]]
    outliers = [r for r in pool if all(abs(r["p"] - c) >= OUTLIER_GAP for c in centres)]
    assert 1 <= len(outliers) <= 6


def test_tight_windows_are_asymmetric_the_right_way_round():
    # Out of college the deadline is lateness, so a tight student gives little `a`.
    out = [r for r in generate(200, "COLLEGE_AIRPORT", seed=2) if r["a"] <= 15]
    assert out and sum(r["b"] > r["a"] for r in out) / len(out) >= 0.9
    # Back from the airport nobody can leave before landing, so it is `b` that is tight.
    back = [r for r in generate(200, "AIRPORT_COLLEGE", seed=2) if r["b"] <= 15]
    assert back and sum(r["a"] > r["b"] for r in back) / len(back) >= 0.9


def test_blocks_are_symmetric_between_near_neighbours():
    pool = generate(40, seed=5, blocks=4)
    by_id = {r["student_id"]: r for r in pool}
    pairs = {tuple(sorted((r["student_id"], o))) for r in pool for o in r["blocked_with"]}
    assert len(pairs) == 4
    for x, y in pairs:
        assert x != y
        assert x in by_id[y]["blocked_with"] and y in by_id[x]["blocked_with"]
        # close enough in time that the block actually constrains the solver
        assert abs(by_id[x]["p"] - by_id[y]["p"]) <= BLOCK_NEAR


def test_no_blocks_unless_asked():
    assert all(r["blocked_with"] == [] for r in generate(40, seed=5))


def test_a_generated_pool_goes_straight_into_the_solver():
    pool = generate(40, seed=3, blocks=3)
    out = solve(pool, CONFIG)
    seen = [m for g in out["groups"] for m in g["members"]] + out["ungrouped"]
    assert sorted(seen) == sorted(r["student_id"] for r in pool)
