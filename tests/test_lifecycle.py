import copy
import random

import pytest

from config import CONFIG, REASONS
from lifecycle import on_decline, should_sit_out

DELTA_KEYS = {"block_pair", "update_request", "set_min_group_size", "withdraw", "increment_decline_count", "add_anchor"}
FACTS = {"departure_time": 1030, "group_size": 2}  # what the platform always adds


def req(**overrides):
    return {"student_id": "s1", "route": "COLLEGE_AIRPORT", "p": 1040, "b": 30, "a": 30,
            "min_group_size": 2, "blocked_with": [], "declined_anchors": [], "decline_count": 0, **overrides}


# ---- PERSON ----------------------------------------------------------------

def test_person_blocks_the_named_member_and_costs_no_budget():
    assert on_decline(req(), "PERSON", {"named_student_id": "s3", **FACTS}) == {"block_pair": ["s1", "s3"]}


@pytest.mark.parametrize("payload", [
    {},                                                    # nobody named
    {"named_student_id": "s1"},                            # themselves
    {"named_student_id": "s9"},                            # already blocked
])
def test_a_person_decline_that_blocks_nothing_new_costs_budget(payload):
    assert on_decline(req(blocked_with=["s9"]), "PERSON", {**payload, **FACTS}) == {"increment_decline_count": 1}


# ---- TIME ------------------------------------------------------------------

def test_widening_updates_the_window_costs_no_budget_and_anchors_the_declined_time():
    assert on_decline(req(), "TIME", {"b": 60, **FACTS}) == {
        "update_request": {"b": 60, "a": 30}, "add_anchor": {"T": 1030, "size": 2}}


def test_a_window_never_shrinks():
    # a narrower `a` is ignored; only the genuine widening of `b` lands
    assert on_decline(req(), "TIME", {"b": 60, "a": 10, **FACTS})["update_request"] == {"b": 60, "a": 30}


@pytest.mark.parametrize("payload", [{}, {"b": 30, "a": 30}, {"b": 10, "a": 5}])
def test_a_time_decline_that_widens_nothing_costs_budget(payload):
    assert on_decline(req(), "TIME", {**payload, **FACTS}) == {
        "increment_decline_count": 1, "add_anchor": {"T": 1030, "size": 2}}


def test_no_anchor_without_the_platform_facts():
    assert on_decline(req(), "TIME", {}) == {"increment_decline_count": 1}


# ---- TOO_FEW ---------------------------------------------------------------

def test_too_few_raises_the_minimum_to_a_full_cab():
    assert on_decline(req(), "TOO_FEW", FACTS) == {"set_min_group_size": CONFIG["max_group"]}


def test_too_few_from_someone_who_already_only_takes_full_cabs_costs_budget():
    # The loop this closes: the UI offers "too few" on a triple too. Re-setting
    # an unchanged minimum used to count as a change, so the student never ran
    # out of budget and could decline full cab after full cab forever.
    r = req(min_group_size=CONFIG["max_group"])
    assert on_decline(r, "TOO_FEW", {**FACTS, "group_size": 3}) == {"increment_decline_count": 1}


# ---- PLANS_CHANGED, TIMEOUT, bad input -------------------------------------

def test_plans_changed_withdraws():
    assert on_decline(req(), "PLANS_CHANGED", FACTS) == {"withdraw": True}


def test_timeout_only_costs_budget_whatever_the_payload():
    assert on_decline(req(), "TIMEOUT", {}) == {"increment_decline_count": 1}
    assert on_decline(req(), "TIMEOUT", {"b": 90, "named_student_id": "s3", **FACTS}) == {"increment_decline_count": 1}


def test_unknown_reason_raises():
    with pytest.raises(ValueError):
        on_decline(req(), "BORED", {})


def test_a_missing_payload_is_an_empty_one():
    assert on_decline(req(), "TIMEOUT", None) == {"increment_decline_count": 1}


# ---- properties the platform relies on --------------------------------------

def _random_case(rng):
    r = req(b=rng.choice((5, 30, 240)), a=rng.choice((5, 30, 240)),
            min_group_size=rng.choice((2, 3)), blocked_with=rng.sample(["s2", "s3", "s4"], rng.randint(0, 2)))
    payload = {}
    if rng.random() < 0.6:
        payload["named_student_id"] = rng.choice(["s1", "s2", "s3", "s4"])
    for k in ("b", "a"):
        if rng.random() < 0.6:
            payload[k] = rng.choice((5, 30, 60, 240))
    if rng.random() < 0.8:
        payload.update(departure_time=5 * rng.randint(180, 240), group_size=rng.choice((2, 3)))
    return r, rng.choice(REASONS), payload


def _apply(request, delta):
    """What the platform's dispatcher does with a delta, minus storage."""
    r = copy.deepcopy(request)
    if "block_pair" in delta:
        r["blocked_with"] = r["blocked_with"] + [delta["block_pair"][1]]
    if "update_request" in delta:
        r.update(delta["update_request"])
    if "set_min_group_size" in delta:
        r["min_group_size"] = delta["set_min_group_size"]
    if "withdraw" in delta:
        return None
    if "increment_decline_count" in delta:
        r["decline_count"] += delta["increment_decline_count"]
    if "add_anchor" in delta:
        r["declined_anchors"] = r["declined_anchors"] + [delta["add_anchor"]]
    return r


def test_every_decline_either_really_changes_the_request_or_costs_budget():
    """The termination argument (section 7.5), checked on random inputs."""
    rng = random.Random(0)
    for _ in range(3000):
        r, reason, payload = _random_case(rng)
        delta = on_decline(r, reason, payload)
        after = _apply(r, delta)
        if "increment_decline_count" in delta:
            continue
        # no budget spent, so the solver's view of the request must have changed
        solver_view = ("b", "a", "min_group_size", "blocked_with")
        assert after is None or any(after[k] != r[k] for k in solver_view), (reason, payload, delta)


def test_deltas_only_carry_keys_the_dispatcher_can_act_on():
    # The dispatcher acts on key presence: {"withdraw": False} would delete the request.
    rng = random.Random(1)
    for _ in range(3000):
        r, reason, payload = _random_case(rng)
        delta = on_decline(r, reason, payload)
        assert set(delta) <= DELTA_KEYS
        assert all(v not in (None, False, [], {}) for v in delta.values()), delta
        if "increment_decline_count" in delta:
            # an int to add (DynamoDB can't add a boolean), and always exactly one
            assert delta["increment_decline_count"] == 1 and type(delta["increment_decline_count"]) is int


def test_on_decline_does_not_mutate_its_inputs():
    rng = random.Random(2)
    for _ in range(500):
        r, reason, payload = _random_case(rng)
        r0, p0 = copy.deepcopy(r), copy.deepcopy(payload)
        on_decline(r, reason, payload)
        assert (r, payload) == (r0, p0)


# ---- should_sit_out ---------------------------------------------------------

@pytest.mark.parametrize("count, sits_out", [(0, False), (1, False), (2, True), (3, True)])
def test_the_decline_budget(count, sits_out):
    assert should_sit_out(req(decline_count=count), CONFIG) is sits_out


def test_a_request_with_no_counter_yet_has_its_whole_budget():
    r = req()
    del r["decline_count"]
    assert should_sit_out(r, CONFIG) is False


def test_the_budget_comes_from_config():
    assert should_sit_out(req(decline_count=2), {**CONFIG, "decline_budget": 3}) is False
