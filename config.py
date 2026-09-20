"""
Single shared config for Exodus. Both the solver side and the platform side
import this — nobody hardcodes a tuning constant anywhere else.
"""

CONFIG = {
    "sigma": 0.8,               # per-person cost of a group of 2
    "upsilon": 2.5,             # per-person cost of being ungrouped
    "beta": 1.0,                # fairness weight on max penalty
    "p_cap": 0.9,               # no member's penalty may exceed this
    "grid_minutes": 5,          # departure time grid resolution
    "max_group": 3,             # cab capacity
    "accept_window_minutes": 20,  # time to accept/decline a proposal
    "decline_budget": 2,        # no-state-change declines before sit-out
    "min_release_gap_seconds": 10,  # a route can't be released twice within this window

    # A confirmed pair may still gain a third rider (schedule.py). The departure
    # time never changes; only the seat is offered, at a later release.
    "backfill_open_lead_minutes": 360,   # a pair stays open only if it leaves at least this long after the next release
    "backfill_close_lead_minutes": 180,  # and a release may offer the seat only at least this long before departure

    # Releases happen in wall-clock time, departures are minutes on the travel
    # day (contracts.md), and nothing else links the two. These do.
    "tz_offset_minutes": 330,       # the college's clock: IST is UTC+5:30
    "travel_date": None,            # "YYYY-MM-DD" of the trip; None means the day of the release. EXODUS_TRAVEL_DATE overrides.
}

EMAIL_DOMAIN = "iiitb.ac.in"

# Roll numbers: imt2022001 (iMTech), mt2023045 (MTech), ms..., phd... The domain
# rule lives in Cedar; this is the shape of the name in front of the @, which a
# `like` pattern can't express.
STUDENT_ID_RE = r"^(imt|mt|ms|phd)\d{7}$"

ROUTES = [
    "COLLEGE_AIRPORT",
    "COLLEGE_STATION",
    "AIRPORT_COLLEGE",
    "STATION_COLLEGE",
]

STATUSES = ["PENDING", "GROUPED", "CONFIRMED", "SAT_OUT"]

STATES = ["FORMED", "CONFIRMED", "DISSOLVED"]

REASONS = ["PERSON", "TIME", "TOO_FEW", "PLANS_CHANGED", "TIMEOUT"]
