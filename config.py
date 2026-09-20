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
