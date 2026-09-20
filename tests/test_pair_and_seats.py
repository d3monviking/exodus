"""
A trio that loses a member carries on as a pair; a confirmed pair can gain a
third rider. Everything at the handler level, against the JSON fake, with the
clock passed in.

NOW is 13:30 IST on 2027-01-15. The demo trio leaves at 5:15 pm (minute 1035),
so at NOW it is 225 minutes away: too close for a third rider to be looked for
(the pair must leave 360 minutes after the next release), and close enough that
the seat could be filled if it were open (180 minutes before departure).
Setting the travel date to the next day puts the departure a day off, which is
how the tests that need an open seat get one.
"""

import json

import pytest

import roster
import schedule
from config import CONFIG
from handlers import get_my_request, lifecycle_sweep, release_orchestrator as ro, respond
from repo import FakeRepo

NOW = 1_800_000_000
WINDOW = CONFIG["accept_window_minutes"] * 60
TOMORROW = "2027-01-16"
EMAIL = {s: f"{s}@iiitb.ac.in" for s in (f"imt2022{n:03d}" for n in range(1, 10))}
A, B, C, D, E, F = (f"imt2022{n:03d}" for n in range(1, 7))  # A, B, C are the trio


@pytest.fixture(autouse=True)
def no_travel_date(monkeypatch):
    monkeypatch.delenv("EXODUS_TRAVEL_DATE", raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = FakeRepo(tmp_path / "db.json")
    for mod in (respond, get_my_request, lifecycle_sweep):
        monkeypatch.setattr(mod, "get_repo", lambda r=r: r)
    monkeypatch.setattr(respond.time, "time", lambda: NOW + 10)
    return r


def add(repo, sid, p, b=30, a=30, **extra):
    repo.put_request({"student_id": sid, "route": "COLLEGE_AIRPORT", "p": p, "b": b, "a": a, "min_group_size": 2,
                      "status": "PENDING", "decline_count": 0, "declined_anchors": [], **extra})


@pytest.fixture
def trio(repo):
    """A, B, C released into one FORMED trio leaving at 5:15 pm."""
    add(repo, A, 1020, 30, 30); add(repo, B, 1030, 20, 20); add(repo, C, 1040, 15, 30)
    ro.run_release(repo, now=NOW)
    g = repo.group_for(A)
    assert g["departure_time"] == 1035 and len(g["members"]) == 3
    return g["group_id"]


def call(sid, group_id, body):
    event = {"headers": {"X-Student-Email": EMAIL[sid]}, "pathParameters": {"id": group_id}, "body": json.dumps(body)}
    resp = respond.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def me(sid):
    return json.loads(get_my_request.handler({"headers": {"X-Student-Email": EMAIL[sid]}}, None)["body"])


def decline(sid, gid, reason="TOO_FEW", **payload):
    return call(sid, gid, {"action": "decline", "reason": reason, **({"payload": payload} if payload else {})})


def accept(sid, gid):
    return call(sid, gid, {"action": "accept"})


def release(repo, at):
    return ro.run_release(repo, now=at)["COLLEGE_AIRPORT"]


# ---- the clock ----------------------------------------------------------------

def test_minutes_until_a_departure_on_the_day_of_the_release():
    assert schedule.minutes_until(1035, NOW) == 225  # 13:30 IST to 17:15 IST


def test_minutes_until_a_departure_on_a_later_travel_day(monkeypatch):
    monkeypatch.setenv("EXODUS_TRAVEL_DATE", TOMORROW)
    assert schedule.minutes_until(1035, NOW) == 225 + 24 * 60
    assert schedule.minutes_until(1035, NOW, {**CONFIG, "travel_date": TOMORROW}) == 225 + 24 * 60


def test_the_timezone_is_the_colleges_not_the_servers():
    # a Lambda runs on UTC: 17:15 IST is 11:45 UTC, which is 225 minutes after 08:00 UTC
    assert schedule.minutes_until(1035, NOW, {**CONFIG, "tz_offset_minutes": 330}) == 225
    # the same minute-of-day read as UTC is 17:15 UTC, 555 minutes after 08:00 UTC
    assert schedule.minutes_until(1035, NOW, {**CONFIG, "tz_offset_minutes": 0}) == 555


def test_a_departure_that_has_left_is_negative():
    assert schedule.minutes_until(600, NOW) == -210  # 10:00 IST, three and a half hours ago


@pytest.mark.parametrize("lead, opens", [(359, False), (360, True), (900, True)])
def test_a_pair_stays_open_only_if_it_leaves_late_enough_after_the_next_release(lead, opens):
    assert schedule.seat_may_open(1035, NOW + (225 - lead) * 60) is opens


@pytest.mark.parametrize("lead, fillable", [(179, False), (180, True), (600, True)])
def test_a_release_may_offer_the_seat_only_a_few_hours_before_departure(lead, fillable):
    assert schedule.seat_fillable(1035, NOW + (225 - lead) * 60) is fillable


def test_the_next_release_is_one_interval_after_the_last_and_never_in_the_past(repo):
    assert schedule.next_release_at(repo, NOW) is None  # nothing has run yet
    ro.run_release(repo, now=NOW)
    assert schedule.next_release_at(repo, NOW + 10) == NOW + schedule.RELEASE_INTERVAL_SECONDS
    assert schedule.next_release_at(repo, NOW + 999) == NOW + 999  # overdue means now


# ---- a decline in a trio: the other two carry on as a pair --------------------

def test_a_decline_in_a_trio_leaves_a_pair_at_the_same_time_and_tells_the_decliner_when_to_come_back(repo, trio):
    code, body = decline(A, trio, "TIME")
    assert (code, body["state"], body["reason"]) == (200, "REDUCED", "TIME")
    assert body["next_release_at"] == NOW + schedule.RELEASE_INTERVAL_SECONDS  # the warning the page shows

    g = repo.get_group(trio)
    assert g["state"] == "FORMED" and g["reduced"] is True and g["left_by"] == A
    assert sorted(g["members"]) == [B, C]
    assert g["departure_time"] == 1035  # locked: the pair keeps the trio's time
    assert g["accept_deadline"] == NOW + 10 + WINDOW  # a fresh window to decide
    assert A in g["excluded"]


def test_the_decliner_goes_back_to_the_pool(repo, trio):
    decline(A, trio)
    r = repo.get_request(A)
    assert (r["status"], r["current_group_id"], r["last_group_id"]) == ("PENDING", None, trio)
    assert A in [x["student_id"] for x in repo.open_pool("COLLEGE_AIRPORT")]
    assert me(A)["proposal"] is None and me(A)["last_outcome"] is None  # nothing fell through for them


def test_the_pair_is_told_who_backed_out_and_asked_again(repo, trio):
    decline(A, trio)
    p = me(B)["proposal"]
    assert (p["state"], p["reduced"], p["size"], p["invited"]) == ("FORMED", True, 2, False)
    assert p["left_by"] == {"student_id": A, "name": roster.name_for(A)}
    assert [o["student_id"] for o in p["others"]] == [C]
    assert p["departure_time"] == 1035
    assert "2 ways" in p["explanation"] and "1 other" in p["explanation"]  # rewritten for a pair


def test_an_earlier_accept_does_not_survive_the_change(repo, trio):
    accept(B, trio)  # B agreed to a trio
    decline(A, trio)
    assert repo.get_group(trio)["responses"] == {}
    assert me(B)["proposal"]["my_response"] is None and me(B)["proposal"]["accepted"] == 0


def test_a_trio_whose_remaining_pair_cannot_stay_together_is_dissolved(repo, trio):
    repo.apply_decline_delta(C, {"set_min_group_size": 3})  # C only takes full cabs
    code, body = decline(A, trio)
    assert (code, body["state"]) == (200, "DISSOLVED")
    assert repo.get_group(trio)["state"] == "DISSOLVED"
    assert set(repo.get_request(s)["status"] for s in (A, B, C)) == {"PENDING"}


def test_a_blocked_pair_cannot_stay_together(repo, trio):
    repo.add_block(B, C, "PERSON")
    assert decline(A, trio)[1]["state"] == "DISSOLVED"


def test_a_pair_that_lost_a_member_to_plans_changing_is_still_asked(repo, trio):
    assert decline(A, trio, "PLANS_CHANGED")[1]["state"] == "REDUCED"
    assert repo.get_request(A) is None
    assert sorted(repo.get_group(trio)["members"]) == [B, C]


# ---- staying together, or splitting -------------------------------------------

def test_both_staying_confirms_the_pair_at_the_locked_time(repo, trio):
    decline(A, trio)
    assert accept(B, trio)[1] == {"state": "FORMED", "accepted": 1, "of": 2}
    assert accept(C, trio)[1]["state"] == "CONFIRMED"
    g = repo.get_group(trio)
    assert (g["state"], sorted(g["members"]), g["departure_time"]) == ("CONFIRMED", [B, C], 1035)
    assert repo.get_request(B)["status"] == repo.get_request(C)["status"] == "CONFIRMED"


def test_a_confirmed_pair_leaving_too_soon_does_not_look_for_a_third(repo, trio):
    decline(A, trio); accept(B, trio); accept(C, trio)
    assert repo.get_group(trio)["open_seat"] is False  # 225 minutes out, and the bar is 360
    assert me(B)["proposal"]["open_seat"] is False


def test_a_confirmed_pair_leaving_late_enough_keeps_a_seat_open(repo, trio, monkeypatch):
    monkeypatch.setenv("EXODUS_TRAVEL_DATE", TOMORROW)
    decline(A, trio); accept(B, trio); accept(C, trio)
    assert repo.get_group(trio)["open_seat"] is True
    assert me(B)["proposal"]["open_seat"] is True


def test_either_splitting_dissolves_it_and_costs_the_splitter_like_any_decline(repo, trio):
    decline(A, trio)
    accept(B, trio)
    code, body = decline(C, trio, "TIME")  # C would rather split
    assert (code, body["state"]) == (200, "DISSOLVED")
    assert set(repo.get_request(s)["status"] for s in (A, B, C)) == {"PENDING"}
    assert repo.get_request(C)["decline_count"] == 1  # a split is a real decline: no free way out

    out = me(B)["last_outcome"]  # B had said yes, and is told who backed out
    assert (out["cause"], out["who"]["student_id"]) == ("declined", C)
    assert me(C)["last_outcome"]["cause"] == "you_declined"


def test_someone_who_has_agreed_to_stay_cannot_then_split(repo, trio):
    decline(A, trio)
    accept(B, trio)
    assert decline(B, trio)[0] == 409  # accepting locks you in


def test_a_late_answer_from_the_pair_is_refused(repo, trio, monkeypatch):
    decline(A, trio)
    monkeypatch.setattr(respond.time, "time", lambda: NOW + 10 + WINDOW + 1)
    assert accept(B, trio)[0] == 409


def test_the_leaver_is_not_told_about_a_cab_they_left_when_it_later_falls_apart(repo, trio):
    decline(A, trio)
    decline(B, trio)  # the remaining pair splits
    assert repo.get_group(trio)["state"] == "DISSOLVED"
    assert me(A)["last_outcome"] is None  # A left before that, and it is none of their business
    assert me(C)["last_outcome"]["who"]["student_id"] == B


# ---- silence -------------------------------------------------------------------

def test_one_silent_member_of_a_trio_is_treated_like_a_decline(repo, trio):
    accept(A, trio); accept(B, trio)  # C says nothing
    deadline = NOW + WINDOW
    out = lifecycle_sweep.sweep(repo, deadline + 1)
    assert out["reduced"] == [{"group_id": trio, "timed_out": [C]}] and out["dissolved"] == []
    g = repo.get_group(trio)
    assert (g["state"], sorted(g["members"]), g["responses"]) == ("FORMED", [A, B], {})  # A and B are asked again
    assert repo.get_request(C)["decline_count"] == 1 and repo.get_request(C)["status"] == "PENDING"
    assert repo.get_request(A)["decline_count"] == 0  # the ones who answered are not penalised


def test_two_silent_members_of_a_trio_leave_one_person_so_it_dissolves(repo, trio):
    accept(A, trio)
    out = lifecycle_sweep.sweep(repo, NOW + WINDOW + 1)
    assert out["dissolved"] == [{"group_id": trio, "timed_out": [B, C]}] and out["reduced"] == []


def test_a_pair_that_never_answers_is_dissolved(repo, trio):
    decline(A, trio)
    out = lifecycle_sweep.sweep(repo, NOW + 10 + WINDOW + 1)
    assert out["dissolved"] == [{"group_id": trio, "timed_out": [B, C]}]


# ---- the third seat -----------------------------------------------------------

@pytest.fixture
def open_pair(repo, trio, monkeypatch):
    """B and C confirmed as a pair at 5:15 pm, with the seat open (the trip is tomorrow)."""
    monkeypatch.setenv("EXODUS_TRAVEL_DATE", TOMORROW)
    decline(A, trio); accept(B, trio); accept(C, trio)
    assert repo.get_group(trio)["open_seat"] is True
    return trio


def test_the_seat_goes_to_the_pending_student_it_fits_best(repo, open_pair):
    add(repo, D, 1050)             # 15 minutes from the pair's time, in a 30-minute window: penalty 0.5
    add(repo, E, 1040, 30, 30)     # 5 minutes off: penalty ~0.17
    out = release(repo, NOW + 300)
    assert [(s["student_id"], s["group_id"]) for s in out["seats"]] == [(E, open_pair)]
    assert repo.get_request(E)["status"] == "GROUPED" and repo.get_request(E)["current_group_id"] == open_pair
    assert repo.get_request(D)["status"] != "GROUPED" or repo.group_for(D)["group_id"] != open_pair


def test_the_offer_shows_the_seat_as_a_cab_of_three_at_the_locked_time(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    p = me(E)["proposal"]
    assert (p["state"], p["invited"], p["size"], p["departure_time"]) == ("FORMED", True, 3, 1035)
    assert p["accept_deadline"] == NOW + 300 + WINDOW
    assert [o["student_id"] for o in p["others"]] == [B, C]
    assert all(o["name"] for o in p["others"])
    assert "2 others" in p["explanation"] and "3 ways" in p["explanation"]
    # the pair are told a seat is being offered, but not to whom
    q = me(B)["proposal"]
    assert (q["seat_offered"], q["invited"], q["size"]) == (True, False, 2)
    assert [o["student_id"] for o in q["others"]] == [C]


@pytest.mark.parametrize("why, fields", [
    ("the pair's time is outside their window", dict(p=1200, b=30, a=30)),
    ("it would spend more than 90% of their flexibility", dict(p=1035 + 28, b=30, a=30)),
])
def test_students_who_do_not_fit_are_not_offered_the_seat(repo, open_pair, why, fields):
    add(repo, D, **fields)
    assert release(repo, NOW + 300)["seats"] == [], why


def test_a_student_blocked_from_one_of_the_pair_is_not_offered_the_seat(repo, open_pair):
    add(repo, D, 1040)
    repo.add_block(D, B, "PERSON")  # the platform derives blocked_with from the Blocks table
    assert release(repo, NOW + 300)["seats"] == []


def test_the_student_who_left_this_cab_is_not_offered_its_seat(repo, open_pair):
    # A declined the trio; if their window would fit, they still must not be handed the seat back
    repo.apply_decline_delta(A, {"update_request": {"b": 60, "a": 60}})
    assert release(repo, NOW + 300)["seats"] == []


def test_a_student_who_sits_the_release_out_is_not_offered_a_seat(repo, open_pair):
    add(repo, D, 1040, decline_count=CONFIG["decline_budget"])
    assert release(repo, NOW + 300)["seats"] == []


def test_a_seat_that_is_not_open_is_never_offered(repo, trio):
    decline(A, trio); accept(B, trio); accept(C, trio)  # today: leaves too soon, so no open seat
    add(repo, D, 1040)
    assert repo.get_group(trio)["open_seat"] is False
    assert release(repo, NOW + 300)["seats"] == []


@pytest.mark.parametrize("seconds_later, offered", [(60, True), (3600, False)])
def test_a_late_release_is_too_close_to_departure_to_add_someone(repo, open_pair, monkeypatch, seconds_later, offered):
    monkeypatch.setenv("EXODUS_TRAVEL_DATE", "2027-01-15")  # the pair leaves 225 minutes after NOW
    add(repo, D, 1040)
    # 60s on: 224 minutes to go, fine. An hour on: 165, inside the 180-minute cutoff.
    assert bool(release(repo, NOW + seconds_later)["seats"]) is offered


def test_only_one_offer_is_out_at_a_time(repo, open_pair):
    add(repo, D, 1040); add(repo, E, 1045)
    assert len(release(repo, NOW + 300)["seats"]) == 1
    assert release(repo, NOW + 600)["seats"] == []  # the seat is spoken for until that offer ends
    assert len(repo.groups_with_offers()) == 1


def test_accepting_the_seat_completes_the_cab_without_moving_the_time(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    code, body = accept(E, open_pair)
    assert (code, body["state"]) == (200, "CONFIRMED")
    g = repo.get_group(open_pair)
    assert (g["state"], sorted(g["members"]), g["departure_time"]) == ("CONFIRMED", [B, C, E], 1035)
    assert g["open_seat"] is False and not g.get("seat_offer")
    assert repo.get_request(E)["status"] == "CONFIRMED"
    # the pair see who joined
    p = me(B)["proposal"]
    assert p["size"] == 3 and [o["student_id"] for o in p["others"]] == [C, E]
    assert p["seat_offered"] is False and p["open_seat"] is False


def test_declining_the_seat_leaves_the_pair_alone(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    code, body = decline(E, open_pair, "TIME")
    assert (code, body["state"], body["reason"]) == (200, "OFFER_DECLINED", "TIME")
    assert body["next_release_at"] is not None
    g = repo.get_group(open_pair)
    assert (g["state"], sorted(g["members"]), g["open_seat"]) == ("CONFIRMED", [B, C], True)
    assert not g.get("seat_offer") and E in g["excluded"]
    r = repo.get_request(E)
    assert (r["status"], r["decline_count"]) == ("PENDING", 1)  # a real decline: it counts

    add(repo, F, 1045)
    out = release(repo, NOW + 600)
    assert [s["student_id"] for s in out["seats"]] == [F]  # the seat goes to someone else, never back to E


def test_declining_the_seat_by_naming_someone_blocks_that_pair_member(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    for bad in (E, D, "", None):  # yourself, a stranger, nobody: only a member of the pair may be named
        assert decline(E, open_pair, "PERSON", named_student_id=bad)[0] == 400
    assert repo.get_group(open_pair)["seat_offer"]["student_id"] == E  # the offer is still standing
    assert decline(E, open_pair, "PERSON", named_student_id=B)[0] == 200
    assert B in repo.get_request(E)["blocked_with"]


def test_an_unanswered_offer_expires_and_costs_a_timeout(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    assert lifecycle_sweep.sweep(repo, NOW + 300 + WINDOW - 1)["offers_expired"] == []
    out = lifecycle_sweep.sweep(repo, NOW + 300 + WINDOW + 1)
    assert out["offers_expired"] == [{"group_id": open_pair, "student_id": E}] and out["errors"] == []
    g = repo.get_group(open_pair)
    assert not g.get("seat_offer") and g["state"] == "CONFIRMED" and sorted(g["members"]) == [B, C]
    assert (repo.get_request(E)["status"], repo.get_request(E)["decline_count"]) == ("PENDING", 1)
    assert lifecycle_sweep.sweep(repo, NOW + 300 + WINDOW + 2)["offers_expired"] == []  # idempotent


def test_answering_an_expired_offer_is_refused(repo, open_pair, monkeypatch):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    monkeypatch.setattr(respond.time, "time", lambda: NOW + 300 + WINDOW + 1)
    assert accept(E, open_pair)[0] == 409


def test_only_the_student_offered_the_seat_may_answer_for_it(repo, open_pair):
    add(repo, E, 1040)
    release(repo, NOW + 300)
    assert accept(D, open_pair)[0] == 403       # a stranger: Cedar
    assert accept(B, open_pair)[0] == 409       # a member of the pair: the group is already confirmed


def test_the_release_row_says_how_many_seats_were_offered(repo, open_pair):
    add(repo, E, 1040); add(repo, F, 300, 15, 15)  # F fits nothing, so the pool still has something to solve
    out = release(repo, NOW + 300)
    assert len(out["seats"]) == 1
    assert repo.latest_release("COLLEGE_AIRPORT")["seats_offered"] == 1
