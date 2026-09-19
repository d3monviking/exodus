import pytest

from config import CONFIG
from handlers import release_orchestrator as ro
from repo import FakeRepo
from solver import solve

NOW = 1_800_000_000


@pytest.fixture
def repo(tmp_path):
    return FakeRepo(tmp_path / "db.json")


def add(repo, sid, p, b=30, a=30, route="COLLEGE_AIRPORT", **extra):
    repo.put_request({"student_id": sid, "route": route, "p": p, "b": b, "a": a, "min_group_size": 2,
                      "status": "PENDING", "decline_count": 0, "declined_anchors": [], **extra})


def triple(repo):
    add(repo, "s1", 1020, 30, 30)
    add(repo, "s2", 1030, 20, 20)
    add(repo, "s3", 1040, 15, 30)


def test_release_forms_a_group_and_records_everything(repo):
    triple(repo)
    out = ro.run_release(repo, now=NOW)["COLLEGE_AIRPORT"]
    assert out["status"] == "released" and len(out["groups"]) == 1

    g = repo.group_for("s1")
    assert g["state"] == "FORMED" and sorted(g["members"]) == ["s1", "s2", "s3"]
    assert g["departure_time"] % 5 == 0
    assert g["accept_deadline"] == NOW + CONFIG["accept_window_minutes"] * 60
    assert set(g["explanations"]) == {"s1", "s2", "s3"}
    assert all(repo.get_request(s)["status"] == "GROUPED" for s in ("s1", "s2", "s3"))

    rel = repo.latest_release("COLLEGE_AIRPORT")
    assert (rel["pool_size"], rel["groups_of_3"], rel["ungrouped"], rel["cabs_saved"]) == (3, 1, 0, 2)
    assert out["audit_log"] and (repo.path.parent / "release_log").exists()


def test_idle_routes_write_no_row_but_every_run_writes_a_run_row(repo):
    ro.run_release(repo, now=NOW)
    assert repo.latest_release("COLLEGE_AIRPORT") is None
    assert repo.latest_release("ALL")["ran_at"] == NOW


def test_an_idle_tick_does_not_overwrite_the_last_real_release(repo):
    # regression: the empty tick after a real release used to become "latest release",
    # making the board's cabs-saved number vanish two minutes after it appeared
    triple(repo)
    ro.run_release(repo, now=NOW)
    ro.run_release(repo, now=NOW + 120)
    assert repo.latest_release("COLLEGE_AIRPORT")["cabs_saved"] == 2
    assert repo.latest_release("ALL")["ran_at"] == NOW + 120


def test_run_row_totals_across_routes(repo):
    triple(repo)
    add(repo, "t1", 600, route="AIRPORT_COLLEGE"); add(repo, "t2", 610, route="AIRPORT_COLLEGE")
    ro.run_release(repo, now=NOW)
    run = repo.latest_release("ALL")
    assert (run["pool_size"], run["groups_of_3"], run["groups_of_2"], run["cabs_saved"]) == (5, 1, 1, 3)


def test_double_fire_is_skipped_and_never_regroups(repo):
    triple(repo)
    ro.run_release(repo, now=NOW)
    again = ro.run_release(repo, now=NOW + 3)["COLLEGE_AIRPORT"]
    assert again["status"] == "skipped_too_soon"

    forced = ro.run_release(repo, now=NOW + 3, force=True)["COLLEGE_AIRPORT"]
    assert forced["status"] == "released" and forced["groups"] == []  # members already GROUPED
    assert len([g for g in repo._read()["groups"].values()]) == 1


def test_release_allowed_again_after_the_gap(repo):
    triple(repo)
    ro.run_release(repo, now=NOW)
    later = NOW + CONFIG["min_release_gap_seconds"]
    assert ro.run_release(repo, now=later)["COLLEGE_AIRPORT"]["status"] == "released"


def test_cedar_rejects_a_blocked_group_even_if_the_solver_returns_it(repo, monkeypatch):
    triple(repo)
    repo.add_block("s1", "s3", "PERSON")
    monkeypatch.setattr(ro, "solve", lambda pool, cfg: {
        "groups": [{"members": ["s1", "s2", "s3"], "departure_time": 1030,
                    "penalties": {"s1": 0.3, "s2": 0.0, "s3": 0.3}, "cost": 0.6}],
        "ungrouped": [], "stats": {"pool_size": 3, "groups_of_3": 1, "groups_of_2": 0,
                                   "ungrouped": 0, "cabs_saved": 2, "total_cost": 0.6}})
    out = ro.run_release(repo, now=NOW)["COLLEGE_AIRPORT"]
    assert out["groups"] == [] and out["groups_rejected"] == 1
    assert all(repo.get_request(s)["status"] == "PENDING" for s in ("s1", "s2", "s3"))
    assert out["stats"]["groups_of_3"] == 0 and out["stats"]["ungrouped"] == 3  # stats reflect reality


def test_one_failing_route_does_not_stop_the_others(repo, monkeypatch):
    triple(repo)
    add(repo, "t1", 600, route="AIRPORT_COLLEGE"); add(repo, "t2", 610, route="AIRPORT_COLLEGE")
    real = ro.solve

    def flaky(pool, cfg):
        if pool[0]["route"] == "COLLEGE_AIRPORT":
            raise RuntimeError("boom")
        return real(pool, cfg)

    monkeypatch.setattr(ro, "solve", flaky)
    out = ro.run_release(repo, now=NOW)
    assert out["COLLEGE_AIRPORT"]["status"] == "error" and "boom" in out["COLLEGE_AIRPORT"]["error"]
    assert out["AIRPORT_COLLEGE"]["status"] == "released" and len(out["AIRPORT_COLLEGE"]["groups"]) == 1


def test_http_status_is_500_when_a_route_errors(repo, monkeypatch):
    monkeypatch.setattr(ro, "get_repo", lambda: repo)
    monkeypatch.setattr(ro, "solve", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    triple(repo)
    resp = ro.handler({"httpMethod": "POST"}, None)
    assert resp["statusCode"] == 500


# ---- stub solver: the contract invariants the platform relies on ----

def pool_of(*spec):
    return [{"student_id": f"x{i}", "route": "R", "p": p, "b": b, "a": a, "min_group_size": 2, "blocked_with": []}
            for i, (p, b, a) in enumerate(spec)]


def test_stub_solver_invariants():
    pool = pool_of((1000, 30, 30), (1010, 20, 20), (1020, 15, 30), (1300, 15, 15), (1305, 15, 15))
    out = solve(pool, CONFIG)
    seen = [m for g in out["groups"] for m in g["members"]] + out["ungrouped"]
    assert sorted(seen) == sorted(r["student_id"] for r in pool)  # each id exactly once
    by_id = {r["student_id"]: r for r in pool}
    for g in out["groups"]:
        assert g["departure_time"] % 5 == 0
        for m in g["members"]:
            r = by_id[m]
            assert r["p"] - r["b"] <= g["departure_time"] <= r["p"] + r["a"]  # feasible for everyone
            assert 0 <= g["penalties"][m] <= 1


def test_stub_solver_does_not_group_people_with_disjoint_windows():
    out = solve(pool_of((600, 15, 15), (900, 15, 15)), CONFIG)
    assert out["groups"] == [] and len(out["ungrouped"]) == 2


def test_stub_solver_respects_min_group_size_and_blocks():
    pool = pool_of((1000, 30, 30), (1010, 30, 30))
    pool[0]["min_group_size"] = 3
    assert solve(pool, CONFIG)["groups"] == []
    pool[0]["min_group_size"] = 2
    pool[1]["blocked_with"] = ["x0"]
    assert solve(pool, CONFIG)["groups"] == []


def test_stub_solver_is_deterministic():
    pool = pool_of((1000, 30, 30), (1010, 20, 20), (1020, 15, 30))
    assert solve(pool, CONFIG) == solve(list(reversed(pool)), CONFIG)
